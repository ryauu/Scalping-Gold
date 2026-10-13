"""Kiểm thử mô-đun signals.py (Bước 3: Vùng Á & Bước 3b: Tín hiệu phá vỡ Breakout):
1. Ngày giả lập làm tay (10 nến): kiểm tra đúng asia_high / asia_low, và nến 07:00 nằm ngoài vùng.
2. Ngày thiếu nến dưới ngưỡng (n_bars < min_bars) cho valid = False (mặc định mask_invalid=True cho NaN).
3. Ngày cuối tuần: n_bars = 0, valid = False, asia_high/low = NaN.
4. Đối chiếu ngày thật (2024-01-02 và 2024-01-29) với truy vấn DuckDB độc lập (dùng addCleanup).
5. Tín hiệu BUY giả lập: nến M1 đóng cửa vượt biên sinh tín hiệu, vào lệnh tại nến M1 kế tiếp.
6. Râu nến chạm qua biên nhưng đóng cửa bên trong: KHÔNG sinh tín hiệu.
7. Giá đóng cửa đúng bằng ngưỡng: KHÔNG sinh tín hiệu.
8. Cả hai hướng xảy ra trong ngày: ưu tiên hướng xuất hiện đầu tiên, tối đa 1 lệnh/ngày.
9. Đối chiếu tín hiệu trên ngày thật (2024-01-02): BUY lúc 09:37, vào lệnh lúc 09:38 giá 2077.83.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Đảm bảo thư mục gốc repo có trong sys.path để chạy trực tiếp: python tests/test_signals.py
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DB_PATH = _REPO_ROOT / "data" / "meta.duckdb"

import duckdb
import numpy as np
import pandas as pd

from src.backtest.data import attach_h1_atr, load_m1
from src.backtest.signals import (
    DEFAULT_MIN_ASIAN_BARS,
    compute_asian_range,
    generate_breakout_signals,
)


class TestSignalsModule(unittest.TestCase):
    def test_01_handmade_asian_range_and_0700_exclusion(self):
        """Bước 3 kiểm tra: 10 nến giả lập tính đúng high/low và nến 07:00:00 bị loại khỏi vùng Á."""
        times = pd.date_range("2024-01-02 00:00:00", periods=10, freq="30min")
        highs = [2005.0, 2012.5, 2008.0, 2015.0, 2010.0, 2018.5, 2007.0, 2020.0, 2014.0, 2009.0]
        lows = [1995.0, 2002.0, 1998.0, 2001.0, 1999.0, 2003.0, 1992.0, 2005.0, 2000.0, 1996.0]

        # Nến lúc 06:59:00 (phải được tính vào phiên Á)
        t_0659 = pd.Timestamp("2024-01-02 06:59:00")
        # Nến lúc 07:00:00 với high cực cao và low cực thấp (KHÔNG được tính)
        t_0700 = pd.Timestamp("2024-01-02 07:00:00")

        all_times = list(times) + [t_0659, t_0700]
        all_highs = highs + [2016.0, 9999.0]
        all_lows = lows + [2004.0, 100.0]

        df_m1 = pd.DataFrame({
            "time_utc": all_times,
            "open": all_highs,
            "high": all_highs,
            "low": all_lows,
            "close": all_highs,
        })

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
        """Bước 3 kiểm tra: Ngày thiếu nến dưới ngưỡng (n_bars < min_bars) cho valid = False và NaN mặc định."""
        times = pd.date_range("2024-01-03 00:00:00", periods=50, freq="1min")
        df_m1 = pd.DataFrame({
            "time_utc": times,
            "open": np.full(50, 2050.0),
            "high": np.full(50, 2055.0),
            "low": np.full(50, 2045.0),
            "close": np.full(50, 2050.0),
        })

        # Mặc định mask_invalid=True: vừa valid=False vừa gán NaN cho asia_high/low (fail-safe)
        res = compute_asian_range(df_m1, min_bars=DEFAULT_MIN_ASIAN_BARS)
        self.assertEqual(len(res), 1)
        row = res.iloc[0]
        self.assertEqual(row["n_bars"], 50)
        self.assertFalse(row["valid"])
        self.assertTrue(np.isnan(row["asia_high"]))
        self.assertTrue(np.isnan(row["asia_low"]))

        # Nếu truyền mask_invalid=False: giữ nguyên số liệu quan sát
        res_unmasked = compute_asian_range(df_m1, min_bars=DEFAULT_MIN_ASIAN_BARS, mask_invalid=False)
        row_u = res_unmasked.iloc[0]
        self.assertFalse(row_u["valid"])
        self.assertEqual(row_u["asia_high"], 2055.0)
        self.assertEqual(row_u["asia_low"], 2045.0)

    def test_03_weekend_and_empty_asian_bars(self):
        """Bước 3 kiểm tra: Ngày cuối tuần hoặc không có nến phiên Á có n_bars = 0 và valid = False."""
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
        m1_0102 = load_m1("2024-01-02", "2024-01-02")
        res_0102 = compute_asian_range(m1_0102)

        # Sử dụng DB_PATH chuẩn tuyệt đối và self.addCleanup
        con = duckdb.connect(str(DB_PATH), read_only=True)
        self.addCleanup(con.close)

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
        self.assertTrue(row["valid"])

        # Kiểm tra ngày 2024-01-29 (nơi nến 07:00:00 có đỉnh cao hơn đỉnh phiên Á)
        m1_0129 = load_m1("2024-01-29", "2024-01-29")
        res_0129 = compute_asian_range(m1_0129)
        row_0129 = res_0129[res_0129["date"] == "2024-01-29"].iloc[0]

        bar_0700_high = con.execute(
            "SELECT high FROM m1_utc WHERE time_utc = '2024-01-29 07:00:00'"
        ).fetchone()[0]

        # Đỉnh nến 07:00 cao hơn đỉnh phiên Á thực tế
        self.assertGreater(bar_0700_high, row_0129["asia_high"])
        self.assertNotEqual(row_0129["asia_high"], bar_0700_high, "Đỉnh vùng Á 2024-01-29 không được dính nến 07:00")

    def test_05_breakout_buy_handmade(self):
        """Bước 3b kiểm tra: Ngày giả có nến đóng cửa vượt biên sinh tín hiệu BUY và điểm vào lệnh đúng nến kế tiếp."""
        # 1. Tạo 380 nến phiên Á [00:00, 06:20) với range [2030, 2050]
        asia_times = pd.date_range("2024-01-15 00:00:00", periods=380, freq="1min")
        df_asia = pd.DataFrame({
            "time_utc": asia_times,
            "open": np.full(380, 2040.0),
            "high": np.full(380, 2050.0),
            "low": np.full(380, 2030.0),
            "close": np.full(380, 2040.0),
            "atr": np.full(380, 10.0),
        })

        # 2. Tạo nến trong cửa sổ [07:00, 13:00):
        # Lúc 07:15 có nến đóng cửa vượt biên BUY: asia_high (2050) + 0.1 * 10 (1.0) = 2051.0
        # Nến 07:15 đóng cửa tại 2052.5 > 2051.0 -> TÍN HIỆU BUY
        # Nến kế tiếp 07:16 có Open = 2053.0 -> ĐIỂM VÀO LỆNH
        session_times = pd.date_range("2024-01-15 07:00:00", periods=60, freq="1min")
        df_session = pd.DataFrame({
            "time_utc": session_times,
            "open": np.full(60, 2045.0),
            "high": np.full(60, 2048.0),
            "low": np.full(60, 2042.0),
            "close": np.full(60, 2045.0),
            "atr": np.full(60, 10.0),
        })

        # Nến index 15 (07:15:00) đóng cửa vượt ngưỡng
        df_session.loc[15, "close"] = 2052.5
        df_session.loc[15, "high"] = 2054.0

        # Nến index 16 (07:16:00) mở cửa tại 2053.0
        df_session.loc[16, "open"] = 2053.0

        df_all = pd.concat([df_asia, df_session], ignore_index=True)

        sigs = generate_breakout_signals(df_all)
        self.assertEqual(len(sigs), 1)

        sig = sigs.iloc[0]
        self.assertEqual(sig["direction"], "BUY")
        self.assertEqual(pd.Timestamp(sig["signal_time"]), pd.Timestamp("2024-01-15 07:15:00"))
        self.assertEqual(pd.Timestamp(sig["entry_time"]), pd.Timestamp("2024-01-15 07:16:00"))
        self.assertEqual(sig["entry_price"], 2053.0)
        self.assertAlmostEqual(sig["threshold"], 2051.0)
        self.assertEqual(sig["asia_high"], 2050.0)
        self.assertEqual(sig["asia_low"], 2030.0)

    def test_06_wick_only_no_signal(self):
        """Bước 3b kiểm tra: Râu nến chạm qua biên nhưng giá đóng cửa bên trong thì KHÔNG có tín hiệu."""
        asia_times = pd.date_range("2024-01-15 00:00:00", periods=380, freq="1min")
        df_asia = pd.DataFrame({
            "time_utc": asia_times,
            "open": np.full(380, 2040.0),
            "high": np.full(380, 2050.0),
            "low": np.full(380, 2030.0),
            "close": np.full(380, 2040.0),
            "atr": np.full(380, 10.0),
        })

        session_times = pd.date_range("2024-01-15 07:00:00", periods=60, freq="1min")
        df_session = pd.DataFrame({
            "time_utc": session_times,
            "open": np.full(60, 2045.0),
            "high": np.full(60, 2048.0),
            "low": np.full(60, 2042.0),
            "close": np.full(60, 2045.0),
            "atr": np.full(60, 10.0),
        })

        # Ngưỡng BUY là 2051.0 (2050 + 0.1 * 10). Râu nến quét lên 2055.0 nhưng đóng cửa 2050.0
        df_session.loc[20, "high"] = 2055.0
        df_session.loc[20, "close"] = 2050.0

        # Ngưỡng SELL là 2029.0 (2030 - 0.1 * 10). Râu nến quét xuống 2025.0 nhưng đóng cửa 2031.0
        df_session.loc[30, "low"] = 2025.0
        df_session.loc[30, "close"] = 2031.0

        df_all = pd.concat([df_asia, df_session], ignore_index=True)
        sigs = generate_breakout_signals(df_all)

        self.assertTrue(sigs.empty, "Râu nến quét qua nhưng close đóng bên trong tuyệt đối không được sinh tín hiệu")

    def test_07_exact_equality_no_signal(self):
        """Bước 3b kiểm tra: Giá đóng cửa đúng bằng ngưỡng (bằng, không lớn hơn) thì KHÔNG có tín hiệu."""
        asia_times = pd.date_range("2024-01-15 00:00:00", periods=380, freq="1min")
        df_asia = pd.DataFrame({
            "time_utc": asia_times,
            "open": np.full(380, 2040.0),
            "high": np.full(380, 2050.0),
            "low": np.full(380, 2030.0),
            "close": np.full(380, 2040.0),
            "atr": np.full(380, 10.0),
        })

        session_times = pd.date_range("2024-01-15 07:00:00", periods=60, freq="1min")
        df_session = pd.DataFrame({
            "time_utc": session_times,
            "open": np.full(60, 2045.0),
            "high": np.full(60, 2048.0),
            "low": np.full(60, 2042.0),
            "close": np.full(60, 2045.0),
            "atr": np.full(60, 10.0),
        })

        # Ngưỡng BUY = 2051.0. Đóng cửa ĐÚNG BẰNG 2051.0
        df_session.loc[15, "close"] = 2051.0

        df_all = pd.concat([df_asia, df_session], ignore_index=True)
        sigs = generate_breakout_signals(df_all)

        self.assertTrue(sigs.empty, "Đóng cửa đúng bằng ngưỡng (không strictly >) không được sinh tín hiệu")

    def test_08_both_directions_first_priority(self):
        """Bước 3b kiểm tra: Cả 2 hướng đều xảy ra trong ngày thì ưu tiên hướng đến trước, tối đa 1 lệnh/ngày."""
        asia_times = pd.date_range("2024-01-15 00:00:00", periods=380, freq="1min")
        df_asia = pd.DataFrame({
            "time_utc": asia_times,
            "open": np.full(380, 2040.0),
            "high": np.full(380, 2050.0),
            "low": np.full(380, 2030.0),
            "close": np.full(380, 2040.0),
            "atr": np.full(380, 10.0),
        })

        session_times = pd.date_range("2024-01-15 07:00:00", periods=120, freq="1min")
        df_session = pd.DataFrame({
            "time_utc": session_times,
            "open": np.full(120, 2040.0),
            "high": np.full(120, 2045.0),
            "low": np.full(120, 2035.0),
            "close": np.full(120, 2040.0),
            "atr": np.full(120, 10.0),
        })

        # 1. Lúc 07:30: Tín hiệu SELL phá vỡ đáy: close = 2028.0 < 2029.0
        df_session.loc[30, "close"] = 2028.0
        df_session.loc[31, "open"] = 2027.5

        # 2. Lúc 08:30: Tín hiệu BUY phá vỡ đỉnh: close = 2055.0 > 2051.0
        df_session.loc[90, "close"] = 2055.0
        df_session.loc[91, "open"] = 2056.0

        df_all = pd.concat([df_asia, df_session], ignore_index=True)
        sigs = generate_breakout_signals(df_all)

        self.assertEqual(len(sigs), 1, "Chỉ được sinh tối đa 1 tín hiệu mỗi ngày")
        sig = sigs.iloc[0]
        self.assertEqual(sig["direction"], "SELL", "Phải lấy tín hiệu SELL xuất hiện trước lúc 07:30")
        self.assertEqual(pd.Timestamp(sig["signal_time"]), pd.Timestamp("2024-01-15 07:30:00"))
        self.assertEqual(pd.Timestamp(sig["entry_time"]), pd.Timestamp("2024-01-15 07:31:00"))
        self.assertEqual(sig["entry_price"], 2027.5)

    def test_09_real_day_breakout_signal(self):
        """Bước 3b kiểm tra thực tế: Đối chiếu tín hiệu phá vỡ ngày 2024-01-02 trên dữ liệu DuckDB."""
        m1 = load_m1("2024-01-02", "2024-01-02")
        m1_atr = attach_h1_atr(m1)
        sigs = generate_breakout_signals(m1_atr)

        self.assertEqual(len(sigs), 1, "Ngày 2024-01-02 phải có đúng 1 tín hiệu Breakout")
        sig = sigs.iloc[0]
        self.assertEqual(sig["direction"], "BUY")
        self.assertEqual(str(sig["signal_time"]), "2024-01-02 09:37:00")
        self.assertEqual(str(sig["entry_time"]), "2024-01-02 09:38:00")
        self.assertAlmostEqual(sig["entry_price"], 2077.83, places=2)
        self.assertAlmostEqual(sig["asia_high"], 2076.68, places=2)
        self.assertAlmostEqual(sig["threshold"], 2077.07, places=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

