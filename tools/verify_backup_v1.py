import sys
import duckdb
import pandas as pd
from pathlib import Path

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

print("=== 1. KIỂM TRA SỰ TỒN TẠI CỦA THƯ MỤC SAO LƯU V1 ===")
clean_v1_path = Path("data/clean_v1/GOLD_M1_utc")
derived_v1_path = Path("data/derived_v1/GOLD_H1_utc")
clean_v2_path = Path("data/clean/GOLD_M1_utc")
derived_v2_path = Path("data/derived/GOLD_H1_utc")

print(f"clean_v1 exists: {clean_v1_path.exists()} (is_dir: {clean_v1_path.is_dir()})")
print(f"derived_v1 exists: {derived_v1_path.exists()} (is_dir: {derived_v1_path.is_dir()})")

con = duckdb.connect()

# Đếm số dòng clean_v1 vs clean v2
n_clean_v1 = con.execute("SELECT count(*) FROM 'data/clean_v1/GOLD_M1_utc/**/*.parquet'").fetchone()[0]
n_clean_v2 = con.execute("SELECT count(*) FROM 'data/clean/GOLD_M1_utc/**/*.parquet'").fetchone()[0]
print(f"\nSố dòng clean_v1: {n_clean_v1:,}")
print(f"Số dòng clean v2:  {n_clean_v2:,}")

# Đếm số dòng derived_v1 vs derived v2
n_derived_v1 = con.execute("SELECT count(*) FROM 'data/derived_v1/GOLD_H1_utc/**/*.parquet'").fetchone()[0]
n_derived_v2 = con.execute("SELECT count(*) FROM 'data/derived/GOLD_H1_utc/**/*.parquet'").fetchone()[0]
print(f"Số dòng derived_v1: {n_derived_v1:,}")
print(f"Số dòng derived v2:  {n_derived_v2:,}")

print("\n=== 2. KIỂM TRA time_utc TRONG KHOẢNG DST (VÍ DỤ 2024-03-15) ===")
# Trong khoảng DST gap tháng 3/2024 (10/03/2024 -> 31/03/2024):
# Bản v1 (NY+7): EDT bắt đầu sớm hơn -> time_utc_v1 = time_server - 3h
# Bản v2 (Europe/Nicosia): EET vẫn là mùa đông -> time_utc_v2 = time_server - 2h
# Do đó: time_utc_v1 nhỏ hơn time_utc_v2 đúng 1 giờ (-1 hour)!
query_dst = """
SELECT 
    v1.time_server,
    v1.time_utc AS time_utc_v1,
    v2.time_utc AS time_utc_v2,
    date_diff('hour', v1.time_utc, v2.time_utc) AS diff_v2_minus_v1_hours,
    v1.open AS open_v1,
    v2.open AS open_v2,
    v1.close AS close_v1,
    v2.close AS close_v2
FROM 'data/clean_v1/GOLD_M1_utc/year=2024/*.parquet' v1
JOIN 'data/clean/GOLD_M1_utc/year=2024/*.parquet' v2
  ON v1.time_server = v2.time_server
WHERE v1.time_server >= '2024-03-15 10:00:00' AND v1.time_server < '2024-03-15 10:05:00'
ORDER BY v1.time_server
"""
res_dst = con.execute(query_dst).df()
print("Kết quả đối chiếu 5 nến M1 ngày 2024-03-15:")
print(res_dst)

print("\n=== 3. KIỂM TRA NGOÀI KHOẢNG DST (VÍ DỤ 2024-01-15 & 2024-06-15) ===")
query_winter = """
SELECT 
    date_diff('hour', v1.time_utc, v2.time_utc) AS diff_hours,
    count(*) AS count_bars
FROM 'data/clean_v1/GOLD_M1_utc/year=2024/*.parquet' v1
JOIN 'data/clean/GOLD_M1_utc/year=2024/*.parquet' v2
  ON v1.time_server = v2.time_server
WHERE v1.time_server >= '2024-01-15' AND v1.time_server < '2024-01-20'
GROUP BY 1
"""
print("Phân phối chênh lệch giờ tháng 1/2024 (mùa đông đồng bộ):")
print(con.execute(query_winter).df())

query_summer = """
SELECT 
    date_diff('hour', v1.time_utc, v2.time_utc) AS diff_hours,
    count(*) AS count_bars
FROM 'data/clean_v1/GOLD_M1_utc/year=2024/*.parquet' v1
JOIN 'data/clean/GOLD_M1_utc/year=2024/*.parquet' v2
  ON v1.time_server = v2.time_server
WHERE v1.time_server >= '2024-06-15' AND v1.time_server < '2024-06-20'
GROUP BY 1
"""
print("\nPhân phối chênh lệch giờ tháng 6/2024 (mùa hè đồng bộ):")
print(con.execute(query_summer).df())

print("\n=== 4. TOÀN BỘ PHÂN PHỐI CHÊNH LỆCH GIỜ v1 VS v2 TRÊN TOÀN BỘ NĂM 2024 ===")
query_year = """
SELECT 
    date_diff('hour', v1.time_utc, v2.time_utc) AS diff_v2_minus_v1_hours,
    count(*) AS count_bars
FROM 'data/clean_v1/GOLD_M1_utc/year=2024/*.parquet' v1
JOIN 'data/clean/GOLD_M1_utc/year=2024/*.parquet' v2
  ON v1.time_server = v2.time_server
GROUP BY 1
ORDER BY 1
"""
print(con.execute(query_year).df())

print("\n=== 5. KIỂM TRA DERIVED H1 v1 VS v2 ===")
# Trong H1: cùng so sánh ngày 2024-03-15
query_h1 = """
SELECT 
    v1.time_utc AS h1_time_utc_v1,
    v2.time_utc AS h1_time_utc_v2,
    v1.open AS open_v1,
    v2.open AS open_v2,
    v1.n_m1 AS n_m1_v1,
    v2.n_m1 AS n_m1_v2
FROM 'data/derived_v1/GOLD_H1_utc/year=2024/*.parquet' v1
JOIN 'data/derived/GOLD_H1_utc/year=2024/*.parquet' v2
  ON v1.time_utc + INTERVAL 1 HOUR = v2.time_utc
WHERE CAST(v2.time_utc AS VARCHAR) LIKE '2024-03-15 08:%'
"""
print(con.execute(query_h1).df())
