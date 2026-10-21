import sys
import duckdb
import pandas as pd
import numpy as np

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

con = duckdb.connect()

print("=== PHÂN TÍCH 111 NẾN H1 THƯA TRONG GIỜ NGHỈ (2014-2015) ===")

# Tìm các nến H1 có n_m1 <= 20 trong 2014-2015
df_h1_sparse = con.execute("""
    SELECT 
        time_utc,
        year,
        date_part('month', time_utc) as month,
        date_part('hour', time_utc) as hour_utc,
        n_m1,
        open, high, low, close
    FROM 'data/derived/GOLD_H1_utc/year=201*/*.parquet'
    WHERE year IN (2014, 2015, 2016)
      AND n_m1 < 45
    ORDER BY time_utc
""").df()

print(f"Tổng số nến H1 n_m1 < 45 trong 2014-2016: {len(df_h1_sparse)}")
print("Phân bố theo n_m1:")
print(df_h1_sparse["n_m1"].value_counts().sort_index())

print("\nPhân bố theo hour_utc:")
print(df_h1_sparse.groupby(["year", "hour_utc"])["n_m1"].agg(["count", "min", "median", "max"]))

# Lấy các nến M1 thuộc về các giờ H1 thưa này
df_m1_sparse = con.execute("""
    WITH sparse_h1 AS (
        SELECT time_utc as h1_time, date_trunc('hour', time_utc) as h1_start, n_m1
        FROM 'data/derived/GOLD_H1_utc/year=201*/*.parquet'
        WHERE year IN (2014, 2015)
          AND n_m1 <= 15
    )
    SELECT 
        m.time_utc,
        m.time_server,
        date_part('minute', m.time_utc) as min_utc,
        date_part('minute', m.time_server) as min_server,
        date_part('hour', m.time_server) as hour_server,
        date_part('hour', m.time_utc) as hour_utc,
        s.n_m1,
        m.year
    FROM 'data/clean/GOLD_M1_utc/year=201*/*.parquet' m
    JOIN sparse_h1 s
      ON date_trunc('hour', m.time_utc) = s.h1_start
    ORDER BY m.time_utc
""").df()

print(f"\nTổng số nến M1 trong các nến H1 n_m1 <= 15: {len(df_m1_sparse)}")
print("Phân phối phút trong giờ (min_utc):")
print(df_m1_sparse["min_utc"].value_counts().sort_index())

print("\nPhân phối phút server (min_server):")
print(df_m1_sparse["min_server"].value_counts().sort_index())

# Tìm ngày bắt đầu và kết thúc của hiện tượng này
print("\nKhoảng thời gian xuất hiện hiện tượng:")
print("Nến H1 thưa đầu tiên:", df_h1_sparse[df_h1_sparse["n_m1"] <= 15]["time_utc"].min())
print("Nến H1 thưa cuối cùng:", df_h1_sparse[df_h1_sparse["n_m1"] <= 15]["time_utc"].max())

# Kiểm tra các ngày lân cận lúc hiện tượng biến mất trong năm 2015
last_sparse = df_h1_sparse[df_h1_sparse["n_m1"] <= 15]["time_utc"].max()
print(f"\nChi tiết 10 nến H1 thưa cuối cùng:")
print(df_h1_sparse[df_h1_sparse["n_m1"] <= 15].tail(10)[["time_utc", "hour_utc", "n_m1"]])

# Kiểm tra sau thời điểm đó, giờ nghỉ như thế nào (hoàn toàn rỗng hay n_m1 = 0)?
after_date = last_sparse.strftime('%Y-%m-%d')
print(f"\nKiểm tra các nến H1 trong giờ nghỉ (hour_utc 21, 22) xung quanh {after_date}:")
around_df = con.execute(f"""
    SELECT time_utc, hour(time_utc) as hr, n_m1
    FROM 'data/derived/GOLD_H1_utc/year=2015/*.parquet'
    WHERE time_utc >= '{last_sparse - pd.Timedelta(days=7)}'
      AND time_utc <= '{last_sparse + pd.Timedelta(days=14)}'
      AND hour(time_utc) IN (21, 22)
    ORDER BY time_utc
""").df()
print(around_df)
