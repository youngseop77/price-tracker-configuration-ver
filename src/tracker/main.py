from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from .alert import check_and_alert
from .browser_scraper import BrowserScrapeError, collect_lowest_offer_via_browser
from .config import TargetConfig, load_config
from .db import ObservationStore
from .naver_api import (
    NaverShoppingSearchClient,
    collect_certified_rank,
    collect_lowest_offer_via_api,
)
from .notifier import send_price_alert
from .util import calc_change_metrics, dump_json, utc_now_iso

logger = logging.getLogger("naver_price_tracker")


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


async def _collect_one(client: NaverShoppingSearchClient, target: TargetConfig, app_config, artifacts_dir: str) -> dict:
    """단일 타겟 수집 및 NO_MATCH 시 자동 폴백 로직"""
    result = None
    
    if target.mode == "api_query":
        try:
            result = collect_lowest_offer_via_api(client, app_config, target)
        except Exception as e:
            # 401, 429, 네트워크 오류 등은 폴백하지 않고 예외 발생 (main 루프에서 처리)
            raise e

        # API 결과가 NO_MATCH이고 fallback_url이 있는 경우에만 브라우저 폴백
        if result.get("status") == "NO_MATCH" and target.fallback_url:
            logger.info("API NO_MATCH -> Browser 폴백 실행 | %s", target.name)
            fallback_target = TargetConfig(
                name=target.name,
                mode="browser_url",
                url=target.fallback_url,
                browser=target.browser,
                match=target.match,
            )
            fallback_result = await collect_lowest_offer_via_browser(fallback_target, artifacts_dir)
            # 폴백 정보 기록 루틴 (status 오염 금지)
            fallback_result["fallback_used"] = 1
            fallback_result["status"] = "OK"  # 폴백 성공 시에도 순수 OK 유지
            return fallback_result
            
        return result

    if target.mode == "browser_url":
        return await collect_lowest_offer_via_browser(target, artifacts_dir=artifacts_dir)
        
    raise ValueError(f"지원하지 않는 mode: {target.mode}")


