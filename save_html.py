import requests

def main():
    catalog_id = "53508451505"
    url = f"https://search.shopping.naver.com/catalog/{catalog_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    print(f"Fetching {url}...")
    resp = requests.get(url, headers=headers)
    print(f"Status: {resp.status_code}")
    with open("catalog_debug.html", "w", encoding="utf-8") as f:
        f.write(resp.text)
    print("Saved to catalog_debug.html")

if __name__ == "__main__":
    main()
