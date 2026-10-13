"""Kiểm thử mô-đun simulate.py (Bước 4: Mô phỏng khớp lệnh và quản lý rủi ro R-multiple):

1. BUY chạm TP (chi phí = 0): ra đúng +2.0R.
2. BUY chạm SL (chi phí = 0): ra đúng -1.0R.
3. BUY chạm cả SL lẫn TP trong 1 nến: tính SL (-1.0R).
4. BUY thoát lúc 16:00 UTC: ra đúng lý do TIME tại giá mở.
5. BUY gap qua SL: ra đúng lý do SL_GAP tại giá mở.
6. BUY chính nến vào lệnh đã chạm SL: ra đúng SL ngay nến vào lệnh.
7. BUY hết dữ liệu trước 16:00: ra đúng lý do DATA_END tại giá đóng cuối.
8. SELL chạm TP (chi phí = 0): ra đúng +2.0R.
9. SELL chạm SL (chi phí = 0): kích hoạt theo ask (bid high + spread >= SL), ra đúng -1.0R.
10. SELL chạm cả SL lẫn TP trong 1 nến: tính SL (-1.0R).
11. SELL thoát lúc 16:00 UTC: ra đúng lý do TIME tại giá mở.
12. SELL gap qua SL: kích hoạt theo ask, ra đúng lý do SL_GAP tại giá mở.
13. SELL chính nến vào lệnh đã chạm SL: ra đúng SL ngay nến vào lệnh.
14. SELL hết dữ liệu trước 16:00: ra đúng lý do DATA_END tại giá đóng cuối.
15. Chi phí khác 0: TP trả đúng ít hơn 2R một lượng bằng chi phí trượt giá quy ra R (2.0 - slip / R_dist).
16. Bất đối xứng gap: Gap qua TP KHÔNG thưởng thêm, chỉ lấy đúng mức TP (bảo thủ).
17. Kiểm đếm DATA_END và summary attributes trong simulate_trades.
18. Pipeline thực tế end-to-end với dữ liệu DuckDB (ngày 2024-01-02).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Đảm bảo thư mục gốc repo có trong sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
import pandas as pd

from src.backtest.data import load_m1
from src.backtest.signals import compute_asian_range, generate_breakout_signals
from src.backtest.simulate import (
    DEFAULT_POINT,
    DEFAULT_SLIP_POINTS,
    YEARLY_MEDIAN_SPREADS,
    get_effective_spread,
    simulate_single_trade,
    simulate_trades,
)


class TestSimulateModule(unittest.TestCase):
    def setUp(self):
        # Thiết lập ngày giả lập kiểm thử
        self.entry_time = pd.Timestamp("2024-01-15 07:16:00")
        self.trade_date = pd.Timestamp("2024-01-15")

    # ==========================================
    # CÁC TEST BUY VỚI CHI PHÍ BẰNG 0
    # Entry = 2100, SL = 2090 -> R_dist = 10, TP = 2120
    # ==========================================

    def test_01_buy_tp_zero_cost(self):
        """1. BUY chạm TP: BUY vào 2100, SL 2090 -> R_dist = 10, TP = 2120. Chạm TP ra đúng +2.0R."""
        # 3 nến: nến vào lệnh [07:16], nến kế tiếp [07:17], nến chạm TP [07:18]
        times = pd.date_range("2024-01-15 07:16:00", periods=3, freq="1min")
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2105.0, 2115.0],
            "high": [2104.0, 2112.0, 2125.0],  # Nến 07:18 có high = 2125 >= TP (2120)
            "low": [2098.0, 2102.0, 2110.0],
            "close": [2102.0, 2110.0, 2122.0],
        })

        res = simulate_single_trade(
            direction="BUY",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2090.0,
            m1_bars=df_bars,
            spread=0.0,
            slip=0.0,
        )

        self.assertEqual(res["direction"], "BUY")
        self.assertEqual(res["entry_price"], 2100.0)
        self.assertEqual(res["sl"], 2090.0)
        self.assertEqual(res["r_dist"], 10.0)
        self.assertEqual(res["tp"], 2120.0)
        self.assertEqual(res["exit_reason"], "TP")
        self.assertEqual(res["exit_price"], 2120.0)
        self.assertEqual(pd.Timestamp(res["exit_time"]), pd.Timestamp("2024-01-15 07:18:00"))
        self.assertAlmostEqual(res["pnl_r"], 2.0, places=6)

    def test_02_buy_sl_zero_cost(self):
        """2. BUY chạm SL: BUY vào 2100, SL 2090 -> R_dist = 10. Chạm SL ra đúng -1.0R."""
        times = pd.date_range("2024-01-15 07:16:00", periods=3, freq="1min")
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2098.0, 2095.0],
            "high": [2102.0, 2101.0, 2096.0],
            "low": [2096.0, 2094.0, 2088.0],  # Nến 07:18 có low = 2088 <= SL (2090)
            "close": [2098.0, 2095.0, 2090.0],
        })

        res = simulate_single_trade(
            direction="BUY",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2090.0,
            m1_bars=df_bars,
            spread=0.0,
            slip=0.0,
        )

        self.assertEqual(res["exit_reason"], "SL")
        self.assertEqual(res["exit_price"], 2090.0)
        self.assertEqual(pd.Timestamp(res["exit_time"]), pd.Timestamp("2024-01-15 07:18:00"))
        self.assertAlmostEqual(res["pnl_r"], -1.0, places=6)

    def test_03_buy_both_in_one_candle_picks_sl(self):
        """3. BUY chạm cả SL lẫn TP trong một nến: theo quy tắc bảo thủ phải tính SL (-1.0R)."""
        times = pd.date_range("2024-01-15 07:16:00", periods=2, freq="1min")
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2105.0],
            "high": [2102.0, 2130.0],  # Nến 07:17 quét lên 2130 >= TP (2120)
            "low": [2098.0, 2085.0],   # Nến 07:17 đồng thời quét xuống 2085 <= SL (2090)
            "close": [2101.0, 2110.0],
        })

        res = simulate_single_trade(
            direction="BUY",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2090.0,
            m1_bars=df_bars,
            spread=0.0,
            slip=0.0,
        )

        self.assertEqual(res["exit_reason"], "SL", "Chạm cả hai trong 1 nến phải ưu tiên tính SL")
        self.assertEqual(res["exit_price"], 2090.0)
        self.assertAlmostEqual(res["pnl_r"], -1.0, places=6)

    def test_04_buy_time_exit_at_1600(self):
        """4. BUY thoát lúc 16:00 UTC: nến đầu tiên có giờ >= 16:00 thoát ở giá mở, lý do TIME."""
        times = [
            pd.Timestamp("2024-01-15 07:16:00"),
            pd.Timestamp("2024-01-15 12:00:00"),
            pd.Timestamp("2024-01-15 16:00:00"),  # Nến 16:00 UTC
        ]
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2105.0, 2112.0],
            "high": [2104.0, 2110.0, 2115.0],
            "low": [2096.0, 2095.0, 2108.0],
            "close": [2102.0, 2108.0, 2110.0],
        })

        res = simulate_single_trade(
            direction="BUY",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2090.0,
            m1_bars=df_bars,
            spread=0.0,
            slip=0.0,
        )

        self.assertEqual(res["exit_reason"], "TIME")
        self.assertEqual(res["exit_price"], 2112.0, "Phải thoát ở giá mở của nến 16:00")
        self.assertEqual(pd.Timestamp(res["exit_time"]), pd.Timestamp("2024-01-15 16:00:00"))
        # pnl_R = (2112 - 2100) / 10 = +1.2R
        self.assertAlmostEqual(res["pnl_r"], 1.2, places=6)

    def test_05_buy_gap_over_sl(self):
        """5. BUY gap qua SL: nến tiếp theo mở cửa dưới SL thoát ở giá mở, lý do SL_GAP."""
        times = pd.date_range("2024-01-15 07:16:00", periods=2, freq="1min")
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2085.0],  # Nến 07:17 mở cửa gap xuống 2085 <= SL (2090)
            "high": [2102.0, 2088.0],
            "low": [2095.0, 2080.0],
            "close": [2096.0, 2082.0],
        })

        res = simulate_single_trade(
            direction="BUY",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2090.0,
            m1_bars=df_bars,
            spread=0.0,
            slip=0.0,
        )

        self.assertEqual(res["exit_reason"], "SL_GAP")
        self.assertEqual(res["exit_price"], 2085.0, "Thoát ở giá mở khi bị gap")
        # pnl_R = (2085 - 2100) / 10 = -1.5R
        self.assertAlmostEqual(res["pnl_r"], -1.5, places=6)

    def test_06_buy_entry_candle_touches_sl(self):
        """6. BUY nến vào lệnh đã chạm SL: ngay chính nến vào lệnh (i=0) có low chạm SL."""
        times = [pd.Timestamp("2024-01-15 07:16:00"), pd.Timestamp("2024-01-15 07:17:00")]
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2088.0],
            "high": [2102.0, 2095.0],
            "low": [2089.0, 2080.0],  # Ngay nến 07:16 có low = 2089 <= SL (2090)
            "close": [2092.0, 2085.0],
        })

        res = simulate_single_trade(
            direction="BUY",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2090.0,
            m1_bars=df_bars,
            spread=0.0,
            slip=0.0,
        )

        self.assertEqual(res["exit_reason"], "SL")
        self.assertEqual(res["exit_price"], 2090.0)
        self.assertEqual(pd.Timestamp(res["exit_time"]), pd.Timestamp("2024-01-15 07:16:00"))
        self.assertAlmostEqual(res["pnl_r"], -1.0, places=6)

    def test_07_buy_data_end_before_1600(self):
        """7. BUY hết dữ liệu trước 16:00: thoát ở giá đóng cuối, lý do DATA_END."""
        times = [
            pd.Timestamp("2024-01-15 07:16:00"),
            pd.Timestamp("2024-01-15 10:00:00"),
            pd.Timestamp("2024-01-15 14:30:00"),  # Nến cuối cùng trong ngày trước 16:00
        ]
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2102.0, 2105.0],
            "high": [2104.0, 2108.0, 2112.0],
            "low": [2096.0, 2098.0, 2101.0],
            "close": [2101.0, 2106.0, 2108.0],  # Close cuối = 2108
        })

        res = simulate_single_trade(
            direction="BUY",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2090.0,
            m1_bars=df_bars,
            spread=0.0,
            slip=0.0,
        )

        self.assertEqual(res["exit_reason"], "DATA_END")
        self.assertEqual(res["exit_price"], 2108.0, "Thoát ở giá đóng nến cuối")
        self.assertEqual(pd.Timestamp(res["exit_time"]), pd.Timestamp("2024-01-15 14:30:00"))
        # pnl_R = (2108 - 2100) / 10 = +0.8R
        self.assertAlmostEqual(res["pnl_r"], 0.8, places=6)

    # ==========================================
    # CÁC TEST SELL VỚI CHI PHÍ BẰNG 0
    # Entry = 2100, SL = 2110 -> R_dist = 10, TP = 2080
    # SELL kích hoạt theo ask (bid + spread)
    # ==========================================

    def test_08_sell_tp_zero_cost(self):
        """8. SELL chạm TP: SELL vào 2100, SL 2110 -> R_dist = 10, TP = 2080. Chạm TP ra đúng +2.0R."""
        times = pd.date_range("2024-01-15 07:16:00", periods=3, freq="1min")
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2095.0, 2085.0],
            "high": [2102.0, 2098.0, 2088.0],
            "low": [2096.0, 2088.0, 2075.0],  # Nến 07:18 có low = 2075 <= TP (2080)
            "close": [2098.0, 2090.0, 2078.0],
        })

        res = simulate_single_trade(
            direction="SELL",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2110.0,
            m1_bars=df_bars,
            spread=0.0,
            slip=0.0,
        )

        self.assertEqual(res["direction"], "SELL")
        self.assertEqual(res["entry_price"], 2100.0)
        self.assertEqual(res["sl"], 2110.0)
        self.assertEqual(res["r_dist"], 10.0)
        self.assertEqual(res["tp"], 2080.0)
        self.assertEqual(res["exit_reason"], "TP")
        self.assertEqual(res["exit_price"], 2080.0)
        self.assertEqual(pd.Timestamp(res["exit_time"]), pd.Timestamp("2024-01-15 07:18:00"))
        # pnl_R = (2100 - 2080) / 10 = +2.0R
        self.assertAlmostEqual(res["pnl_r"], 2.0, places=6)

    def test_09_sell_sl_zero_cost(self):
        """9. SELL chạm SL: SELL vào 2100, SL 2110 -> R_dist = 10. Chạm SL ra đúng -1.0R."""
        times = pd.date_range("2024-01-15 07:16:00", periods=3, freq="1min")
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2104.0, 2108.0],
            "high": [2102.0, 2106.0, 2112.0],  # Nến 07:18 có high = 2112 >= SL (2110)
            "low": [2098.0, 2101.0, 2105.0],
            "close": [2101.0, 2105.0, 2111.0],
        })

        res = simulate_single_trade(
            direction="SELL",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2110.0,
            m1_bars=df_bars,
            spread=0.0,
            slip=0.0,
        )

        self.assertEqual(res["exit_reason"], "SL")
        self.assertEqual(res["exit_price"], 2110.0)
        self.assertEqual(pd.Timestamp(res["exit_time"]), pd.Timestamp("2024-01-15 07:18:00"))
        # pnl_R = (2100 - 2110) / 10 = -1.0R
        self.assertAlmostEqual(res["pnl_r"], -1.0, places=6)

    def test_10_sell_both_in_one_candle_picks_sl(self):
        """10. SELL chạm cả SL lẫn TP trong 1 nến: theo quy tắc bảo thủ phải tính SL (-1.0R)."""
        times = pd.date_range("2024-01-15 07:16:00", periods=2, freq="1min")
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2095.0],
            "high": [2102.0, 2115.0],  # Quét lên 2115 >= SL (2110)
            "low": [2097.0, 2075.0],   # Đồng thời quét xuống 2075 <= TP (2080)
            "close": [2099.0, 2090.0],
        })

        res = simulate_single_trade(
            direction="SELL",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2110.0,
            m1_bars=df_bars,
            spread=0.0,
            slip=0.0,
        )

        self.assertEqual(res["exit_reason"], "SL", "Chạm cả hai trong 1 nến phải ưu tiên tính SL")
        self.assertEqual(res["exit_price"], 2110.0)
        self.assertAlmostEqual(res["pnl_r"], -1.0, places=6)

    def test_11_sell_time_exit_at_1600(self):
        """11. SELL thoát lúc 16:00 UTC: nến đầu tiên có giờ >= 16:00 thoát ở giá mở, lý do TIME."""
        times = [
            pd.Timestamp("2024-01-15 07:16:00"),
            pd.Timestamp("2024-01-15 12:00:00"),
            pd.Timestamp("2024-01-15 16:00:00"),
        ]
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2096.0, 2092.0],
            "high": [2103.0, 2100.0, 2096.0],
            "low": [2095.0, 2090.0, 2088.0],
            "close": [2098.0, 2094.0, 2090.0],
        })

        res = simulate_single_trade(
            direction="SELL",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2110.0,
            m1_bars=df_bars,
            spread=0.0,
            slip=0.0,
        )

        self.assertEqual(res["exit_reason"], "TIME")
        self.assertEqual(res["exit_price"], 2092.0)
        self.assertEqual(pd.Timestamp(res["exit_time"]), pd.Timestamp("2024-01-15 16:00:00"))
        # pnl_R = (2100 - 2092) / 10 = +0.8R
        self.assertAlmostEqual(res["pnl_r"], 0.8, places=6)

    def test_12_sell_gap_over_sl(self):
        """12. SELL gap qua SL: nến tiếp theo mở cửa trên SL theo ask thoát ở giá mở, lý do SL_GAP."""
        times = pd.date_range("2024-01-15 07:16:00", periods=2, freq="1min")
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2115.0],  # Nến 07:17 mở cửa gap lên 2115 >= SL (2110)
            "high": [2102.0, 2118.0],
            "low": [2098.0, 2112.0],
            "close": [2101.0, 2116.0],
        })

        res = simulate_single_trade(
            direction="SELL",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2110.0,
            m1_bars=df_bars,
            spread=0.0,
            slip=0.0,
        )

        self.assertEqual(res["exit_reason"], "SL_GAP")
        self.assertEqual(res["exit_price"], 2115.0)
        # pnl_R = (2100 - 2115) / 10 = -1.5R
        self.assertAlmostEqual(res["pnl_r"], -1.5, places=6)

    def test_13_sell_entry_candle_touches_sl(self):
        """13. SELL nến vào lệnh đã chạm SL: ngay chính nến vào lệnh (i=0) có high chạm SL."""
        times = [pd.Timestamp("2024-01-15 07:16:00"), pd.Timestamp("2024-01-15 07:17:00")]
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2108.0],
            "high": [2111.0, 2115.0],  # Ngay nến 07:16 có high = 2111 >= SL (2110)
            "low": [2098.0, 2105.0],
            "close": [2106.0, 2112.0],
        })

        res = simulate_single_trade(
            direction="SELL",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2110.0,
            m1_bars=df_bars,
            spread=0.0,
            slip=0.0,
        )

        self.assertEqual(res["exit_reason"], "SL")
        self.assertEqual(res["exit_price"], 2110.0)
        self.assertEqual(pd.Timestamp(res["exit_time"]), pd.Timestamp("2024-01-15 07:16:00"))
        self.assertAlmostEqual(res["pnl_r"], -1.0, places=6)

    def test_14_sell_data_end_before_1600(self):
        """14. SELL hết dữ liệu trước 16:00: thoát ở giá đóng cuối, lý do DATA_END."""
        times = [
            pd.Timestamp("2024-01-15 07:16:00"),
            pd.Timestamp("2024-01-15 10:00:00"),
            pd.Timestamp("2024-01-15 14:30:00"),
        ]
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2098.0, 2096.0],
            "high": [2103.0, 2101.0, 2099.0],
            "low": [2096.0, 2094.0, 2092.0],
            "close": [2099.0, 2097.0, 2095.0],  # Close cuối = 2095
        })

        res = simulate_single_trade(
            direction="SELL",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2110.0,
            m1_bars=df_bars,
            spread=0.0,
            slip=0.0,
        )

        self.assertEqual(res["exit_reason"], "DATA_END")
        self.assertEqual(res["exit_price"], 2095.0)
        self.assertEqual(pd.Timestamp(res["exit_time"]), pd.Timestamp("2024-01-15 14:30:00"))
        # pnl_R = (2100 - 2095) / 10 = +0.5R
        self.assertAlmostEqual(res["pnl_r"], 0.5, places=6)

    # ==========================================
    # CÁC TEST VỚI CHI PHÍ KHÁC 0
    # TP trả đúng ít hơn 2R một lượng bằng chi phí quy ra R
    # ==========================================

    def test_15_nonzero_costs_reduction_exact(self):
        """15. Với chi phí khác 0: TP trả đúng ít hơn 2R một lượng bằng chi phí trượt giá quy ra R (2.0 - slip/R_dist)."""
        spread = 0.40  # 40 points
        slip = 0.05    # 5 points

        # --- A) BUY ---
        # entry_open = 2100.0, SL = 2090.0
        # entry_price = 2100.0 + 0.40 + 0.05 = 2100.45
        # R_dist = 2100.45 - 2090.0 = 10.45
        # TP = 2100.45 + 2 * 10.45 = 2121.35
        # Chạm TP -> exit_price = TP - slip = 2121.35 - 0.05 = 2121.30
        # pnl_R = (2121.30 - 2100.45) / 10.45 = 20.85 / 10.45 = 2.0 - 0.05 / 10.45
        times = pd.date_range("2024-01-15 07:16:00", periods=2, freq="1min")
        df_buy = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2110.0],
            "high": [2105.0, 2125.0],  # high 2125.0 >= TP (2121.35)
            "low": [2098.0, 2108.0],
            "close": [2102.0, 2122.0],
        })

        res_buy = simulate_single_trade(
            direction="BUY",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2090.0,
            m1_bars=df_buy,
            spread=spread,
            slip=slip,
        )

        expected_r_dist_buy = 10.45
        expected_pnl_buy = 2.0 - (slip / expected_r_dist_buy)
        self.assertAlmostEqual(res_buy["entry_price"], 2100.45, places=6)
        self.assertAlmostEqual(res_buy["r_dist"], expected_r_dist_buy, places=6)
        self.assertAlmostEqual(res_buy["tp"], 2121.35, places=6)
        self.assertAlmostEqual(res_buy["exit_price"], 2121.30, places=6)
        self.assertAlmostEqual(res_buy["pnl_r"], expected_pnl_buy, places=6)
        self.assertLess(res_buy["pnl_r"], 2.0, "TP sau chi phí phải nhỏ hơn đúng 2.0R")

        # --- B) SELL ---
        # entry_open = 2100.0, SL = 2110.0
        # entry_price = 2100.0 - 0.05 = 2099.95
        # R_dist = 2110.0 - 2099.95 = 10.05
        # TP = 2099.95 - 2 * 10.05 = 2079.85
        # SELL kích hoạt theo ask: bid low + spread <= TP -> bid low <= 2079.85 - 0.40 = 2079.45
        # Chạm TP -> exit_price = TP + slip = 2079.85 + 0.05 = 2079.90
        # pnl_R = (2099.95 - 2079.90) / 10.05 = 20.05 / 10.05 = 2.0 - 0.05 / 10.05
        df_sell = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2090.0],
            "high": [2102.0, 2092.0],
            "low": [2095.0, 2075.0],  # bid low = 2075.0, ask low = 2075.40 <= TP (2079.85)
            "close": [2098.0, 2078.0],
        })

        res_sell = simulate_single_trade(
            direction="SELL",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2110.0,
            m1_bars=df_sell,
            spread=spread,
            slip=slip,
        )

        expected_r_dist_sell = 10.05
        expected_pnl_sell = 2.0 - (slip / expected_r_dist_sell)
        self.assertAlmostEqual(res_sell["entry_price"], 2099.95, places=6)
        self.assertAlmostEqual(res_sell["r_dist"], expected_r_dist_sell, places=6)
        self.assertAlmostEqual(res_sell["tp"], 2079.85, places=6)
        self.assertAlmostEqual(res_sell["exit_price"], 2079.90, places=6)
        self.assertAlmostEqual(res_sell["pnl_r"], expected_pnl_sell, places=6)
        self.assertLess(res_sell["pnl_r"], 2.0, "TP sau chi phí phải nhỏ hơn đúng 2.0R")

    # ==========================================
    # QUY ƯỚC BẤT ĐỐI XỨNG GAP TP
    # ==========================================

    def test_16_tp_gap_no_extra_reward_conservative(self):
        """16. Bất đối xứng gap: Gap qua TP KHÔNG thưởng thêm, chỉ lấy đúng mức TP (bảo thủ)."""
        # BUY: TP = 2120. Nến sau mở cửa nhảy vọt qua TP tại 2135.0
        times = pd.date_range("2024-01-15 07:16:00", periods=2, freq="1min")
        df_buy_gap = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2135.0],  # Gap mở cửa vượt xa TP (2120)
            "high": [2104.0, 2140.0],
            "low": [2098.0, 2130.0],
            "close": [2102.0, 2138.0],
        })

        res_buy = simulate_single_trade(
            direction="BUY",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2090.0,
            m1_bars=df_buy_gap,
            spread=0.0,
            slip=0.0,
        )

        self.assertEqual(res_buy["exit_reason"], "TP")
        self.assertEqual(res_buy["exit_price"], 2120.0, "Không được lấy giá mở 2135.0, chỉ lấy đúng TP")
        self.assertAlmostEqual(res_buy["pnl_r"], 2.0, places=6)

        # SELL: TP = 2080. Nến sau mở cửa nhảy vọt qua TP xuống 2065.0
        df_sell_gap = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2065.0],  # Gap mở cửa rơi sâu qua TP (2080)
            "high": [2102.0, 2070.0],
            "low": [2096.0, 2060.0],
            "close": [2098.0, 2068.0],
        })

        res_sell = simulate_single_trade(
            direction="SELL",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2110.0,
            m1_bars=df_sell_gap,
            spread=0.0,
            slip=0.0,
        )

        self.assertEqual(res_sell["exit_reason"], "TP")
        self.assertEqual(res_sell["exit_price"], 2080.0, "Không được lấy giá mở 2065.0, chỉ lấy đúng TP")
        self.assertAlmostEqual(res_sell["pnl_r"], 2.0, places=6)

    # ==========================================
    # TEST SIMULATE_TRADES VÀ KIỂM ĐẾM
    # ==========================================

    def test_17_data_end_counting_and_summary(self):
        """17. Kiểm đếm DATA_END và các lý do thoát trong bảng summary của simulate_trades."""
        # Tạo 2 ngày có tín hiệu:
        # Ngày 1: Thoát TP
        # Ngày 2: Hết dữ liệu trước 16:00 (DATA_END)
        day1 = pd.Timestamp("2024-01-15")
        day2 = pd.Timestamp("2024-01-16")

        signals = pd.DataFrame([
            {
                "date": day1,
                "direction": "BUY",
                "signal_time": pd.Timestamp("2024-01-15 07:15:00"),
                "entry_time": pd.Timestamp("2024-01-15 07:16:00"),
                "entry_price": 2100.0,
                "asia_high": 2095.0,
                "asia_low": 2090.0,  # SL = 2090 -> R_dist = 10, TP = 2120
            },
            {
                "date": day2,
                "direction": "SELL",
                "signal_time": pd.Timestamp("2024-01-16 08:00:00"),
                "entry_time": pd.Timestamp("2024-01-16 08:01:00"),
                "entry_price": 2100.0,
                "asia_high": 2110.0,  # SL = 2110 -> R_dist = 10, TP = 2080
                "asia_low": 2095.0,
            },
        ])

        # Tạo nến M1
        bars_day1 = pd.DataFrame({
            "time_utc": pd.date_range("2024-01-15 07:16:00", periods=3, freq="1min"),
            "open": [2100.0, 2105.0, 2115.0],
            "high": [2104.0, 2112.0, 2125.0],  # Chạm TP 2120
            "low": [2098.0, 2102.0, 2110.0],
            "close": [2102.0, 2110.0, 2122.0],
        })

        bars_day2 = pd.DataFrame({
            "time_utc": pd.date_range("2024-01-16 08:01:00", periods=2, freq="1min"),
            "open": [2100.0, 2098.0],
            "high": [2102.0, 2100.0],
            "low": [2097.0, 2095.0],
            "close": [2099.0, 2096.0],  # Hết dữ liệu trước 16:00 -> DATA_END
        })

        all_m1 = pd.concat([bars_day1, bars_day2], ignore_index=True)

        trades_df, summary = simulate_trades(
            signals,
            all_m1,
            fixed_spread=0.0,
            fixed_slip=0.0,
            return_summary=True,
        )

        self.assertEqual(len(trades_df), 2)
        self.assertEqual(summary["total_trades"], 2)
        self.assertEqual(summary["n_tp"], 1)
        self.assertEqual(summary["n_data_end"], 1, "Phải đếm đúng 1 lệnh DATA_END")
        self.assertEqual(trades_df.iloc[0]["exit_reason"], "TP")
        self.assertEqual(trades_df.iloc[1]["exit_reason"], "DATA_END")
        self.assertIn("n_data_end", summary)

    def test_18_simulate_trades_real_data_pipeline(self):
        """18. Pipeline thực tế: load_m1 -> compute_asian_range -> generate_breakout_signals -> simulate_trades."""
        # Nạp dữ liệu thực tế ngày 2024-01-02
        m1 = load_m1("2024-01-02", "2024-01-02")
        asian = compute_asian_range(m1)
        sigs = generate_breakout_signals(m1, asian_ranges=asian)

        self.assertFalse(sigs.empty, "Ngày 2024-01-02 có tín hiệu Breakout")

        # Mô phỏng khớp lệnh với thông số mặc định (spread theo năm 2024, slip = 5 points)
        trades = simulate_trades(sigs, m1)
        self.assertEqual(len(trades), len(sigs))

        trade = trades.iloc[0]
        # Kiểm tra đầy đủ các cột đầu ra theo yêu cầu Bước 4
        expected_cols = [
            "date",
            "direction",
            "entry_time",
            "entry_price",
            "exit_time",
            "exit_price",
            "sl",
            "tp",
            "r_dist",
            "exit_reason",
            "pnl_r",
            "spread",
            "year",
        ]
        for col in expected_cols:
            self.assertIn(col, trades.columns)

        self.assertEqual(trade["direction"], "BUY")
        self.assertEqual(trade["year"], 2024)
        self.assertGreater(trade["spread"], 0.0)
        self.assertGreater(trade["r_dist"], 0.0)
        self.assertIn(trade["exit_reason"], ["TP", "SL", "SL_GAP", "TIME", "DATA_END"])


if __name__ == "__main__":
    unittest.main()
