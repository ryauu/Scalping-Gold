import sys
import duckdb
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

print("=====================================================================")
print("QUÉT TOÀN BỘ DỮ LIỆU 2014-2026 ĐỐI CHIẾU MÚI GIỜ CŨ VÀ MỚI")
print("=====================================================================")

con = duckdb.connect()

# 1. Nạp toàn bộ dữ liệu sạch M1
print("\n1. Đang nạp toàn bộ 4,353,867 nến M1 từ data/clean/GOLD_M1_utc...")
df = con.execute("""
    SELECT time_utc, time_server, year
    FROM read_parquet('data/clean/GOLD_M1_utc/**/*.parquet')
    ORDER BY time_server
""").df()

n_rows = len(df)
print(f"   Đã nạp {n_rows:,} dòng M1.")
assert n_rows == 4_353_867, f"Số dòng M1 ({n_rows}) khác 4,353,867!"

# 2. Tính time_utc theo hai quy tắc độc lập
print("\n2. Tính time_utc theo quy tắc cũ (NY+7) và quy tắc mới (Europe/Nicosia)...")
ts_server = pd.DatetimeIndex(df["time_server"])

# Quy tắc mới: Europe/Nicosia
ts_nicosia = ts_server.tz_localize("Europe/Nicosia", ambiguous="NaT", nonexistent="shift_forward")
n_bad_new = int(ts_nicosia.isna().sum())
print(f"   Số nến rơi vào giờ mơ hồ khi chuyển giờ EU (n_bad): {n_bad_new} (Kỳ vọng = 0)")
assert n_bad_new == 0, "n_bad phải bằng 0!"
utc_new = ts_nicosia.tz_convert("UTC").tz_localize(None)

# Khẳng định file parquet vừa ghi trùng khớp hoàn toàn với utc_new
match_file = (df["time_utc"] == utc_new).all()
print(f"   File parquet data/clean vừa ghi khớp 100% với tính toán mới: {match_file}")
assert match_file, "Cột time_utc trong file không khớp với utc_new!"

# Quy tắc cũ: America/New_York + 7h
ts_ny = (ts_server - pd.Timedelta(hours=7)).tz_localize("America/New_York", ambiguous="NaT", nonexistent="shift_forward")
n_bad_old = int(ts_ny.isna().sum())
print(f"   Số nến rơi vào giờ mơ hồ theo quy tắc cũ: {n_bad_old}")
utc_old = ts_ny.tz_convert("UTC").tz_localize(None)

# 3. Tính độ chênh lệch (diff_hours = utc_new - utc_old)
print("\n3. Kiểm tra chênh lệch giờ trên toàn bộ 4,353,867 dòng:")
diff_sec = (utc_new - utc_old).total_seconds()
diff_hours = diff_sec / 3600.0

# Loại trừ các nến NaT của old (nếu có lúc chuyển giờ NY)
valid_mask = ~np.isnan(diff_hours)
unique_diffs = np.unique(diff_hours[valid_mask])
print(f"   Các giá trị chênh lệch duy nhất tồn tại: {unique_diffs}")
assert set(unique_diffs).issubset({0.0, 1.0}), f"Lỗi: Xuất hiện độ lệch khác 0 và 1: {unique_diffs}!"
print("   [KHẲNG ĐỊNH] Mọi dòng dữ liệu chỉ có đúng 2 trạng thái: chênh 0h hoặc chênh đúng +1h!")

# 4. Đối chiếu tập ngày chênh +1h với khoảng chênh DST lý thuyết (ZoneInfo)
print("\n4. Đối chiếu tập ngày có nến chênh +1h với tính toán lịch DST lý thuyết (ZoneInfo):")
# Lấy các ngày có ít nhất một nến chênh +1h
df["diff_hours"] = diff_hours
df["date"] = pd.to_datetime(df["time_server"]).dt.date
data_gap_dates = set(df[df["diff_hours"] == 1.0]["date"].unique())
print(f"   Số ngày trong dữ liệu có nến chênh +1h: {len(data_gap_dates)} ngày")

# Tính toán lý thuyết cho mọi ngày từ 2014-06-13 đến 2026-10-13
start_d = date(2014, 6, 13)
end_d = date(2026, 10, 13)
all_trading_dates = set(df["date"].unique())

