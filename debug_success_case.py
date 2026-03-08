from tracker.naver_api import NaverShoppingSearchClient, collect_certified_rank
from tracker.config import AppConfig
import json
from dotenv import load_dotenv
load_dotenv()

class DummyTarget:
    def __init__(self, name, query, catalog_id, cert_id):
        self.name = name
        self.query = query
        self.mode = "api_query"
        self.match = type('obj', (object,), {'product_id': catalog_id})()
        self.certified_item_id = cert_id

def debug_existing_case():
    client = NaverShoppingSearchClient()
    cat_id = "55727496884"
    cert_id = "11989781594"
    
    print(f"Searching for Catalog ID: {cat_id}...")
    res = client.search(query=cat_id, display=100)
    items = res.get("items", [])
    print(f"Total items found: {len(items)}")
    for item in items:
        if str(item.get("mallProductId")) == cert_id:
            print(f"MATCH FOUND! Mall Name: {item.get('mallName')}")
            return
    print("Match not found in catalog search")

if __name__ == "__main__":
    debug_existing_case()
