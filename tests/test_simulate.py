"""Kiểm thử mô-đun simulate.py (Bước 4: Mô phỏng khớp lệnh và quản lý rủi ro R-multiple):

1. BUY chạm TP (chi phí = 0): ra đúng +2.0R.
2. BUY chạm SL (chi phí = 0): ra đúng -1.0R.
3. BUY chạm cả SL lẫn TP trong 1 nến: tính SL (-1.0R).
4. BUY thoát lúc 16:00 UTC: ra đúng lý do TIME tại giá mở.
5. BUY gap qua SL: ra đúng lý do SL_GAP tại giá mở.
6. BUY chính nến vào lệnh đã chạm SL: ra đúng SL ngay nến vào lệnh.
7. BUY hết dữ liệu trước 16:00: ra đúng lý do DATA_END tại giá đóng cuối.
8. SELL chạm TP (chi phí = 0): ra đúng +2.0R.
9. SELL chạm SL (chi phí = 0): kích hoạt theo ask, ra đúng -1.0R.
10. SELL chạm cả SL lẫn TP trong 1 nến: tính SL (-1.0R).
11. SELL thoát lúc 16:00 UTC: ra đúng lý do TIME tại giá mở.
12. SELL gap qua SL: kích hoạt theo ask, ra đúng lý do SL_GAP tại giá mở.
13. SELL chính nến vào lệnh đã chạm SL: ra đúng SL ngay nến vào lệnh.
14. SELL hết dữ liệu trước 16:00: ra đúng lý do DATA_END tại giá đóng cuối.
15. Chi phí khác 0 (BUY):
    - BUY chạm TP: ra đúng +1.9952R (2.0 - slip / R_dist).
    - BUY chạm SL: ra đúng -1.0048R (-1.0 - slip / R_dist).
16. Chi phí khác 0 (SELL):
    - SELL chạm TP: ra đúng +1.9950R (2.0 - slip / R_dist).
    - SELL chạm SL: ra đúng -1.0050R (-1.0 - slip / R_dist).
17. Bất đối xứng gap: Gap qua TP KHÔNG thưởng thêm, chỉ lấy đúng mức TP (bảo thủ).
18. Nhánh M1Data trong simulate_single_trade: chạy mượt mà không lỗi AttributeError.
19. Nguồn spread cố định: YEARLY_MEDIAN_SPREADS báo lỗi KeyError nếu thiếu năm.
20. Giữ cột đối chiếu biểu đồ (signal_time, entry_delay, atr, r_atr_ratio, asia_high/low) và đếm n_skipped.
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

from src.backtest.data import M1Data
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
        """1. BUY chạm TP (chi phí 0): ra đúng +2.0R."""
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
        """2. BUY chạm SL (chi phí 0): ra đúng -1.0R."""
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
        """3. BUY chạm cả SL lẫn TP trong một nến: tính SL (-1.0R)."""
        times = pd.date_range("2024-01-15 07:16:00", periods=2, freq="1min")
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2105.0],
            "high": [2102.0, 2130.0],  # Quét lên 2130 >= TP (2120)
            "low": [2098.0, 2085.0],   # Đồng thời quét xuống 2085 <= SL (2090)
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

        self.assertEqual(res["exit_reason"], "SL")
        self.assertEqual(res["exit_price"], 2090.0)
        self.assertAlmostEqual(res["pnl_r"], -1.0, places=6)

    def test_04_buy_time_exit_at_1600(self):
        """4. BUY thoát lúc 16:00 UTC: thoát tại giá mở nến 16:00 (+1.2R)."""
        times = [
            pd.Timestamp("2024-01-15 07:16:00"),
            pd.Timestamp("2024-01-15 12:00:00"),
            pd.Timestamp("2024-01-15 16:00:00"),
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
        self.assertEqual(res["exit_price"], 2112.0)
        self.assertEqual(pd.Timestamp(res["exit_time"]), pd.Timestamp("2024-01-15 16:00:00"))
        # pnl_R = (2112 - 2100) / 10 = +1.2R
        self.assertAlmostEqual(res["pnl_r"], 1.2, places=6)

    def test_05_buy_gap_over_sl(self):
        """5. BUY gap qua SL: thoát tại giá mở (-1.5R)."""
        times = pd.date_range("2024-01-15 07:16:00", periods=2, freq="1min")
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2085.0],  # Mở cửa gap xuống 2085 <= SL (2090)
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
        self.assertEqual(res["exit_price"], 2085.0)
        # pnl_R = (2085 - 2100) / 10 = -1.5R
        self.assertAlmostEqual(res["pnl_r"], -1.5, places=6)

    def test_06_buy_entry_candle_touches_sl(self):
        """6. BUY chính nến vào lệnh đã chạm SL: ra đúng SL ngay nến vào lệnh (-1.0R)."""
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
        """7. BUY hết dữ liệu trước 16:00: thoát tại giá đóng cuối (+0.8R)."""
        times = [
            pd.Timestamp("2024-01-15 07:16:00"),
            pd.Timestamp("2024-01-15 10:00:00"),
            pd.Timestamp("2024-01-15 14:30:00"),
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
        self.assertEqual(res["exit_price"], 2108.0)
        self.assertEqual(pd.Timestamp(res["exit_time"]), pd.Timestamp("2024-01-15 14:30:00"))
        # pnl_R = (2108 - 2100) / 10 = +0.8R
        self.assertAlmostEqual(res["pnl_r"], 0.8, places=6)

    # ==========================================
    # CÁC TEST SELL VỚI CHI PHÍ BẰNG 0
    # Entry = 2100, SL = 2110 -> R_dist = 10, TP = 2080
    # ==========================================

    def test_08_sell_tp_zero_cost(self):
        """8. SELL chạm TP (chi phí 0): ra đúng +2.0R."""
        times = pd.date_range("2024-01-15 07:16:00", periods=3, freq="1min")
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2095.0, 2085.0],
            "high": [2102.0, 2098.0, 2088.0],
            "low": [2096.0, 2088.0, 2075.0],  # low = 2075 <= TP (2080)
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

        self.assertEqual(res["exit_reason"], "TP")
        self.assertEqual(res["exit_price"], 2080.0)
        self.assertEqual(pd.Timestamp(res["exit_time"]), pd.Timestamp("2024-01-15 07:18:00"))
        # pnl_R = (2100 - 2080) / 10 = +2.0R
        self.assertAlmostEqual(res["pnl_r"], 2.0, places=6)

    def test_09_sell_sl_zero_cost(self):
        """9. SELL chạm SL (chi phí 0): ra đúng -1.0R."""
        times = pd.date_range("2024-01-15 07:16:00", periods=3, freq="1min")
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2104.0, 2108.0],
            "high": [2102.0, 2106.0, 2112.0],  # high = 2112 >= SL (2110)
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
        """10. SELL chạm cả SL lẫn TP trong 1 nến: tính SL (-1.0R)."""
        times = pd.date_range("2024-01-15 07:16:00", periods=2, freq="1min")
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2095.0],
            "high": [2102.0, 2115.0],  # Quét lên 2115 >= SL (2110)
            "low": [2097.0, 2075.0],   # Quét xuống 2075 <= TP (2080)
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

        self.assertEqual(res["exit_reason"], "SL")
        self.assertEqual(res["exit_price"], 2110.0)
        self.assertAlmostEqual(res["pnl_r"], -1.0, places=6)

    def test_11_sell_time_exit_at_1600(self):
        """11. SELL thoát lúc 16:00 UTC: thoát tại giá mở (+0.8R)."""
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
        """12. SELL gap qua SL: thoát tại giá mở (-1.5R)."""
        times = pd.date_range("2024-01-15 07:16:00", periods=2, freq="1min")
        df_bars = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2115.0],  # Mở cửa gap lên 2115 >= SL (2110)
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
        """13. SELL nến vào lệnh đã chạm SL: ra đúng SL ngay nến vào lệnh (-1.0R)."""
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
        """14. SELL hết dữ liệu trước 16:00: thoát tại giá đóng cuối (+0.5R)."""
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
    # spread = 0.40, slip = 0.05
    # BUY: entry = 2100.45, R_dist = 10.45
    #      TP cho +1.9952R (2.0 - 0.05/10.45)
    #      SL cho -1.0048R (-1.0 - 0.05/10.45)
    # SELL: entry = 2099.95, R_dist = 10.05
    #      TP cho +1.9950R (2.0 - 0.05/10.05)
    #      SL cho -1.0050R (-1.0 - 0.05/10.05)
    # ==========================================

    def test_15_nonzero_costs_buy_tp_and_sl(self):
        """15. BUY có chi phí: TP cho đúng +1.9952R và SL cho đúng -1.0048R."""
        spread = 0.40
        slip = 0.05

        # A) BUY chạm TP
        times = pd.date_range("2024-01-15 07:16:00", periods=2, freq="1min")
        df_buy_tp = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2110.0],
            "high": [2105.0, 2125.0],  # 2125 >= TP (2121.35)
            "low": [2098.0, 2108.0],
            "close": [2102.0, 2122.0],
        })
        res_tp = simulate_single_trade(
            direction="BUY",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2090.0,
            m1_bars=df_buy_tp,
            spread=spread,
            slip=slip,
        )
        # R_dist = 2100.45 - 2090.0 = 10.45
        # TP = 2121.35 -> exit = 2121.30 -> pnl = (2121.30 - 2100.45)/10.45 = 1.9952153...
        self.assertAlmostEqual(res_tp["pnl_r"], 1.9952, places=4)
        self.assertAlmostEqual(res_tp["pnl_r"], 2.0 - (slip / 10.45), places=6)

        # B) BUY chạm SL
        df_buy_sl = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2095.0],
            "high": [2102.0, 2096.0],
            "low": [2096.0, 2088.0],  # low = 2088 <= SL (2090)
            "close": [2098.0, 2089.0],
        })
        res_sl = simulate_single_trade(
            direction="BUY",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2090.0,
            m1_bars=df_buy_sl,
            spread=spread,
            slip=slip,
        )
        # exit = 2090 - 0.05 = 2089.95 -> pnl = (2089.95 - 2100.45)/10.45 = -10.50/10.45 = -1.004784...
        self.assertAlmostEqual(res_sl["pnl_r"], -1.0048, places=4)
        self.assertAlmostEqual(res_sl["pnl_r"], -1.0 - (slip / 10.45), places=6)

    def test_16_nonzero_costs_sell_tp_and_sl(self):
        """16. SELL có chi phí: TP cho đúng +1.9950R và SL cho đúng -1.0050R."""
        spread = 0.40
        slip = 0.05

        # A) SELL chạm TP
        times = pd.date_range("2024-01-15 07:16:00", periods=2, freq="1min")
        df_sell_tp = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2090.0],
            "high": [2102.0, 2092.0],
            "low": [2095.0, 2075.0],  # ask low = 2075.40 <= TP (2079.85)
            "close": [2098.0, 2078.0],
        })
        res_tp = simulate_single_trade(
            direction="SELL",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2110.0,
            m1_bars=df_sell_tp,
            spread=spread,
            slip=slip,
        )
        # R_dist = 2110.0 - 2099.95 = 10.05
        # TP = 2079.85 -> exit = 2079.90 -> pnl = (2099.95 - 2079.90)/10.05 = 20.05/10.05 = 1.9950248...
        self.assertAlmostEqual(res_tp["pnl_r"], 1.9950, places=4)
        self.assertAlmostEqual(res_tp["pnl_r"], 2.0 - (slip / 10.05), places=6)

        # B) SELL chạm SL
        df_sell_sl = pd.DataFrame({
            "time_utc": times,
            "open": [2100.0, 2105.0],
            "high": [2102.0, 2112.0],  # ask high = 2112.40 >= SL (2110.0)
            "low": [2098.0, 2104.0],
            "close": [2100.0, 2111.0],
        })
        res_sl = simulate_single_trade(
            direction="SELL",
            entry_time=self.entry_time,
            entry_open=2100.0,
            sl=2110.0,
            m1_bars=df_sell_sl,
            spread=spread,
            slip=slip,
        )
        # exit = 2110 + 0.05 = 2110.05 -> pnl = (2099.95 - 2110.05)/10.05 = -10.10/10.05 = -1.004975...
        self.assertAlmostEqual(res_sl["pnl_r"], -1.0050, places=4)
        self.assertAlmostEqual(res_sl["pnl_r"], -1.0 - (slip / 10.05), places=6)

    def test_17_tp_gap_no_extra_reward_conservative(self):
        """17. Bất đối xứng gap: Gap qua TP KHÔNG thưởng thêm, chỉ lấy đúng mức TP (bảo thủ)."""
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

    # ==========================================
    # TEST CÁC LỖI & CẢI TIẾN ĐƯỢC NGƯỜI DÙNG YÊU CẦU
    # ==========================================

    def test_18_m1data_instance_branch_no_attribute_error(self):
        """18. Nhánh M1Data trong simulate_single_trade chạy mượt mà, không bị AttributeError (time_series.dt.tz)."""
        times = pd.date_range("2024-01-15 07:16:00", periods=3, freq="1min").to_numpy()
        opens = np.array([2100.0, 2105.0, 2115.0], dtype=np.float64)
        highs = np.array([2104.0, 2112.0, 2125.0], dtype=np.float64)
        lows = np.array([2098.0, 2102.0, 2110.0], dtype=np.float64)
        closes = np.array([2102.0, 2110.0, 2122.0], dtype=np.float64)

        m1_obj = M1Data(
            time=times,
            open=opens,
            high=highs,
            low=lows,
            close=closes,
        )

        # Gọi trực tiếp với M1Data instance (trước đây time_series.dt.tz bị vỡ nếu time_series là DatetimeIndex)
        res = simulate_single_trade(
            direction="BUY",
            entry_time="2024-01-15 07:16:00",
            entry_open=2100.0,
            sl=2090.0,
            m1_bars=m1_obj,
            spread=0.0,
            slip=0.0,
        )

        self.assertEqual(res["exit_reason"], "TP")
        self.assertAlmostEqual(res["pnl_r"], 2.0, places=6)

    def test_19_yearly_spread_fixed_source_and_missing_year_error(self):
        """19. get_effective_spread dùng YEARLY_MEDIAN_SPREADS cố định và báo KeyError nếu thiếu năm."""
        # Năm 2024 có trong bảng (med = 31) -> 31 * 1.25 * 0.01 = 0.3875
        spread_2024 = get_effective_spread(2024)
        self.assertAlmostEqual(spread_2024, 0.3875, places=6)

        # Năm 2010 không có trong DATA.md -> phải raise KeyError rõ ràng, không âm thầm fallback
        with self.assertRaises(KeyError) as ctx:
            get_effective_spread(2010)
        self.assertIn("YEARLY_MEDIAN_SPREADS", str(ctx.exception))

    def test_20_extended_columns_and_n_skipped_summary(self):
        """20. simulate_trades giữ đầy đủ các cột tín hiệu đối chiếu biểu đồ và thống kê n_skipped."""
        day1 = pd.Timestamp("2024-01-15")
        day2_skipped = pd.Timestamp("2024-01-16")  # Ngày không có nến

        signals = pd.DataFrame([
            {
                "date": day1,
                "direction": "BUY",
                "signal_time": pd.Timestamp("2024-01-15 07:15:00"),
                "entry_time": pd.Timestamp("2024-01-15 07:16:00"),
                "entry_delay": 1,
                "entry_price": 2100.0,
                "atr": 10.0,
                "asia_high": 2095.0,
                "asia_low": 2090.0,
            },
            {
                "date": day2_skipped,
                "direction": "SELL",
                "signal_time": pd.Timestamp("2024-01-16 08:00:00"),
                "entry_time": pd.Timestamp("2024-01-16 08:01:00"),
                "entry_delay": 1,
                "entry_price": 2100.0,
                "atr": 10.0,
                "asia_high": 2110.0,
                "asia_low": 2095.0,
            },
        ])

        bars_day1 = pd.DataFrame({
            "time_utc": pd.date_range("2024-01-15 07:16:00", periods=2, freq="1min"),
            "open": [2100.0, 2115.0],
            "high": [2104.0, 2125.0],
            "low": [2098.0, 2110.0],
            "close": [2102.0, 2122.0],
        })

        trades_df, summary = simulate_trades(
            signals,
            bars_day1,
            fixed_spread=0.0,
            fixed_slip=0.0,
            return_summary=True,
        )

        self.assertEqual(len(trades_df), 1)
        self.assertEqual(summary["total_trades"], 1)
        self.assertEqual(summary["n_skipped"], 1, "Phải đếm đúng 1 lệnh bị bỏ vì ngày 2 không có nến")

        # Kiểm tra đầy đủ các cột phục vụ dựng lại biểu đồ theo PROTOCOL.md
        required_cols = [
            "signal_time",
            "entry_delay",
            "asia_high",
            "asia_low",
            "atr",
            "r_atr_ratio",
        ]
        for col in required_cols:
            self.assertIn(col, trades_df.columns, f"Thiếu cột {col} trong kết quả")

        row = trades_df.iloc[0]
        self.assertEqual(pd.Timestamp(row["signal_time"]), pd.Timestamp("2024-01-15 07:15:00"))
        self.assertEqual(row["entry_delay"], 1)
        self.assertEqual(row["atr"], 10.0)
        self.assertAlmostEqual(row["r_atr_ratio"], 1.0, places=4)  # r_dist = 10, atr = 10 -> ratio = 1.0
        self.assertEqual(row["asia_high"], 2095.0)
        self.assertEqual(row["asia_low"], 2090.0)


if __name__ == "__main__":
    unittest.main()
