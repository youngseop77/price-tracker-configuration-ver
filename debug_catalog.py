import requests
import re
import json

url = "https://search.shopping.naver.com/catalog/55668557960"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

resp = requests.get(url, headers=headers)
print(f"Status Code: {resp.status_code}")

# __NEXT_DATA__ 확인
match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', resp.text)
if match:
    data = json.loads(match.group(1))
    with open('catalog_debug.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print("Found __NEXT_DATA__. Saved to catalog_debug.json")
else:
    print("NOT found __NEXT_DATA__")
    with open('catalog_page.html', 'w', encoding='utf-8') as f:
        f.write(resp.text)
