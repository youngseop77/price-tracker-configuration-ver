import yaml
import json
import os
import sys
from tracker.naver_api import NaverShoppingSearchClient, collect_certified_rank

def main():
    client = NaverShoppingSearchClient()
    # 갤럭시 버즈3프로 실버 (Catalog: 53508451505)
    catalog_id = "53508451505"
    certified_id = "10497605595"
    
    class AppConfig:
        def __init__(self): self.exclude = ""
            
    class TargetMatch:
        def __init__(self): self.product_id = catalog_id
            
    class Target:
        def __init__(self):
            self.name = "Test"
            self.query = "갤럭시 버즈3프로 실버"
            self.mode = "api"
            self.match = TargetMatch()
            self.certified_item_id = certified_id
            
    print(f"\nCalling collect_certified_rank for {catalog_id} / {certified_id}...")
    result = collect_certified_rank(client, AppConfig(), Target())
    print(f"Result: {result}")

if __name__ == "__main__":
    main()
