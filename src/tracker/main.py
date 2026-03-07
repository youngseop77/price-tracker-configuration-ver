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
from .naver_api import NaverShoppingSearchClient, collect_lowest_offer_via_api
from .notifier import send_price_alert
from .util import calc_change_metrics, dump_json, utc_now_iso

logger = logging.getLogger("naver_price_tracker")


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


async def _collect_one(target: TargetConfig, app_config, artifacts_dir: str) -> dict:
    """단일 타겟 수집 및 NO_MATCH 시 자동 폴백 로직"""
    result = None
    
    if target.mode == "api_query":
        client = NaverShoppingSearchClient(timeout_seconds=app_config.timeout_seconds)
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
    for target in app_config.targets:
        logger.info("수집 시작 | %s | mode=%s", target.name, target.mode)
        try:
            result = await _collect_one(target, app_config, artifacts_dir)
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

            # 3. 필수 필드 기본값 보장 (의미 오염 방지 및 DB 정합성)
            result.setdefault("fallback_used", 0)
            result.setdefault("config_mode", target.mode)
            result.setdefault("source_mode", target.mode)
            result["alert_triggered"] = result.get("alert_triggered", 0)

            # 4. 가격 하락 알림 체크 (하락폭 기준)
            alert_active = check_and_alert(result, prev_price, app_config.alert_threshold_percent)
            result["alert_triggered"] = 1 if alert_active else 0

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
                logger.warning("수집 미일치 | %s | %s", target.name, result.get("status"))

        except Exception as exc:  # noqa: BLE001
            fail += 1
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

    return ok, fail


async def run_daemon(config_path: str, db_path: str, artifacts_dir: str, interval_seconds: int) -> None:
    while True:
        # time.monotonic() 기반 간격 계산
        start_time = time.monotonic()
        ok, fail = await run_once(config_path, db_path, artifacts_dir)
        logger.info("1회차 완료 | ok=%s fail=%s", ok, fail)
        
        elapsed = time.monotonic() - start_time
        # 드리프트 방지를 위해 float 대기 시간 계산 (int truncation 제거)
        sleep_for = max(0.0, interval_seconds - elapsed)
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
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(verbose=args.verbose)
    load_dotenv(override=False)

    try:
        if args.command == "once":
            ok, fail = asyncio.run(run_once(args.config, args.db, args.artifacts_dir))
            # 부분 실패(fail > 0)하더라도, 적어도 하나라도 성공했으면(ok > 0) 전체 파이프라인(대시보드 생성 등)을 이어가도록 0 반환
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
            
            # JSON 저장 (dashboard.html이 fetch로 읽음)
            json_path = Path("./dashboard_data.json").resolve()
            json_path.write_text(dump_json(data), encoding="utf-8")
            
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
