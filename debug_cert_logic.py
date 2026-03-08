from tracker.naver_api import NaverShoppingSearchClient
import json
from dotenv import load_dotenv
load_dotenv()

def debug_certified_search(catalog_id, cert_id):
    client = NaverShoppingSearchClient()
    print(f"Searching for catalog_id: {catalog_id}...")
    res = client.search(query=catalog_id, display=100)
    items = res.get("items", [])
    
    print(f"Total items found: {len(items)}\n")
    for i, item in enumerate(items[:30]):
        mid = str(item.get("mallProductId", ""))
        mall_name = str(item.get("mallName", ""))
        pid = str(item.get("productId", ""))
        price = item.get("lprice", "0")
        
        print(f"[{i:02d}] Mall: {mall_name:<25} | MallPID: {mid:<15} | PID: {pid:<15} | Price: {price}")
        if mid == cert_id:
            print(f"     >>> FOUND EXACT MATCH! index: {i} <<<")

if __name__ == "__main__":
    # 갤럭시 버즈3 실버 (상품명으로 검색하여 카탈로그 매칭 확인)
    debug_certified_search("갤럭시 버즈3 실버", None) # 카탈로그 ID 53507707537 확인용
