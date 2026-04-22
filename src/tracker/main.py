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
from .browser_scraper import (
    BrowserScrapeError,
    collect_lowest_offer_via_browser,
    collect_current_offer_via_browser
)
from .config import StoreType, TargetConfig, load_config
from .gsheet_store import GoogleSheetStore
from .naver_api import (
    NaverShoppingSearchClient,
    collect_lowest_offer_via_api,
    collect_mall_inventory,
    collect_mall_items,
    _normalized_item,
)
from .notifier import send_price_alert
from .report import send_daily_report
from .util import (
    all_keywords_present, any_keyword_present, calc_change_metrics, clean_text, dump_json, utc_now_iso, 
    is_night_time_kst, normalize_for_match
)

def _normalize_seller_name(name: str | None) -> str:
    """판매처 이름을 비교하기 좋게 소문자화 및 공백 제거합니다."""
    if not name: return ""
    return name.lower().replace(" ", "")

def _is_authorized_seller(seller_name: str | None, authorized_sellers: list[str]) -> bool:
    """해당 판매처가 공식(Authorized) 업체인지 확인합니다."""
    if not seller_name or not authorized_sellers:
        return False
    sn = _normalize_seller_name(seller_name)
    for auth in authorized_sellers:
        if _normalize_seller_name(auth) == sn:
            return True
    return False

def _extract_image_id(path: str) -> str:
    """이미지 URL에서 확장자를 제외한 파일명(ID)을 추출합니다."""
    # https://.../main_12345/12345.jpg -> 12345
    filename = path.split("/")[-1]
    return filename.split(".")[0]

logger = logging.getLogger("naver_price_tracker")


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


async def _collect_one(client: NaverShoppingSearchClient, target: TargetConfig, app_config, artifacts_dir: str, broad_items: list[dict] | None = None) -> tuple[dict, list[dict]]:
    """단일 타겟 수집 및 NO_MATCH 시 자동 폴백 로직 (확장 수집 지원)"""
    result = None
    items = []
    
    if target.mode == "api_query":
        try:
            result, items = collect_lowest_offer_via_api(client, app_config, target, broad_items=broad_items)
        except Exception as e:
            raise e

        if result.get("status") == "NO_MATCH" and target.fallback_url:
            logger.info("API NO_MATCH -> Browser 폴백 실행 | %s", target.name)
            fallback_target = TargetConfig(
                name=target.name,
                mode="browser_url",
                url=target.fallback_url,
                browser=target.browser,
                match=target.match,
            )
            fallback_result, fallback_items = await collect_lowest_offer_via_browser(fallback_target, artifacts_dir)
            fallback_result["fallback_used"] = 1
            fallback_result["status"] = "OK"
            return fallback_result, fallback_items
            
        return result, items

    elif target.mode == "browser_url":
        result, items = await collect_lowest_offer_via_browser(target, artifacts_dir)
        return result, items

    else:
        raise ValueError(f"지원하지 않는 수집 모드: {target.mode}")