theo_gap_dates = set()
curr = start_d
while curr <= end_d:
    # Lấy thời điểm 12:00 UTC của ngày
    dt_utc = datetime(curr.year, curr.month, curr.day, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
    offset_ny = dt_utc.astimezone(ZoneInfo("America/New_York")).utcoffset().total_seconds() / 3600.0
    offset_eu = dt_utc.astimezone(ZoneInfo("Europe/Nicosia")).utcoffset().total_seconds() / 3600.0
    # Chênh lệch bình thường: đông EU(+2) - NY(-5) = 7h; hè EU(+3) - NY(-4) = 7h
    # Khi Mỹ đã sang hè (-4) mà EU vẫn là đông (+2): EU - NY = 6h -> lệch 1h!
    if (offset_eu - offset_ny) == 6.0:
        theo_gap_dates.add(curr)
    curr += timedelta(days=1)

# Chỉ xét các ngày có giao dịch thực tế
theo_gap_trading_dates = theo_gap_dates.intersection(all_trading_dates)
print(f"   Số ngày giao dịch lý thuyết nằm trong khoảng chênh DST: {len(theo_gap_trading_dates)} ngày")

diff_dates = data_gap_dates.symmetric_difference(theo_gap_trading_dates)
print(f"   Số ngày sai lệch giữa thực tế dữ liệu và lý thuyết ZoneInfo: {len(diff_dates)}")
if len(diff_dates) == 0:
    print("   [KHẲNG ĐỊNH HOÀN HẢO] 100% các ngày chênh +1h trong dữ liệu TRÙNG KHỚP TUYỆT ĐỐI với lịch DST lý thuyết!")
else:
    print(f"   Chi tiết các ngày lệch: {diff_dates}")

# 5. Phân rã số ngày chênh +1h theo từng năm
print("\n5. Bảng số ngày chênh DST theo từng năm (2014-2026):")
gap_series = pd.Series(list(data_gap_dates))
gap_by_year = gap_series.apply(lambda d: d.year).value_counts().sort_index()
for yr, cnt in gap_by_year.items():
    print(f"   Năm {yr}: {cnt:2d} ngày giao dịch")
print(f"   Tổng cộng toàn bộ lịch sử: {gap_by_year.sum()} ngày giao dịch chênh DST.")

# 6. Đối chiếu số lần mở lại lúc 21:00 UTC giữa cũ và mới
print("\n6. Đối chiếu số ngày có nến mở lại lúc 21:00 UTC:")
# Dữ liệu cũ
h1_old = con.execute("""
    SELECT time_utc, year FROM read_parquet('data/derived_v1/GOLD_H1_utc/**/*.parquet') ORDER BY time_utc
""").df()
h1_old["time_utc"] = pd.to_datetime(h1_old["time_utc"])
diffs_old = h1_old["time_utc"].diff()
gap21_old = h1_old[(h1_old["time_utc"].dt.hour == 21) & (diffs_old >= pd.Timedelta(hours=2)) & (diffs_old < pd.Timedelta(days=2))]
cnt_old = gap21_old["time_utc"].dt.year.value_counts().sort_index()

# Dữ liệu mới
h1_new = con.execute("""
    SELECT time_utc, year FROM read_parquet('data/derived/GOLD_H1_utc/**/*.parquet') ORDER BY time_utc
""").df()
h1_new["time_utc"] = pd.to_datetime(h1_new["time_utc"])
diffs_new = h1_new["time_utc"].diff()
gap21_new = h1_new[(h1_new["time_utc"].dt.hour == 21) & (diffs_new >= pd.Timedelta(hours=2)) & (diffs_new < pd.Timedelta(days=2))]
cnt_new = gap21_new["time_utc"].dt.year.value_counts().sort_index()

print("Năm    | Dữ liệu cũ (NY+7) | Dữ liệu mới (Nicosia)")
for yr in range(2014, 2027):
    c_o = cnt_old.get(yr, 0)
    c_n = cnt_new.get(yr, 0)
    print(f"  {yr} | {c_o:17d} | {c_n:20d}")
print(f"Tổng   | {cnt_old.sum():17d} | {cnt_new.sum():20d}")

# 7. Đối chiếu tổng số dòng H1 và M1 cũ vs mới
print("\n7. Đối chiếu tổng số dòng dữ liệu:")
m1_new_count = con.execute("SELECT count(*) FROM read_parquet('data/clean/GOLD_M1_utc/**/*.parquet')").fetchone()[0]
m1_old_count = con.execute("SELECT count(*) FROM read_parquet('data/clean_v1/GOLD_M1_utc/**/*.parquet')").fetchone()[0]
h1_new_count = con.execute("SELECT count(*) FROM read_parquet('data/derived/GOLD_H1_utc/**/*.parquet')").fetchone()[0]
h1_old_count = con.execute("SELECT count(*) FROM read_parquet('data/derived_v1/GOLD_H1_utc/**/*.parquet')").fetchone()[0]
print(f"   M1 cũ: {m1_old_count:,} dòng | M1 mới: {m1_new_count:,} dòng (chênh lệch: {m1_new_count - m1_old_count})")
print(f"   H1 cũ: {h1_old_count:,} dòng | H1 mới: {h1_new_count:,} dòng (chênh lệch: {h1_new_count - h1_old_count})")

# 8. Khảo sát phân bố n_m1 của các nến H1 trong giờ nghỉ 2014-2015
print("\n8. Phân bố n_m1 của các nến H1 trong giờ nghỉ 2014-2015 (mới):")
h1_1415 = con.execute("""
    SELECT 
        time_utc,
        date_part('year', time_utc) as yr,
        date_part('month', time_utc) as mo,
        date_part('hour', time_utc) as hr,
        n_m1
    FROM read_parquet('data/derived/GOLD_H1_utc/**/*.parquet')
    WHERE yr IN (2014, 2015)
""").df()

# Giờ nghỉ mùa đông là 22:00 UTC, mùa hè là 21:00 UTC
break_w = h1_1415[(h1_1415["mo"].isin([1, 2, 12])) & (h1_1415["hr"] == 22)]
break_s = h1_1415[(h1_1415["mo"].isin([6, 7, 8])) & (h1_1415["hr"] == 21)]

print(f"   Mùa đông (T1, 2, 12) tại 22:00 UTC: Tổng {len(break_w)} nến H1.")
print(f"     n_m1 trung bình: {break_w['n_m1'].mean():.1f}, min: {break_w['n_m1'].min()}, max: {break_w['n_m1'].max()}")
print(f"     Phân vị 50%: {break_w['n_m1'].median():.0f}, Phân vị 90%: {break_w['n_m1'].quantile(0.9):.0f}")

print(f"   Mùa hè (T6, 7, 8) tại 21:00 UTC: Tổng {len(break_s)} nến H1.")
print(f"     n_m1 trung bình: {break_s['n_m1'].mean():.1f}, min: {break_s['n_m1'].min()}, max: {break_s['n_m1'].max()}")
print(f"     Phân vị 50%: {break_s['n_m1'].median():.0f}, Phân vị 90%: {break_s['n_m1'].quantile(0.9):.0f}")

