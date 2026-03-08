import asyncio
import os
from playwright.async_api import async_playwright

async def inspect_dom():
    url = "https://search.shopping.naver.com/catalog/53507707537"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 2400})
        print(f"Opening {url}...")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(5) # Wait for JS
        
        # Save HTML for manual inspection
        html = await page.content()
        with open("debug_page.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Saved debug_page.html")
        
        # List all classes starting with 'style_' or containing 'seller'
        classes = await page.evaluate("""() => {
            const all = document.querySelectorAll('*');
            const classSet = new Set();
            all.forEach(el => {
                if (el.className && typeof el.className === 'string') {
                    el.className.split(/\\s+/).forEach(c => classSet.add(c));
                }
            });
            return Array.from(classSet).filter(c => c.includes('seller') || c.includes('price') || c.includes('mall'));
        }""")
        print(f"Found {len(classes)} relevant classes:")
        for c in sorted(classes):
            print(f"  - {c}")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(inspect_dom())