async def run_once(config_path: str, db_path: str, artifacts_dir: str) -> tuple[int, int]:
    load_dotenv(override=False)
    email_from = os.getenv("EMAIL_FROM", "")
    email_password = os.getenv("EMAIL_APP_PASSWORD", "")
    email_to = os.getenv("EMAIL_TO", "")
    changed_items: list[dict] = []  # 가격 변동 항목 수집용
    # load_config 내부에서 validate_config를 호출하여 실패 시 ValueError 발생 (Fail-Fast)
    app_config = load_config(config_path)
    store = ObservationStore(db_path)

    ok = 0
    fail = 0
    failed_targets = []
    fallback_used_count = 0
    alerts_triggered_count = 0
    certified_calc_success = 0
    certified_null_count = 0
    
    client = NaverShoppingSearchClient(timeout_seconds=app_config.timeout_seconds)
    for target in app_config.targets:
        logger.info("수집 시작 | %s | mode=%s", target.name, target.mode)
        try:
            result = await _collect_one(client, target, app_config, artifacts_dir)
            result["collected_at"] = utc_now_iso()

            # 1. 직전 성공 기록 조회
            prev_success = store.get_latest_success(target.name)
            prev_price = prev_success["price"] if prev_success else None
            
            # 2. 가격 변동 및 상태 계산 (부호 기반 판정)
            current_price = result.get("price")
            result["config_mode"] = target.mode
            if current_price is not None:
                if prev_price is None:
                    result["price_change_status"] = "FIRST_SEEN"
                else:
                    delta, pct = calc_change_metrics(current_price, prev_price)
                    result["prev_price"] = prev_price
                    result["price_delta"] = delta
                    result["price_delta_pct"] = pct
                    
                    if delta is not None:
                        if delta < 0: result["price_change_status"] = "PRICE_DOWN"
                        elif delta > 0: result["price_change_status"] = "PRICE_UP"
                        else: result["price_change_status"] = "PRICE_SAME"
                    else:
                        result["price_change_status"] = "PRICE_SAME"
            else:
                result["price_change_status"] = None

            # 3. 인증 거래처 순위 수집 (추가 API 호출 필요)
            rank_data = collect_certified_rank(client, app_config, target)
            if not rank_data and target.url and target.certified_mall_names:
                # API로 못 찾았는데 URL과 인증점 정보가 있으면 브라우저로 한 번 더 시도 (특히 카탈로그 건)
                logger.info("인증점 API 결과 없음 -> Browser 스크래핑으로 보완 | %s", target.name)
                try:
                    # 임시 타겟을 만들어 브라우저 모드로 실행
                    temp_target = TargetConfig(
                        name=target.name,
                        mode="browser_url",
                        url=target.url,
                        browser=target.browser,
                        match=target.match,
                        certified_item_id=target.certified_item_id,
                        certified_mall_names=target.certified_mall_names
                    )
                    br_result = await collect_lowest_offer_via_browser(temp_target, artifacts_dir)
                    if br_result.get("certified_price"):
                        rank_data = {
                            "certified_price": br_result["certified_price"],
                            "rank": br_result["rank"],
                            "total": br_result["total"],
                            "certified_lowest_price": br_result["certified_lowest_price"],
                            "certified_between_non_auth_count": br_result["certified_between_non_auth_count"],
                            "certified_cheaper_non_auth_count": br_result["certified_cheaper_non_auth_count"]
                        }
                except Exception as e:
                    logger.warning("인증점 브라우저 수집 실패 | %s | %s", target.name, e)

            if rank_data:
                result["certified_price"] = rank_data["certified_price"]
                result["certified_rank"] = rank_data["rank"]
                result["certified_total_sellers"] = rank_data["total"]
                result["certified_lowest_price"] = rank_data.get("certified_lowest_price")
                result["certified_between_non_auth_count"] = rank_data.get("certified_between_non_auth_count")
                result["certified_cheaper_non_auth_count"] = rank_data.get("certified_cheaper_non_auth_count")

            # 4. 필수 필드 기본값 보장 (의미 오염 방지 및 DB 정합성)
            result.setdefault("fallback_used", 0)
            result.setdefault("config_mode", target.mode)
            result.setdefault("source_mode", target.mode)
            result["alert_triggered"] = result.get("alert_triggered", 0)

            # 4. 가격 하락 알림 체크 (하락폭 기준)
            alert_active = check_and_alert(result, prev_price, app_config.alert_threshold_percent)
            result["alert_triggered"] = 1 if alert_active else 0
            
            if result.get("fallback_used"):
                fallback_used_count += 1
            if result.get("alert_triggered"):
                alerts_triggered_count += 1
            if result.get("certified_price") is not None:
                certified_calc_success += 1
            else:
                certified_null_count += 1

            store.insert(result)
            if result.get("success"):
                ok += 1
                status = result.get("price_change_status")
                logger.info("수집 완료 | %s | %s", target.name, status)
                # 가격 변동 항목 수집 (이메일 알림용)
                if status in ("PRICE_DOWN", "PRICE_UP"):
                    changed_items.append(result)
            else:
                fail += 1
                failed_targets.append(target.name)
                logger.warning("수집 미일치 | %s | %s", target.name, result.get("status"))

        except Exception as exc:  # noqa: BLE001
            fail += 1
            failed_targets.append(target.name)
            logger.exception("수집 실패 | %s | %s", target.name, exc)
            store.insert({
                "target_name": target.name,
                "config_mode": target.mode,
                "source_mode": target.mode,
                "fallback_used": 0,
                "collected_at": utc_now_iso(),
                "success": 0,
                "status": type(exc).__name__,
                "title": None,
                "price": None,
                "seller_name": None,
                "product_url": target.url,
                "error_message": str(exc),
                "price_change_status": None,
                "prev_price": None,
                "alert_triggered": 0
            })

    store.close()

    # 이메일 알림 발송 (변동된 항목이 있을 때만)
    if changed_items:
        send_price_alert(changed_items, email_from, email_password, email_to)

    return ok, fail, {"failed_targets": failed_targets, "fallback_used_count": fallback_used_count, "alerts_triggered_count": alerts_triggered_count, "certified_calc_success": certified_calc_success, "certified_null_count": certified_null_count}


