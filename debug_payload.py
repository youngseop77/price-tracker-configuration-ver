import sqlite3
import json

conn = sqlite3.connect('price_tracker.sqlite3')
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT raw_payload FROM observations WHERE success=1 LIMIT 1").fetchone()
if row:
    payload = json.loads(row['raw_payload'])
    with open('debug_payload.json', 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print("Payload dumped to debug_payload.json")
else:
    print("No successful records found")
conn.close()
