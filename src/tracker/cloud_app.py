import os
import asyncio
import logging
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from pathlib import Path

from .main import run_once
from .gcs_sync import upload_db, download_db

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("tracker.cloud")

app = FastAPI(title="Naver Price Tracker Cloud")

# Configuration from ENV
CONFIG_PATH = os.getenv("CONFIG_PATH", "targets.yaml")
DB_PATH = os.getenv("DB_PATH", "data/price_tracker.sqlite3")
ARTIFACTS_DIR = os.getenv("ARTIFACTS_DIR", "data/artifacts")
GCS_BUCKET = os.getenv("GCS_BUCKET") # GCS 버킷 이름 (필수)
INTERVAL = int(os.getenv("COLLECT_INTERVAL", "3600"))

@app.on_event("startup")
async def startup_event():
    """앱 시작 시 GCS에서 DB 다운로드 및 추적 루프 시작"""
    logger.info("Cloud App starting up...")
    
    # 1. GCS에서 최신 DB 다운로드
    if GCS_BUCKET:
        download_db(GCS_BUCKET, DB_PATH)
    
    # 2. 백그라운드 추적 루프 시작
    asyncio.create_task(tracker_loop())

async def update_tracker_data():
    """수집 수행 및 UI/GCS 갱신 로직"""
    try:
        logger.info("Starting collection and sync...")
        ok, fail = await run_once(CONFIG_PATH, DB_PATH, ARTIFACTS_DIR)
        logger.info(f"Collection finished: ok={ok}, fail={fail}")
        
        # 수집 후 UI 데이터 갱신 (export-ui 로직 내장)
        from .db import ObservationStore
        from .util import dump_json
        import re
        
        store = ObservationStore(DB_PATH)
        data = store.get_dashboard_data()
        store.close()
        
        # dashboard.html에 데이터 주입
        html_path = Path("dashboard.html")
        if html_path.exists():
            html_content = html_path.read_text(encoding="utf-8")
            injected_script = f'<script id="data-injection">window.injectedData = {dump_json(data)};</script>'
            new_content = re.sub(r'<script id="data-injection">.*?</script>', injected_script, html_content, flags=re.DOTALL)
            html_path.write_text(new_content, encoding="utf-8")
        
        # 3. GCS로 결과 업로드
        if GCS_BUCKET:
            upload_db(GCS_BUCKET, DB_PATH)
        return ok, fail
    except Exception as e:
        logger.error(f"Update error: {e}")
        raise e

async def tracker_loop():
    """배경에서 주기적으로 수집 수행"""
    while True:
        try:
            await update_tracker_data()
        except Exception as e:
            logger.error(f"Tracker loop execution error: {e}")
            
        await asyncio.sleep(INTERVAL)

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    """대시보드 보기"""
    html_path = Path("dashboard.html")
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "Dashboard not found. Run collection first."

@app.post("/collect")
async def manual_collect():
    """수동 수집 트리거"""
    ok, fail = await update_tracker_data()
    return {"status": "ok", "ok": ok, "fail": fail}

@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