async def run_daemon(config_path: str, db_path: str, artifacts_dir: str, interval_seconds: int) -> None:
    error_count = 0
    while True:
        start_time = time.monotonic()
        try:
            ok, fail, _ = await run_once(config_path, db_path, artifacts_dir)
            logger.info("1회차 완료 | ok=%s fail=%s", ok, fail)
            error_count = 0  # 성공 시 초기화
        except Exception as e:
            error_count += 1
            logger.error("루프 실행 중 예외 발생: %s", e)
            
        elapsed = time.monotonic() - start_time
        
        # 에러 시 기하급수적 백오프 (최대 10회 = 10분 정도 연장)
        penalty = min(600, error_count * 60) if error_count > 0 else 0
        sleep_for = max(0.0, (interval_seconds + penalty) - elapsed)
        
        if error_count > 0:
            logger.warning("연속 에러 %d회. 다음 실행 대기 | %.2f초 (백오프 %d초 적용)", error_count, sleep_for, penalty)
        else:
            logger.info("다음 실행 대기 | %.2f초", sleep_for)
            
        await asyncio.sleep(sleep_for)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Naver Shopping lowest-price tracker")
    parser.add_argument("command", choices=["once", "daemon", "export-latest", "export-html", "export-ui", "sync-from-gcs", "sync-to-gcs"])
    parser.add_argument("--config", default="./targets.yaml", help="YAML config path")
    parser.add_argument("--db", default=os.getenv("DB_PATH", "./price_tracker.sqlite3"), help="SQLite DB path")
    parser.add_argument("--artifacts-dir", default="./artifacts", help="HTML/screenshot artifact directory")
    parser.add_argument("--interval", type=int, default=int(os.getenv("COLLECT_INTERVAL", "3600")), help="daemon mode polling interval seconds")
    parser.add_argument("--out", default="./latest.csv", help="CSV output path for export-latest")
    parser.add_argument("--html-out", default="./price_report.html", help="HTML output path for export-html")
    parser.add_argument("--limit", type=int, default=20, help="export-html record limit per target")
    parser.add_argument("--summary-json", type=str, default=None, help="JSON file path to write run summary")
    parser.add_argument("--strict-exit", action="store_true", help="Exit 1 if any target fails (for actions testing)")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(verbose=args.verbose)
    load_dotenv(override=False)

    try:
        if args.command == "once":
            ok, fail, summary_dict = asyncio.run(run_once(args.config, args.db, args.artifacts_dir))
            if args.summary_json:
                out = Path(args.summary_json).resolve()
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(dump_json({"ok": ok, "fail": fail, **summary_dict}), encoding="utf-8")
                logger.info(f"Summary JSON 저장됨: {args.summary_json}")
            
            if args.strict_exit and fail > 0:
                return 1

            # 부분 실패(fail > 0)하더라도, 적어도 하나라도 성공했으면(ok > 0) 전체 파이프라인을 이어가도록 0 반환
            return 0 if (ok > 0 or fail == 0) else 1

        if args.command == "daemon":
            asyncio.run(run_daemon(args.config, args.db, args.artifacts_dir, args.interval))
            return 0

        if args.command == "export-latest":
            store = ObservationStore(args.db)
            out_path = store.export_latest_csv(args.out)
            store.close()
            logger.info("CSV 생성 완료 | %s", out_path)
            return 0

        if args.command == "export-html":
            store = ObservationStore(args.db)
            out_path = store.export_html_report(args.html_out, limit=args.limit)
            store.close()
            logger.info("HTML 리포트 생성 완료 | %s", out_path)
            return 0
        if args.command == "export-ui":
            store = ObservationStore(args.db)
            data = store.get_dashboard_data()
            store.close()
            
            # JSON 저장 (dashboard_data.json이 즉시 덮어쓰기되어 Race 발생 방지용)
            json_path = Path("./dashboard_data.json").resolve()
            tmp_path = json_path.with_name(json_path.name + ".tmp")
            tmp_path.write_text(dump_json(data), encoding="utf-8")
            tmp_path.replace(json_path)
            
            logger.info("대시보드 업데이트 완료. dashboard_data.json 저장됨.")
            return 0

        if args.command == "sync-from-gcs":
            import os
            from .gcs_sync import download_db
            bucket = os.getenv("GCS_BUCKET")
            if not bucket:
                logger.error("GCS_BUCKET 환경변수가 설정되지 않았습니다.")
                return 1
            download_db(bucket, args.db)
            logger.info("GCS에서 DB 다운로드 완료")
            return 0

        if args.command == "sync-to-gcs":
            import os
            from .gcs_sync import upload_db
            bucket = os.getenv("GCS_BUCKET")
            if not bucket:
                logger.error("GCS_BUCKET 환경변수가 설정되지 않았습니다.")
                return 1
            upload_db(bucket, args.db)
            logger.info("GCS로 DB 업로드 완료")
            return 0

    except Exception as e:
        logger.error("실행 도중 오류 발생: %s", e)
        return 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
