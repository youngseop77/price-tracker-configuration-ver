from tracker.naver_api import NaverShoppingSearchClient
import os
from dotenv import load_dotenv
load_dotenv()

def test_strategy():
    client = NaverShoppingSearchClient()
    product_name = "갤럭시 버즈3 실버"
    catalog_id = "53507707537"
    cert_malls = ["포디아이", "엠디텍", "제이유 디지탈", "제이유디지탈"]
    
    print(f"Searching for: {product_name}")
    res = client.search(query=product_name, display=100)
    items = res.get("items", [])
    print(f"Total results: {len(items)}")
    
    matching_catalog = [i for i in items if str(i.get("productId")) == catalog_id]
    print(f"Items matching Catalog ID {catalog_id}: {len(matching_catalog)}")
    
    for i, item in enumerate(matching_catalog):
        m_name = item.get("mallName")
        m_pid = item.get("mallProductId")
        price = item.get("lprice")
        is_cert = any(m in m_name for m in cert_malls)
        cert_marker = "[CERTIFIED]" if is_cert else ""
        print(f"[{i:02d}] {m_name:<20} | Price: {price:<10} | {cert_marker}")

if __name__ == "__main__":
    test_strategy()
