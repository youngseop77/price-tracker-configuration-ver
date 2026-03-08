from tracker.naver_api import NaverShoppingSearchClient
import json
import os
from dotenv import load_dotenv
load_dotenv()

def debug_raw_response():
    client = NaverShoppingSearchClient()
    query = "53507707537" # Catalog ID for Buds 3 Silver
    print(f"Searching for query: {query}")
    res = client.search(query=query, display=10)
    print(f"Full response: {json.dumps(res, indent=2, ensure_ascii=False)}")

if __name__ == "__main__":
    debug_raw_response()
