import sys
from pathlib import Path
import duckdb
import pandas as pd
import matplotlib.pyplot as plt

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

artifacts_dir = Path("artifacts")
artifacts_dir.mkdir(parents=True, exist_ok=True)

con = duckdb.connect()

# 1. Lấy dữ liệu 111 nến H1 thưa trong 2014-2015
df_m1_sparse = con.execute("""
    WITH sparse_h1 AS (
        SELECT time_utc as h1_time, date_trunc('hour', time_utc) as h1_start, n_m1
        FROM 'data/derived/GOLD_H1_utc/year=201*/*.parquet'
        WHERE year IN (2014, 2015)
          AND n_m1 <= 15
          AND date_part('hour', time_utc) IN (21, 22)
    )
    SELECT 
        m.time_utc,
        m.time_server,
        date_part('minute', m.time_utc) as min_utc,
        date_part('hour', m.time_utc) as hour_utc,
        m.year
    FROM 'data/clean/GOLD_M1_utc/year=201*/*.parquet' m
    JOIN sparse_h1 s
      ON date_trunc('hour', m.time_utc) = s.h1_start
    ORDER BY m.time_utc
""").df()

# Thống kê phân phối phút
minute_counts = df_m1_sparse["min_utc"].value_counts().reindex(range(60), fill_value=0)

# 2. Lấy chuỗi thời gian số nến trong giờ nghỉ (21h/22h UTC) theo từng ngày 2014 - 2015
df_daily_break = con.execute("""
    SELECT 
        cast(time_utc as date) as trade_date,
        count(*) as break_m1_count
    FROM 'data/clean/GOLD_M1_utc/year=201*/*.parquet'
    WHERE year IN (2014, 2015)
      AND (
        (date_part('hour', time_utc) = 21 AND date_part('month', time_utc) BETWEEN 4 AND 10) OR
        (date_part('hour', time_utc) = 22 AND (date_part('month', time_utc) < 4 OR date_part('month', time_utc) > 10))
      )
    GROUP BY 1
    ORDER BY 1
""").df()
df_daily_break["trade_date"] = pd.to_datetime(df_daily_break["trade_date"])

# Vẽ biểu đồ
plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))

# Subplot 1: Phân phối phút trong giờ
bars = ax1.bar(minute_counts.index, minute_counts.values, color='#d97706', edgecolor='#b45309', alpha=0.85, width=0.8)
ax1.set_title("Phân phối phút trong giờ (Minute-in-Hour) của các nến H1 thưa trong giờ nghỉ (2014-2015)", fontsize=13, fontweight='bold', pad=12)
ax1.set_xlabel("Phút trong giờ (00 -> 59)", fontsize=11)
ax1.set_ylabel("Số lượng nến M1 ghi nhận", fontsize=11)
ax1.set_xticks(range(0, 60, 5))
ax1.axvline(14.5, color='#dc2626', linestyle='--', linewidth=2, label="Giới hạn phút 15 (Giờ nghỉ 17:15 - 18:00 NY)")
ax1.legend(fontsize=11, loc='upper right')

# Chú thích Subplot 1
ax1.annotate("Phút 00 - 14: ~200 nến/phút\n(Thị trường hoạt động)", xy=(7, 200), xytext=(12, 230),
             arrowprops=dict(facecolor='black', shrink=0.05, width=1, headwidth=6),
             fontsize=10, bbox=dict(boxstyle="round,pad=0.3", fc="#fef3c7", ec="#f59e0b", lw=1))
ax1.annotate("Phút 15 - 59: Hoàn toàn rỗng (0 nến)\n(Thị trường nghỉ bảo trì)", xy=(35, 2), xytext=(25, 100),
             arrowprops=dict(facecolor='black', shrink=0.05, width=1, headwidth=6),
             fontsize=10, bbox=dict(boxstyle="round,pad=0.3", fc="#fee2e2", ec="#ef4444", lw=1))

# Subplot 2: Chuỗi thời gian chuyển dịch lịch tháng 7/2015
ax2.plot(df_daily_break["trade_date"], df_daily_break["break_m1_count"], color='#2563eb', marker='o', markersize=3, linestyle='-', alpha=0.7)
ax2.set_title("Số nến M1 trong giờ nghỉ hằng ngày (2014 - 2015): Mốc đổi lịch đầu tháng 7/2015", fontsize=13, fontweight='bold', pad=12)
ax2.set_xlabel("Thời gian (Ngày giao dịch)", fontsize=11)
ax2.set_ylabel("Số nến M1 trong giờ nghỉ", fontsize=11)
ax2.axvline(pd.Timestamp("2015-07-01"), color='#dc2626', linestyle='--', linewidth=2, label="01/07/2015: Sàn đổi lịch nghỉ sang 17:00 NY (0 nến M1)")
ax2.legend(fontsize=11, loc='upper right')

# Chú thích Subplot 2
ax2.annotate("Giai đoạn 2014 - 06/2015:\nThường xuyên có ~14 nến M1/ngày\n(Nghỉ lúc 17:15 NY)", 
             xy=(pd.Timestamp("2014-11-01"), 14), xytext=(pd.Timestamp("2014-08-01"), 25),
             arrowprops=dict(facecolor='black', shrink=0.05, width=1, headwidth=6),
             fontsize=10, bbox=dict(boxstyle="round,pad=0.3", fc="#eff6ff", ec="#3b82f6", lw=1))
ax2.annotate("Sau 01/07/2015:\nGiờ nghỉ hoàn toàn rỗng (0 nến M1)\n(Nghỉ từ 17:00 NY)", 
             xy=(pd.Timestamp("2015-08-01"), 0), xytext=(pd.Timestamp("2015-07-15"), 20),
             arrowprops=dict(facecolor='black', shrink=0.05, width=1, headwidth=6),
             fontsize=10, bbox=dict(boxstyle="round,pad=0.3", fc="#f0fdf4", ec="#22c55e", lw=1))

plt.tight_layout()
out_png = artifacts_dir / "sparse_candles_minute_distribution.png"
plt.savefig(out_png, dpi=150)
plt.close()
print(f"[Đã lưu biểu đồ] {out_png}")