async def run_once(app_config, artifacts_dir: str, gsheet_id: str, summary_json: str | None = None) -> None:
    ok = 0
    fail = 0
    fallback_used_count = 0
    alerts_triggered_count = 0
    changed_items = []
    collected_payloads = []
    
    # 최저가 수집 데이터를 재활용하기 위한 전체 상품 저장소
    all_peeked_items = []
    
    # [추가] 판매자별 상품 ID 필터 사전 정규화 (매칭 효율성)
    from .util import normalize_for_match
    norm_seller_filters = {normalize_for_match(k): set(str(v_id) for v_id in v_list) 
                           for k, v_list in (app_config.seller_filters or {}).items()}

    service_account_json = os.getenv("GCP_SA_KEY") or os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
    if not service_account_json:
        logger.error("구글 서비스 계정 키 환경변수(GCP_SA_KEY 또는 GOOGLE_SERVICE_ACCOUNT_KEY)가 설정되지 않았습니다.")
        return

    store = GoogleSheetStore(gsheet_id, service_account_json)
    client = NaverShoppingSearchClient(timeout_seconds=app_config.timeout_seconds)

    # ---------- [셀러 설정 동기화 및 활성 목록 필터링] ----------
    try:
        store.sync_seller_config(app_config)
        active_sellers = store.get_active_sellers()
        
        if active_sellers is not None:
            logger.info(f"시트 기반 활성 셀러 필터링 적용 (공식: {len(active_sellers['authorized'])}, 모니터링: {len(active_sellers['monitored'])})")
            app_config.authorized_sellers = active_sellers["authorized"]
            app_config.monitored_sellers = active_sellers["monitored"]
            
            # mall_targets도 활성 셀러 기준으로 필터링
            from .util import normalize_for_match
            all_active_names = set(active_sellers["authorized"] + active_sellers["monitored"])
            active_norm = {normalize_for_match(n) for n in all_active_names}
            
            if app_config.mall_targets:
                filtered_mall_targets = []
                for mt in app_config.mall_targets:
                    if normalize_for_match(mt.mall_name) in active_norm:
                        filtered_mall_targets.append(mt)
                    else:
                        logger.debug(f"비활성 셀러 관련 타겟 제외: {mt.name} ({mt.mall_name})")
                app_config.mall_targets = filtered_mall_targets
    except Exception as e:
        logger.error(f"셀러 동기화 중 오류 발생 (YAML 설정으로 계속 진행): {e}")

    # ---------- [1.5단계 셀러+상품명 명시적 호출 수집 (선행 루크업)] ----------
    # 메인 타겟 매칭 전에 각 셀러 전용 상품을 먼저 수집하여 후보군(Pool)을 확보합니다.
    global_mall_pool = [] 
    if app_config.mall_targets:
        logger.info(f"선행 셀러+상품명 명시적 검색 시작 (총 {len(app_config.mall_targets)}개 타겟)")
        tracked_sellers_norm = {normalize_for_match(s) for s in 
            (app_config.monitored_sellers or []) + (app_config.authorized_sellers or [])}
            
        unique_mall_queries = {}
        for mt in app_config.mall_targets:
            q = f"{mt.mall_name} {mt.query}"
            if q not in unique_mall_queries:
                unique_mall_queries[q] = mt
                
        for q, mt in unique_mall_queries.items():
            try:
                mall_spec_items = collect_mall_items(client, app_config, q, pages=1)
                for itm in mall_spec_items:
                    seller_norm = normalize_for_match(itm.get("seller_name", ""))
                    if seller_norm in tracked_sellers_norm:
                        itm["category"] = mt.category
                        itm["#source"] = "mall_direct" # 추적용 태그
                        global_mall_pool.append(itm)
                logger.debug(f"  └─ [{q}] 선행 수집 완료")
            except Exception as e:
                logger.warning(f"  └─ [{q}] 선행 수집 실패: {e}")

    # ---------- [2단계 확장 수집을 위한 대표 키워드 사전 수집] ----------
    broad_search_cache = {}
    unique_rank_queries = set()
    for t in app_config.targets:
        if t.rank_queries:
            # 첫 번째 대표 키워드를 '판매처 복구용' 브로드 쿼리로 사용
            unique_rank_queries.add(t.rank_queries[0])
    
    if unique_rank_queries:
        logger.info(f"확장 수집을 위한 대표 키워드 검색 시작 ({len(unique_rank_queries)}개 키워드)")
        for rq in unique_rank_queries:
            try:
                # 랭킹 수집용 함수를 사용하여 100건(기본) 수집
                broad_items = collect_mall_items(client, app_config, rq, pages=1)
                broad_search_cache[rq] = broad_items
                logger.debug(f"  └─ [{rq}] 캐싱 완료 ({len(broad_items)}건)")
            except Exception as e:
                logger.warning(f"  └─ [{rq}] 캐싱 실패: {e}")

    for target in app_config.targets:
        logger.info("수집 시작 | %s | mode=%s", target.name, target.mode)
        try:
            prev_success = store.get_latest_success(target.name)
            prev_price = prev_success["price"] if prev_success else None

            # [개선] 캐시된 브로드 아이템 + 선행 수집된 셀러 상품 전달
            target_rq = target.rank_queries[0] if target.rank_queries else None
            target_broad_items = broad_search_cache.get(target_rq) or []
            
            # 해당 카테고리에 맞는 선행 수집 셀러 상품들도 후보군에 병합
            category_mall_items = [itm for itm in global_mall_pool if itm.get("category") == target.category]
            combined_broad_items = list(target_broad_items) + category_mall_items
            
            result, items = await _collect_one(client, target, app_config, artifacts_dir, broad_items=combined_broad_items)
            
            # 수집된 모든 상품 중 유용한 것만 선별하여 저장 (데이터 폭발 방지)
            if items:
                # 필터링 기준: monitored + authorized 셀러 목록
                tracked_sellers_norm = {normalize_for_match(s) for s in 
                    (app_config.monitored_sellers or []) + (app_config.authorized_sellers or [])}
                
                for itm in items:
                    # itm은 이제 naver_api.py에서 반환된 정규화된 데이터 리스트임
                    seller_norm = normalize_for_match(itm.get("seller_name", ""))
                    p_id = str(itm.get("product_id") or "")
                    
                    # [추가] 판매자별 상품 ID 필터링 (화이트리스트)
                    if seller_norm in norm_seller_filters:
                        if p_id not in norm_seller_filters[seller_norm]:
                            # 허용되지 않은 상품 ID인 경우 제외
                            continue

                    rank = itm.get("search_rank") or 999
                    
                    # 선별 조건:
                    # 1. 이번 회차 최저가로 선정된 상품
                    # 2. 검색 순위가 30위 이내인 인기 상품
                    # 3. 우리가 직접 모니터링 대상으로 지정한 셀러의 상품
                    is_best = (result.get("success") and str(itm.get("product_id")) == str(result.get("product_id")))
                    is_top_rank = rank <= 30
                    is_tracked_seller = seller_norm in tracked_sellers_norm
                    
                    if is_best or is_top_rank or is_tracked_seller:
                        itm["category"] = target.category
                        itm["product_code"] = target.name
                        itm["rank_query"] = target.rank_queries[0] # 랭킹 매칭용 태그 추가
                        all_peeked_items.append(itm)
                
            result["collected_at"] = utc_now_iso()
            result["config_mode"] = target.mode
            # 최종 선정된 상품의 product_code 역시 타겟명으로 대체
            result["product_code"] = target.name

            current_price = result.get("price")
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

            result.setdefault("fallback_used", 0)
            result["alert_triggered"] = 0
            
            if result.get("success"):
                ok += 1
                status = result.get("price_change_status")
                if status in ["PRICE_DOWN", "PRICE_UP"]:
                    result["alert_triggered"] = 1
                    alerts_triggered_count += 1
                    changed_items.append(result)
                
                if result.get("fallback_used"):
                    fallback_used_count += 1
                
                logger.info("수집 완료 | %s | %s", target.name, result.get("price_change_status"))
                collected_payloads.append(result)
            else:
                fail += 1
                logger.warning("수집 미일치 | %s | %s", target.name, result.get("status"))
                collected_payloads.append(result)

        except Exception as exc:
            fail += 1
            logger.exception("수집 실패 | %s", target.name)
            collected_payloads.append({
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

    # ---------- [도용 감시 로직 추가] ----------
    # 1. 이번 회차 수집된 모든 상품 중 공식 판매처가 사용한 이미지 ID들 추출
    official_image_ids = set()
    for itm in all_peeked_items:
        if _is_authorized_seller(itm.get("seller_name"), app_config.authorized_sellers):
            img_id = _extract_image_id(itm.get("image_url"))
            if img_id:
                official_image_ids.add(img_id)
    
    # 2. 모든 수집 상품에 대해 도용 여부 판단
    for itm in all_peeked_items:
        img_id = _extract_image_id(itm.get("image_url"))
        if img_id in official_image_ids:
            if not _is_authorized_seller(itm.get("seller_name"), app_config.authorized_sellers):
                itm["is_unauthorized"] = 1
                itm["product_code"] = itm.get("product_code") or "IMAGE_MISUSE_DETECTED"
            else:
                itm["is_unauthorized"] = 0
        else:
            itm["is_unauthorized"] = 0

    # 3. 최저가 수집 결과(collected_payloads)들도 동일하게 도용 여부 플래그 업데이트
    for payload in collected_payloads:
        if payload.get("success"):
            img_id = _extract_image_id(payload.get("image_url"))
            if img_id in official_image_ids:
                if not _is_authorized_seller(payload.get("seller_name"), app_config.authorized_sellers):
                    payload["is_unauthorized"] = 1
                    payload["product_code"] = payload.get("product_code") or "IMAGE_MISUSE_DETECTED"
                else:
                    payload["is_unauthorized"] = 0
            else:
                payload["is_unauthorized"] = 0

    # 4. [사용자 요청] 카탈로그 판매처('네이버')를 실판매처로 매칭 (수집 시점 확정)
    matched_mall_records = []
    # 신뢰할 수 있는 대형몰 (가격 오차 허용 대상)
    TRUSTED_SELLERS = ["하이마트", "11번가", "옥션", "G마켓", "SSG.COM", "Gmarket", "인터파크", "Lotte ON", "롯데 ON"]
    from .util import normalize_for_match
    trusted_sellers_norm = {normalize_for_match(s) for s in TRUSTED_SELLERS}

    for payload in collected_payloads:
        if payload.get("success") and payload.get("seller_name") in ["네이버", "Naver", None]:
            price = payload.get("price")
            p_id = str(payload.get("product_id") or "")
            t_name = payload.get("target_name", "")
            if not price: continue
            
            found_mall = None
            found_item = None
            int_price = int(price)
            target_obj = next((t for t in app_config.targets if t.name == t_name), None)
            
            # 1순위: 같은 product_id + 같은 가격인 실제 판매처 (정확한 매칭)
            if p_id:
                for itm in all_peeked_items:
                    try:
                        if str(itm.get("product_id") or "") != p_id:
                            continue
                        m_price = int(itm.get("price") or 0)
                        if m_price == int_price and m_price > 0:
                            m_seller = itm.get("seller_name")
                            if m_seller and m_seller not in ["네이버", "Naver"]:
                                found_mall = m_seller
                                found_item = itm
                                break
                    except (ValueError, TypeError):
                        continue
            
            # 2순위: 1순위 실패 시, 키워드 및 제외 키워드 기반 정밀 매칭
            if not found_mall and target_obj:
                required_kws = target_obj.match.required_keywords or []
                exclude_kws = target_obj.match.exclude_keywords or []
                
                for itm in all_peeked_items:
                    try:
                        itm_title = itm.get("title") or ""
                        m_price = int(itm.get("price") or 0)
                        
                        if m_price != int_price or m_price <= 0:
                            continue
                            
                        # 필수 키워드가 모두 포함되어 있는지 확인
                        if not all_keywords_present(itm_title, required_kws):
                            continue
                            
                        # 배제 키워드가 하나라도 포함되어 있는지 확인
                        if exclude_kws and any_keyword_present(itm_title, exclude_kws):
                            continue
                            
                        # 모든 조건 만족 시 매칭
                        m_seller = itm.get("seller_name")
                        if m_seller and m_seller not in ["네이버", "Naver"]:
                            found_mall = m_seller
                            found_item = itm
                            break
                    except (ValueError, TypeError):
                        continue

            # 3순위 [추가]: 대형몰(Trusted) 대상 1.2% 가격 오차 허용 유연 매칭
            if not found_mall and target_obj:
                required_kws = target_obj.match.required_keywords or []
                exclude_kws = target_obj.match.exclude_keywords or []
                
                for itm in all_peeked_items:
                    try:
                        m_seller = itm.get("seller_name")
                        if not m_seller or normalize_for_match(m_seller) not in trusted_sellers_norm:
                            continue
                            
                        m_price = int(itm.get("price") or 0)
                        if m_price <= 0: continue
                        
                        # 오차 범위 계산 (1.2%)
                        diff_ratio = abs(m_price - int_price) / int_price
                        if diff_ratio > 0.012:
                            continue
                        
                        itm_title = itm.get("title") or ""
                        # 필수 키워드/배제 키워드 검증은 동일하게 수행
                        if not all_keywords_present(itm_title, required_kws):
                            continue
                        if exclude_kws and any_keyword_present(itm_title, exclude_kws):
                            continue
                            
                        found_mall = m_seller
                        found_item = itm
                        logger.info(f"  [FLEXIBLE MATCH] {t_name} -> {found_mall} (Price Diff: {int_price} vs {m_price})")
                        break
                    except (ValueError, TypeError):
                        continue
            
            if found_mall:
                logger.info(f"  [MARKET MATCH] {t_name} -> {found_mall} (Price: {price})")
                payload["seller_name"] = found_mall
                # 자동 매칭된 셀러 정보를 쇼핑몰 리포트(mall_observations)에도 남기기 위해 저장
                if target_obj and found_item:
                    m_item = found_item.copy()
                    m_item["category"] = target_obj.category # 타겟 카테고리 강제 할당 (분류 문제 해결)
                    m_item["collected_at"] = utc_now_iso()
                    matched_mall_records.append({
                        "mall_name": found_mall,
                        "category": target_obj.category,
                        "item": m_item
                    })


    # 루프 종료 후 한 번에 저장 (Batch Insert)
    if collected_payloads:
        try:
            store.insert_batch(collected_payloads)
        except Exception as e:
            logger.error(f"GSheet 배치 저장 최종 실패: {e}")

    # ---------- [랭킹 TOP 10 수집 및 저장 추가] ----------
    # 각 상품 타겟 별로 정의된 rank_queries 기반으로 상위 10개 상품 저장
    unique_rank_queries = set()
    for t in app_config.targets:
        if t.rank_queries:
            unique_rank_queries.update(t.rank_queries)
            
    if unique_rank_queries:
        # [추가] 하루 1회 전송 제한 (이미 오늘 수집된 데이터가 있으면 건너뜀)
        if hasattr(store, "exists_ranking_today") and store.exists_ranking_today():
            from datetime import datetime, timedelta, timezone
            kst = timezone(timedelta(hours=9))
            today_kst = datetime.now(kst).strftime("%Y-%m-%d")
            logger.info(f"📊 오늘은 이미 랭킹 데이터가 수집되었습니다 (KST {today_kst}). 저장을 건너뜁니다.")
        else:
            rank_batch = []
            now_ts = utc_now_iso()
            
            for rq in unique_rank_queries:
                logger.info(f"📊 랭킹 데이터 정리 중: {rq}")
                try:
                    # [최적화] 이미 앞에서 수집한 캐시 데이터가 있다면 재활용 (API 호출 절감)
                    if rq in broad_search_cache:
                        raw_items = broad_search_cache[rq]
                    else:
                        raw_items = collect_mall_items(client, app_config, rq, pages=1)
                    
                    top_10 = raw_items[:10]
                    
                    for idx, item in enumerate(top_10, 1):
                        rank_batch.append({
                            "query": rq,
                            "rank": idx,
                            "title": item.get("title"),
                            "price": item.get("price"),
                            "seller_name": item.get("seller_name"),
                            "product_id": item.get("product_id"),
                            "product_type": item.get("product_type"),
                            "product_url": item.get("product_url"),
                            "image_url": item.get("image_url"),
                            "is_ad": item.get("is_ad", False),
                            "collected_at": now_ts
                        })
                except Exception as e:
                    logger.error(f"키워드 '{rq}' 랭킹 수집 중 오류: {e}")
            
            if rank_batch:
                logger.info(f"📊 {len(rank_batch)}건의 랭킹 히스토리 데이터를 수집했습니다.")
                try:
                    store.insert_ranking_batch(rank_batch)
                except Exception as e:
                    logger.error(f"랭킹 히스토리 저장 실패: {e}")

    # [삭제됨] 이전 2.5단계 명시적 호출 수집 (앞으로 이동됨)

    # ---------- [셀러 트래킹: 최저가 데이터 재활용] ----------
    # 1. 자동 매칭된(Market Match) 셀러 기록 먼저 배치에 추가
    batch_payloads = []
    global_seen = set()

    if matched_mall_records:
        for rec in matched_mall_records:
            m_name = rec["mall_name"]
            cat = rec["category"]
            itm = rec["item"]
            p_id = str(itm.get("product_id") or itm.get("product_url", ""))
            
            dup_key = f"{normalize_for_match(m_name)}|{p_id}"
            if dup_key not in global_seen:
                batch_payloads.append({
                    "target_name": f"[AUTO] {itm.get('product_code', 'Matched')}",
                    "query": "MARKET_MATCH",
                    "mall_name": m_name,
                    "category": cat,
                    "items": [itm]
                })
                global_seen.add(dup_key)

    # 2. 명시적으로 정의된 mall_targets 및 암시적 셀러(나머지 모니터링 셀러) 동시 처리
    from .util import normalize_for_match
    
    # 추적 대상 셀러 전체 수집 (시트 정의 또는 YAML 설정)
    active_malls = set()
    if 'active_sellers' in locals() and active_sellers:
        active_malls.update(active_sellers["authorized"])
        active_malls.update(active_sellers["monitored"])
    else:
        active_malls.update(app_config.authorized_sellers or [])
        active_malls.update(app_config.monitored_sellers or [])

    # 명시적 mall_targets에 정의된 패턴 파악
    target_mall_patterns = {}
    if app_config.mall_targets:
        for m_target in app_config.mall_targets:
            n = normalize_for_match(m_target.mall_name)
            if n not in target_mall_patterns:
                target_mall_patterns[n] = []
            target_mall_patterns[n].append(m_target)

    logger.info(f"최저가 수집 데이터를 활용한 셀러 필터링 시작 (수집 상품 분석: {len(all_peeked_items)}개 + 선행 {len(global_mall_pool)}개)")
    search_pool = all_peeked_items + global_mall_pool

    # 2.1. 명시적 mall_targets 우선 처리
    if app_config.mall_targets:
        for m_target in app_config.mall_targets:
            target_mall_norm = normalize_for_match(m_target.mall_name)
            candidates = []
            
            for itm in search_pool:
                curr_itm_mall_norm = normalize_for_match(itm.get("seller_name", ""))
                
                if target_mall_norm in curr_itm_mall_norm and itm.get("category") == m_target.category:
                    title = str(itm.get("title") or "")
                    exclude_kws = getattr(m_target, "exclude_keywords", [])
                    if exclude_kws and any_keyword_present(title, exclude_kws):
                        continue
                        
                    p_id = str(itm.get("product_id") or itm.get("product_url", ""))
                    dup_key = f"{target_mall_norm}|{p_id}"
                    
                    if dup_key not in global_seen:
                        itm["collected_at"] = utc_now_iso()
                        candidates.append(itm)
                        global_seen.add(dup_key)
            
            if candidates:
                batch_payloads.append({
                    "target_name": m_target.name,
                    "query": m_target.query,
                    "mall_name": m_target.mall_name,
                    "category": m_target.category,
                    "items": candidates
                })
                logger.info(f"  └─ [{m_target.name}] 필터링 완료: {len(candidates)}개 상품 저장 대기")

    # 2.2. 암시적 셀러 트래킹 처리 (하이마트, 11번가 등 명시적 mall target이 없는 셀러들 자동 분류)
    for mall_name in active_malls:
        target_mall_norm = normalize_for_match(mall_name)
        if target_mall_norm in target_mall_patterns:
            continue # 이미 명시적으로 처리된 셀러는 제외
            
        candidates_by_cat = {}
        for itm in search_pool:
            curr_itm_mall_norm = normalize_for_match(itm.get("seller_name", ""))
            
            if target_mall_norm in curr_itm_mall_norm:
                cat = itm.get("category")
                if not cat: continue
                
                p_id = str(itm.get("product_id") or itm.get("product_url", ""))
                dup_key = f"{target_mall_norm}|{p_id}"
                
                if dup_key not in global_seen:
                    itm["collected_at"] = utc_now_iso()
                    
                    if cat not in candidates_by_cat:
                        candidates_by_cat[cat] = []
                    candidates_by_cat[cat].append(itm)
                    global_seen.add(dup_key)
                    
        for cat, candidates in candidates_by_cat.items():
            if candidates:
                batch_payloads.append({
                    "target_name": f"{mall_name} {cat} 자동수집",
                    "query": f"{mall_name} {cat}",
                    "mall_name": mall_name,
                    "category": cat,
                    "items": candidates
                })
                logger.info(f"  └─ [AUTO {mall_name}] {cat} 필터링 완료: {len(candidates)}개 상품 저장 대기")

    if batch_payloads:
        try:
            store.insert_mall_records_batch(batch_payloads)
        except Exception as e:
            logger.error(f"쇼핑몰 리포트 저장 중 오류: {e}")

    store.close()

    # 이메일 알림 (KST 기준 야간 시간대 21:00~08:00 제외)
    if changed_items:
        if not is_night_time_kst():
            send_price_alert(
                changed_items,
                app_config.email.email_from,
                app_config.email.email_password,
                app_config.email.email_to
            )
        else:
            logger.info("야간 시간(21:00-08:00 KST)이므로 이메일 알림을 건너뜁니다.")

    logger.info("최종 결과 | OK: %d, FAIL: %d, Fallback: %d, Alert: %d", ok, fail, fallback_used_count, alerts_triggered_count)

    if summary_json:
        summary_data = {
            "ok": ok,
            "fail": fail,
            "fallback_used": fallback_used_count,
            "alerts": alerts_triggered_count,
            "collected_at": utc_now_iso()
        }
        Path(summary_json).write_text(dump_json(summary_data), encoding="utf-8")
        logger.info(f"수집 요약 저장 완료: {summary_json}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Naver Shopping Price Tracker")
    parser.add_argument("command", choices=["once", "monitor", "export-ui", "serve", "sync-from-gcs", "sync-to-gcs", "daily-report", "export-report", "export-mall-report"], help="실행할 커맨드")
    parser.add_argument("--config", default="targets.yaml", help="설정 파일 경로")
    parser.add_argument("--db", default="price_tracker.sqlite3", help="DB 파일 경로")
    parser.add_argument("--interval", type=int, default=3600, help="모니터링 주기 (초)")
    parser.add_argument("--summary-json", help="수집 결과 요약을 저장할 JSON 경로")
    parser.add_argument("--output", help="파일 저장 경로 (export-report 등에서 사용)")
    parser.add_argument("--verbose", action="store_true", help="상세 로그 출력")
    args = parser.parse_args()

    setup_logging(args.verbose)
    load_dotenv()

    try:
        app_config = load_config(args.config)
    except Exception as e:
        logger.error("설정 로드 실패: %s", e)
        return

    if args.command == "once":
        if not app_config.gsheet_id:
            logger.error("GSHEET_ID가 설정되지 않았습니다 (YAML 또는 환경변수).")
            return
        asyncio.run(run_once(app_config, "artifacts", app_config.gsheet_id, summary_json=args.summary_json))

    elif args.command == "monitor":
        if not app_config.gsheet_id:
            logger.error("GSHEET_ID가 설정되지 않았습니다.")
            return
        logger.info("%d초 간격으로 모니터링을 시작합니다...", args.interval)
        while True:
            try:
                asyncio.run(run_once(app_config, "artifacts", app_config.gsheet_id, summary_json=args.summary_json))
            except Exception as e:
                logger.exception("모니터링 루프 중 오류 발생")
            time.sleep(args.interval)

    elif args.command == "export-ui":
        service_account_json = os.getenv("GCP_SA_KEY") or os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
        if not app_config.gsheet_id or not service_account_json:
            logger.error("GSHEET_ID 또는 구글 서비스 계정 키 환경변수(GCP_SA_KEY/GOOGLE_SERVICE_ACCOUNT_KEY)가 설정되지 않았습니다.")
            return
            
        store = GoogleSheetStore(app_config.gsheet_id, service_account_json)
        try:
            dashboard_raw = store.get_dashboard_data(app_config.targets)
            
            # 고유 랭킹 키워드별 최신 데이터 수집
            rankings = {}
            unique_rank_queries = set()
            for t in app_config.targets:
                if t.rank_queries:
                    unique_rank_queries.update(t.rank_queries)
            
            for rq in unique_rank_queries:
                latest = store.get_latest_rankings(rq)
                if latest:
                    rankings[rq] = latest
            
            # 셀러별 쇼핑몰 리포트 데이터 수집 (seller_config 시트의 is_active 반영 + 카테고리 맵핑)
            active_sellers = store.get_active_sellers()
            
            # 카테고리별 셀러 맵 생성 (mall_targets 활용)
            cat_seller_map = {}
            if app_config.mall_targets:
                for mt in app_config.mall_targets:
                    cat = mt.category or "기타"
                    if cat not in cat_seller_map: cat_seller_map[cat] = []
                    cat_seller_map[cat].append(mt.mall_name)
            
            if active_sellers:
                # 시트 기반 활성 셀러만 필터링하여 맵 재구성 (정규화 매칭 적용)
                from .util import normalize_for_match
                # [개선] 정규화된 활성 셀러 집합 생성
                active_norm_map = {normalize_for_match(s): s for s in 
                                   (active_sellers.get("monitored", []) + active_sellers.get("authorized", []))}
                
                # 이미 카테고리에 할당된 셀러들 추적
                assigned_sellers_norm = set()
                filtered_cat_map = {}
                
                for cat, slist in cat_seller_map.items():
                    # YAML의 셀러명(s)이 정규화된 활성 셀러 맵에 존재하는지 확인
                    filtered_list = []
                    for s in slist:
                        ns = normalize_for_match(s)
                        if ns in active_norm_map:
                            filtered_list.append(active_norm_map[ns]) # 시트에 있는 실제 이름 사용
                            assigned_sellers_norm.add(ns)
                    
                    if filtered_list:
                        filtered_cat_map[cat] = filtered_list
                

                # 매핑되지 않은 나머지 활성 셀러들을 '워치'와 '버즈' 카테고리에 모두 배정
                remaining_sellers = []
                for ns, original_name in active_norm_map.items():
                    if ns not in assigned_sellers_norm:
                        remaining_sellers.append(original_name)
                        
                if remaining_sellers:
                    for target_cat in ["아이폰17"]:
                        if target_cat not in filtered_cat_map:
                            filtered_cat_map[target_cat] = []
                        filtered_cat_map[target_cat].extend(remaining_sellers)

                
                effective_sellers = filtered_cat_map if filtered_cat_map else active_norm_map.values()
                logger.info(f"seller_config 시트 기반 활성 셀러 필터링 적용 (정규화 매칭 완료)")
            else:
                # 시트 조회 실패 시 생성된 맵 그대로 사용
                effective_sellers = cat_seller_map
                logger.info("seller_config 시트 조회 실패로 YAML 기반 카테고리 맵을 사용합니다.")
            
            mall_raw = store.get_mall_report_data(monitored_sellers=effective_sellers)
            mall_reports = {"categories": mall_raw}
            
            # [카탈로그 매칭 안내] 카탈로그 판매처는 이제 수집 시점(run_once)에 확정되어 저장됩니다.

            data = {



                "products": dashboard_raw["products"],
                "rankings": rankings,
                "mall_reports": mall_reports,
                "gsheet_id": app_config.gsheet_id,
                "updated_at": dashboard_raw["generated_at"]
            }
            Path("dashboard_data.json").write_text(dump_json(data), encoding="utf-8")
            logger.info("UI 데이터 내보내기 완료: dashboard_data.json (Google Sheets 기반 + 매칭 로직 적용)")
        finally:
            store.close()


    elif args.command == "sync-from-gcs" or args.command == "sync-to-gcs":
        logger.warning("GCS 연동 기능은 이 브랜치에서 더 이상 사용되지 않습니다 (Google Sheets Only).")

    elif args.command == "serve":
        # 간단한 HTTP 서버 실행 (대시보드 확인용)
        import http.server
        import socketserver
        PORT = 8000
        Handler = http.server.SimpleHTTPRequestHandler
        with socketserver.TCPServer(("", PORT), Handler) as httpd:
            logger.info("http://localhost:%d 에서 대시보드 서비스를 시작합니다.", PORT)
            httpd.serve_forever()

    elif args.command == "daily-report":
        service_account_json = os.getenv("GCP_SA_KEY") or os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
        if not app_config.gsheet_id or not service_account_json:
            logger.error("GSHEET_ID 또는 구글 서비스 계정 키 환경변수(GCP_SA_KEY/GOOGLE_SERVICE_ACCOUNT_KEY)가 설정되지 않았습니다.")
            return
            
        store = GoogleSheetStore(app_config.gsheet_id, service_account_json)
        try:
            send_daily_report(
                store,
                app_config.email.email_from,
                app_config.email.email_password,
                app_config.email.email_to,
                app_config.targets
            )
        finally:
            store.close()

    elif args.command == "export-report":
        service_account_json = os.getenv("GCP_SA_KEY") or os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
        if not app_config.gsheet_id or not service_account_json:
            logger.error("필요한 설정(GSHEET_ID 또는 구글 서비스 계정 키)이 누락되었습니다.")
            return
            
        from .report import generate_daily_report_html
        store = GoogleSheetStore(app_config.gsheet_id, service_account_json)
        try:
            html = generate_daily_report_html(store, app_config.targets)
            output_path = args.output or "report.html"
            Path(output_path).write_text(html, encoding="utf-8")
            logger.info(f"데일리 리포트 생성 완료: {output_path}")
        finally:
            store.close()

    elif args.command == "export-mall-report":
        # 2. 저장소 결정 (GSheet vs SQLite)
        store = None
        if app_config.store_type == StoreType.GSHEET:
            credential_json = os.getenv("GCP_SA_KEY") or os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
            if not credential_json:
                logger.error("GSHEET 모드이나 GCP_SA_KEY (또는 GOOGLE_SERVICE_ACCOUNT_KEY) 환경변수가 설정되지 않았습니다.")
                sys.exit(1)
            store = GoogleSheetStore(app_config.gsheet_id, credential_json)
            logger.info(f"Google Sheets 저장소 사용 준비 완료 (ID: {app_config.gsheet_id})")
        else:
            db_path = os.getenv("DB_PATH", "data/tracker.db")
            store = ObservationStore(db_path)
            logger.info(f"SQLite 저장소 사용 준비 완료 (Path: {db_path})")
            
        from .report import generate_mall_report_html
        try:
            html = generate_mall_report_html(store)
            output_path = args.output or "mall_report.html"
            Path(output_path).write_text(html, encoding="utf-8")
            logger.info(f"쇼핑몰 추적 리포트 생성 완료: {output_path}")
        finally:
            store.close()


if __name__ == "__main__":
    main()
