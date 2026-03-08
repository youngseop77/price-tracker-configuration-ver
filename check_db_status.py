
import sqlite3
from datetime import datetime

def check_db():
    conn = sqlite3.connect('price_tracker.sqlite3')
    cursor = conn.cursor()
    
    print("--- 최근 20개 수집 데이터 ---")
    cursor.execute("""
        SELECT target_name, collected_at, success, status, price, error_message 
        FROM observations 
        ORDER BY collected_at DESC 
        LIMIT 20
    """)
    rows = cursor.fetchall()
    for row in rows:
        print(row)
    
    print("\n--- 타겟별 최신 수집 시각 ---")
    cursor.execute("""
        SELECT target_name, MAX(collected_at), success
        FROM observations
        GROUP BY target_name
    """)
    rows = cursor.fetchall()
    for row in rows:
        print(row)
        
    conn.close()

if __name__ == "__main__":
    check_db()
