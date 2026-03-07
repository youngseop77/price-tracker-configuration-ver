from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import AppConfig, TargetConfig
from .util import all_keywords_present, clean_text, parse_int


def any_keyword_present(text: str, keywords: Iterable[str]) -> bool:
    """텍스트 내에 키워드 중 하나라도 포함되어 있는지 확인 (util에서 제거되어 내부 구현)"""
    from .util import normalize_for_match
    haystack = normalize_for_match(text)
    return any(normalize_for_match(k) in haystack for k in keywords)

SHOP_API_URL = "https://openapi.naver.com/v1/search/shop.json"


class NaverShoppingSearchClient:
    def __init__(self, timeout_seconds: int = 20) -> None:
        self.client_id = os.getenv("NAVER_CLIENT_ID", "")
        self.client_secret = os.getenv("NAVER_CLIENT_SECRET", "")
        self.user_agent = os.getenv("USER_AGENT", "NaverPriceTracker/1.0")
        self.timeout_seconds = int(os.getenv("REQUEST_TIMEOUT", str(timeout_seconds)))
        self.session = requests.Session()
        retry = Retry(total=3, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def _headers(self) -> dict[str, str]:
        if not self.client_id or not self.client_secret:
            raise RuntimeError("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 가 설정되지 않았습니다.")
        return {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }

    def search(self, *, query: str, display: int = 100, start: int = 1, sort: str = "asc", filter_: str | None = None, exclude: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "query": query,
            "display": display,
            "start": start,
            "sort": sort,
        }
        if filter_:
            params["filter"] = filter_
        if exclude:
            params["exclude"] = exclude

        response = self.session.get(
            SHOP_API_URL,
            headers=self._headers(),
            params=params,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()



def _item_matches(target: TargetConfig, item: dict[str, Any]) -> bool:
    title = clean_text(item.get("title"))
    product_id = str(item.get("productId", "") or "").strip()
    target_id = str(target.match.product_id or "").strip()
    product_type = int(item.get("productType", 0) or 0)

    # 1. productId가 지정된 경우 ID가 일치하면 즉시 반환 (타입 체크만 병행)
    if target_id:
        if product_id == target_id:
            if target.match.allowed_product_types and product_type not in target.match.allowed_product_types:
                return False
            return True
        return False

    # 2. product_id가 없는 경우 기존 키워드 기반 매칭 유지
    if target.match.allowed_product_types and product_type not in target.match.allowed_product_types:
        return False
    if target.match.required_keywords and not all_keywords_present(title, target.match.required_keywords):
        return False
    if target.match.exclude_keywords and any_keyword_present(title, target.match.exclude_keywords):
        return False
    return True



def _normalized_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": clean_text(item.get("title")),
        "price": parse_int(item.get("lprice"), default=0),
        "seller_name": clean_text(item.get("mallName")),
        "product_id": str(item.get("productId", "") or "") or None,
        "product_type": int(item.get("productType", 0) or 0),
        "product_url": item.get("link"),
        "raw_payload": item,
    }



def collect_lowest_offer_via_api(client: NaverShoppingSearchClient, app_config: AppConfig, target: TargetConfig) -> dict[str, Any]:
    if not target.query:
        raise ValueError(f"target '{target.name}' 에 query 가 없습니다.")

    pages = max(1, target.request.pages)
    items: list[dict[str, Any]] = []

    for page_index in range(pages):
        start = page_index * app_config.display + 1
        payload = client.search(
            query=target.query,
            display=app_config.display,
            start=start,
            sort=target.request.sort,
            filter_=target.request.filter,
            exclude=app_config.exclude,
        )
        items.extend(payload.get("items", []) or [])

    candidates = [_normalized_item(item) for item in items if _item_matches(target, item)]
    candidates = [c for c in candidates if c["price"] > 0]

    if not candidates:
        return {
            "target_name": target.name,
            "source_mode": target.mode,
            "success": 0,
            "status": "NO_MATCH",
            "title": None,
            "price": None,
            "seller_name": None,
            "product_id": target.match.product_id,
            "product_type": None,
            "product_url": None,
            "raw_payload": {
                "query": target.query,
                "request": asdict(target.request),
                "match": asdict(target.match),
                "items_examined": len(items),
            },
            "error_message": "조건에 맞는 상품을 찾지 못했습니다.",
        }

    best = min(candidates, key=lambda x: (x["price"], x["seller_name"] or "zzzz"))
    return {
        "target_name": target.name,
        "source_mode": target.mode,
        "success": 1,
        "status": "OK",
        **best,
        "error_message": None,
    }


def collect_certified_rank(
    client: NaverShoppingSearchClient,
    app_config: AppConfig,
    target: TargetConfig,
) -> dict[str, Any] | None:
    """인증 거래처 순위 카운팅.
    
    API 결과 내에서 인증점(mallProductId)을 찾아 순위를 계산합니다.
    """
    if not target.certified_item_id or not target.query:
        return None

    cert_id = str(target.certified_item_id).strip()
    catalog_id = str(target.match.product_id or "").strip()

    try:
        # 1. 카탈로그 ID가 있으면 그것으로, 없으면 쿼리로 검색 (100개)
        search_query = catalog_id if catalog_id else target.query
        payload = client.search(
            query=search_query,
            display=100,
            exclude=app_config.exclude,
        )
        items = payload.get("items", []) or []
        
        # 2. 결과가 없으면 상품명으로 재시도
        if not items and catalog_id:
            payload = client.search(query=target.query, display=100, exclude=app_config.exclude)
            items = payload.get("items", []) or []

        valid_items = []
        for item in items:
            mid = str(item.get("mallProductId", "")).strip()
            p_id = str(item.get("productId", "")).strip()
            
            # 카탈로그 ID가 지정된 경우, 해당 카탈로그 아이템만 포함 (노이즈 제거)
            if catalog_id and p_id != catalog_id:
                continue
                
            price = parse_int(item.get("lprice"), default=0)
            if price > 0:
                valid_items.append({"id": mid, "price": price})

        if not valid_items:
            return None

        # 가격순 정렬
        valid_items.sort(key=lambda x: x["price"])
        
        # 중복 제거 (입점 몰 기준)
        seen = set()
        unique_items = []
        for v in valid_items:
            if v["id"] not in seen:
                unique_items.append(v)
                seen.add(v["id"])
        
        # 우리 거래처 찾기
        certified = next((v for v in unique_items if v["id"] == cert_id), None)
        if not certified:
            # 부분 일치 (긴 ID 중 일부만 입력된 경우 등)
            certified = next((v for v in unique_items if cert_id in v["id"] or v["id"] in cert_id), None)
            
        if not certified:
            return None

        cert_price = certified["price"]
        # 순위: 나보다 싸거나 같은 가격의 업체 수
        rank = sum(1 for v in unique_items if v["price"] <= cert_price)
        total = len(unique_items)

        return {"certified_price": cert_price, "rank": rank, "total": total}

    except Exception:
        return None

