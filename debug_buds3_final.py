import asyncio
import os
from dotenv import load_dotenv
from tracker.config import load_config
from tracker.naver_api import NaverShoppingSearchClient, collect_lowest_offer_via_api, collect_certified_rank
from tracker.browser_scraper import collect_lowest_offer_via_browser
import json

async def debug_one():
    load_dotenv()
    app_cfg = load_config("./targets.yaml")
    target = next(t for t in app_cfg.targets if "버즈3 실버" in t.name)
    client = NaverShoppingSearchClient()
    
    print(f"--- Debugging Target: {target.name} ---")
    
    # 1. API Collect
    result = collect_lowest_offer_via_api(client, app_cfg, target)
    print(f"API Main Result Price: {result.get('price')}")
    
    # 2. Certified API
    rank_data = collect_certified_rank(client, app_cfg, target)
    print(f"API Rank Data: {rank_data}")
    
    # 3. Browser Fallback (if API rank_data is None)
    if not rank_data and target.url:
        print(f"Falling back to browser for {target.url}...")
        br_result = await collect_lowest_offer_via_browser(target, "./artifacts")
        print(f"Browser Result Keys: {br_result.keys()}")
        if "certified_price" in br_result:
             print(f"FOUND Certified Price in Browser: {br_result['certified_price']}")
             print(f"Between Count: {br_result.get('certified_between_non_auth_count')}")
        else:
             print("STILL NULL in browser result.")
             # Debug offers found
             offers = br_result.get("raw_payload", {}).get("offers_found", [])
             print(f"Total offers found by browser: {len(offers)}")
             for o in offers[:10]:
                 print(f"  - Mall: {o.get('seller_name')} | Price: {o.get('price')}")

if __name__ == "__main__":
    asyncio.run(debug_one())
