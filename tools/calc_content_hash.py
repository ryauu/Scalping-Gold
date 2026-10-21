import hashlib
import sys
import duckdb
import pandas as pd

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

con = duckdb.connect()

print("=== TÍNH MÃ BĂM NỘI DUNG (CANONICAL CONTENT SHA-256) ===")

print("\n1. CLEAN M1 (chuẩn hóa: time_utc,time_server,open,high,low,close,tick_volume,spread sắp xếp theo time_utc):")
years = con.execute("SELECT DISTINCT year FROM 'data/clean/GOLD_M1_utc/**/*.parquet' ORDER BY year").fetchall()

m1_content_hashes = {}
for (yr,) in years:
    h = hashlib.sha256()
    # Lấy dữ liệu theo thứ tự time_utc
    cursor = con.execute(f"""
        SELECT 
            strftime(time_utc, '%Y-%m-%d %H:%M:%S'),
            strftime(time_server, '%Y-%m-%d %H:%M:%S'),
            printf('%.2f', open),
            printf('%.2f', high),
            printf('%.2f', low),
            printf('%.2f', close),
            tick_volume,
            spread
        FROM 'data/clean/GOLD_M1_utc/year={yr}/*.parquet'
        ORDER BY time_utc
    """)
    while True:
        rows = cursor.fetchmany(10000)
        if not rows:
            break
        chunk = "".join(f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]},{r[6]},{r[7]}\n" for r in rows)
        h.update(chunk.encode("utf-8"))
    sha = h.hexdigest()
    m1_content_hashes[yr] = sha
    print(f"Year {yr}: {sha}")

print("\n2. DERIVED H1 (chuẩn hóa: time_utc,open,high,low,close,tick_volume,spread,n_m1 sắp xếp theo time_utc):")
h1_content_hashes = {}
for (yr,) in years:
    h = hashlib.sha256()
    cursor = con.execute(f"""
        SELECT 
            strftime(time_utc, '%Y-%m-%d %H:%M:%S'),
            printf('%.2f', open),
            printf('%.2f', high),
            printf('%.2f', low),
            printf('%.2f', close),
            tick_volume,
            printf('%.1f', spread),
            n_m1
        FROM 'data/derived/GOLD_H1_utc/year={yr}/*.parquet'
        ORDER BY time_utc
    """)
    rows = cursor.fetchall()
    chunk = "".join(f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]},{r[6]},{r[7]}\n" for r in rows)
    h.update(chunk.encode("utf-8"))
    sha = h.hexdigest()
    h1_content_hashes[yr] = sha
    print(f"Year {yr}: {sha}")
