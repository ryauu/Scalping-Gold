"""Kiểm thử mô-đun signals.py (Bước 3: Vùng Á):
1. Ngày giả lập làm tay (10 nến): kiểm tra đúng asia_high / asia_low, và nến 07:00 nằm ngoài vùng.
2. Ngày thiếu nến dưới ngưỡng (n_bars < min_bars) cho valid = False.
3. Ngày cuối tuần: n_bars = 0, valid = False, asia_high/low = NaN.
4. Đối chiếu ngày thật (2024-01-02 và 2024-01-29) với truy vấn DuckDB độc lập.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Đảm bảo thư mục gốc repo có trong sys.path để chạy trực tiếp: python tests/test_signals.py
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import duckdb
import numpy as np
import pandas as pd

from src.backtest.data import load_m1
from src.backtest.signals import DEFAULT_MIN_ASIAN_BARS, compute_asian_range


class TestSignalsModule(unittest.TestCase):
    def test_01_handmade_asian_range_and_0700_exclusion(self):
        """Bước 3 kiểm tra: 10 nến giả lập tính đúng high/low và nến 07:00:00 bị loại khỏi vùng Á."""
        # 1. Tạo 10 nến trong cửa sổ [00:00, 07:00) ngày 2024-01-02
        # Các mốc: 00:00, 00:30, 01:00, ..., 04:30
        times = pd.date_range("2024-01-02 00:00:00", periods=10, freq="30min")
        highs = [2005.0, 2012.5, 2008.0, 2015.0, 2010.0, 2018.5, 2007.0, 2020.0, 2014.0, 2009.0]
        lows = [1995.0, 2002.0, 1998.0, 2001.0, 1999.0, 2003.0, 1992.0, 2005.0, 2000.0, 1996.0]

        # 2. Thêm nến lúc 06:59:00 (phải được tính vào phiên Á)
        t_0659 = pd.Timestamp("2024-01-02 06:59:00")
        # 3. Thêm nến lúc 07:00:00 với high cực cao và low cực thấp (KHÔNG được tính)
        t_0700 = pd.Timestamp("2024-01-02 07:00:00")

        all_times = list(times) + [t_0659, t_0700]
        all_highs = highs + [2016.0, 9999.0]  # 9999.0 lúc 07:00
        all_lows = lows + [2004.0, 100.0]     # 100.0 lúc 07:00

        df_m1 = pd.DataFrame({
            "time_utc": all_times,
            "open": all_highs,
            "high": all_highs,
            "low": all_lows,
            "close": all_highs,
        })

        # Kỳ vọng: Trong phiên Á [00:00, 07:00):
        # max_high là 2020.0 (tại index 7). Nến 07:00 (9999.0) không được tính!
        # min_low là 1992.0 (tại index 6). Nến 07:00 (100.0) không được tính!
        # Tổng số nến trong phiên Á: 10 nến ban đầu + 1 nến 06:59 = 11 nến
        res = compute_asian_range(df_m1, min_bars=11)

        self.assertEqual(len(res), 1)
        row = res.iloc[0]
        self.assertEqual(pd.Timestamp(row["date"]), pd.Timestamp("2024-01-02"))
        self.assertEqual(row["n_bars"], 11, "Phải đếm đúng 11 nến trong cửa sổ [00:00, 07:00)")
        self.assertAlmostEqual(row["asia_high"], 2020.0, places=4, msg="asia_high phải lấy max trong [00:00, 07:00)")
        self.assertAlmostEqual(row["asia_low"], 1992.0, places=4, msg="asia_low phải lấy min trong [00:00, 07:00)")
        self.assertNotEqual(row["asia_high"], 9999.0, "Nến 07:00:00 tuyệt đối không được tính vào asia_high")
        self.assertNotEqual(row["asia_low"], 100.0, "Nến 07:00:00 tuyệt đối không được tính vào asia_low")
        self.assertTrue(row["valid"], "Vùng Á phải valid khi n_bars >= min_bars")

    def test_02_below_threshold_gives_invalid(self):
        """Bước 3 kiểm tra: Ngày thiếu nến dưới ngưỡng (n_bars < min_bars) cho cờ valid = False."""
        # Tạo ngày chỉ có 50 nến (dưới ngưỡng mặc định 378 nến)
        times = pd.date_range("2024-01-03 00:00:00", periods=50, freq="1min")
        df_m1 = pd.DataFrame({
            "time_utc": times,
            "open": np.full(50, 2050.0),
            "high": np.full(50, 2055.0),
            "low": np.full(50, 2045.0),
            "close": np.full(50, 2050.0),
        })

        # Sử dụng ngưỡng mặc định DEFAULT_MIN_ASIAN_BARS = 378
        res = compute_asian_range(df_m1, min_bars=DEFAULT_MIN_ASIAN_BARS)
        self.assertEqual(len(res), 1)
        row = res.iloc[0]
        self.assertEqual(row["n_bars"], 50)
        self.assertFalse(row["valid"], "Ngày có 50 nến (< 378) bắt buộc phải có valid = False")

        # Kiểm tra tùy chọn mask_invalid=True
        res_masked = compute_asian_range(df_m1, min_bars=DEFAULT_MIN_ASIAN_BARS, mask_invalid=True)
        row_m = res_masked.iloc[0]
        self.assertTrue(np.isnan(row_m["asia_high"]))
        self.assertTrue(np.isnan(row_m["asia_low"]))

    def test_03_weekend_and_empty_asian_bars(self):
        """Bước 3 kiểm tra: Ngày cuối tuần hoặc không có nến phiên Á có n_bars = 0 và valid = False."""
        # Giả lập ngày Chủ Nhật (2024-01-07): broker chỉ mở cửa phiên đêm từ 22:00 UTC (0 nến phiên Á)
        sunday_times = pd.date_range("2024-01-07 22:00:00", periods=30, freq="1min")
        df_m1 = pd.DataFrame({
            "time_utc": sunday_times,
            "open": np.full(30, 2040.0),
            "high": np.full(30, 2042.0),
            "low": np.full(30, 2038.0),
            "close": np.full(30, 2040.0),
        })

        res = compute_asian_range(df_m1)
        self.assertEqual(len(res), 1)
        row = res.iloc[0]
        self.assertEqual(pd.Timestamp(row["date"]), pd.Timestamp("2024-01-07"))
        self.assertEqual(row["n_bars"], 0, "Ngày không có nến trong [00:00, 07:00) phải có n_bars = 0")
        self.assertFalse(row["valid"], "Ngày n_bars = 0 phải có valid = False")
        self.assertTrue(np.isnan(row["asia_high"]), "asia_high phải là NaN khi n_bars = 0")
        self.assertTrue(np.isnan(row["asia_low"]), "asia_low phải là NaN khi n_bars = 0")

    def test_04_verify_against_independent_duckdb_query(self):
        """Bước 3 kiểm tra độc lập: Đối chiếu ngày thật (2024-01-02 và 2024-01-29) với truy vấn DuckDB."""
        # 1. Kiểm tra ngày 2024-01-02 (ngày giao dịch đầu năm điển hình)
        m1_0102 = load_m1("2024-01-02", "2024-01-02")
        res_0102 = compute_asian_range(m1_0102)

        # Truy vấn trực tiếp DuckDB
        con = duckdb.connect("data/meta.duckdb", read_only=True)
        query_duck = """
            SELECT 
                count(*) as n_bars,
                max(high) as asia_high,
                min(low) as asia_low
            FROM m1_utc
            WHERE time_utc >= '2024-01-02 00:00:00' AND time_utc < '2024-01-02 07:00:00'
        """
        duck_row = con.execute(query_duck).df().iloc[0]

        row = res_0102[res_0102["date"] == "2024-01-02"].iloc[0]
        self.assertEqual(row["n_bars"], int(duck_row["n_bars"]))
        self.assertEqual(row["n_bars"], 420, "Ngày 2024-01-02 có đủ 420 nến phiên Á")
        self.assertAlmostEqual(row["asia_high"], float(duck_row["asia_high"]), places=4)
        self.assertAlmostEqual(row["asia_low"], float(duck_row["asia_low"]), places=4)
        self.assertAlmostEqual(row["asia_high"], 2076.68, places=2)
        self.assertAlmostEqual(row["asia_low"], 2062.20, places=2)
        self.assertTrue(row["valid"])

        # 2. Kiểm tra ngày 2024-01-29 (nơi nến 07:00:00 có đỉnh cao hơn đỉnh phiên Á)
        m1_0129 = load_m1("2024-01-29", "2024-01-29")
        res_0129 = compute_asian_range(m1_0129)
        row_0129 = res_0129[res_0129["date"] == "2024-01-29"].iloc[0]

        # Đỉnh nến 07:00:00 là 2029.56, đỉnh phiên Á thật sự là 2028.87
        bar_0700_high = con.execute(
            "SELECT high FROM m1_utc WHERE time_utc = '2024-01-29 07:00:00'"
        ).fetchone()[0]
        con.close()

        self.assertAlmostEqual(row_0129["asia_high"], 2028.87, places=2)
        self.assertGreater(bar_0700_high, row_0129["asia_high"])
        self.assertNotEqual(row_0129["asia_high"], bar_0700_high, "Đỉnh vùng Á 2024-01-29 không được dính nến 07:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
