"""Bộ kiểm thử unit test tính toán thủ công (hand-calculated cases) cho mô-đun pnl.py (SMC v1).

Bao gồm:
1. Kiểm tra công thức R từng lệnh (BUY & SELL) với các lý do thoát: TP, SL, SL_GAP, TIME_EXIT.
2. Kiểm tra tính Max Drawdown theo R sắp xếp tuần tự theo thời điểm thoát lệnh (exit_time).
3. Kiểm tra các chỉ số tổng kết (Expectancy, Profit Factor, Win Rate) bằng các ca số tính tay.
4. Kiểm tra gán lệnh vào năm lịch theo năm vào lệnh (chuẩn C4) và năm thoát lệnh đối với các ca vắt qua năm.
5. Kiểm tra tái lập hoàn toàn của kiểm soát ngẫu nhiên Monte Carlo C4(c) với seed = 42.
"""

from __future__ import annotations

import unittest
import numpy as np
import pandas as pd

from src.backtest.smc.pnl import (
    attach_pnl_r,
    breakdown_by_year,
    compute_drawdown_series,
    compute_trade_r,
    run_monte_carlo_permutation,
    summarize_trades,
)


class TestSMCPnLHandCalculated(unittest.TestCase):
    def test_01_trade_r_formula_buy_hand_cases(self):
        """Kiểm tra công thức R cho lệnh BUY: R = (exit_price - entry_exec) / r_dist."""
        entry_exec = 2000.0
        r_dist = 10.0  # SL danh định tại 1990.0

        # Ca 1: Chạm TP (tp = 2020.0, thoát tại tp - slip = 2019.95)
        # R = (2019.95 - 2000.0) / 10.0 = +1.995 R
        exit_tp = 2019.95
        r_tp = compute_trade_r("BUY", entry_exec, exit_tp, r_dist)
        self.assertAlmostEqual(r_tp, 1.995, places=5)

        # Ca 2: Chạm SL (sl = 1990.0, thoát tại sl - slip = 1989.95)
        # R = (1989.95 - 2000.0) / 10.0 = -1.005 R
        exit_sl = 1989.95
        r_sl = compute_trade_r("BUY", entry_exec, exit_sl, r_dist)
        self.assertAlmostEqual(r_sl, -1.005, places=5)

        # Ca 3: Nhảy SL_GAP qua SL (Open = 1985.0, thoát tại open - slip = 1984.95)
        # R = (1984.95 - 2000.0) / 10.0 = -1.505 R
        exit_gap = 1984.95
        r_gap = compute_trade_r("BUY", entry_exec, exit_gap, r_dist)
        self.assertAlmostEqual(r_gap, -1.505, places=5)

        # Ca 4: Hết giờ TIME_EXIT tại mức lãi nhẹ (Open = 2005.0, thoát tại open - slip = 2004.95)
        # R = (2004.95 - 2000.0) / 10.0 = +0.495 R
        exit_time = 2004.95
        r_time = compute_trade_r("BUY", entry_exec, exit_time, r_dist)
        self.assertAlmostEqual(r_time, 0.495, places=5)

    def test_02_trade_r_formula_sell_hand_cases(self):
        """Kiểm tra công thức R cho lệnh SELL: R = (entry_exec - exit_price) / r_dist."""
        entry_exec = 2000.0
        r_dist = 10.0  # SL danh định tại 2010.0

        # Ca 1: Chạm TP (tp = 1980.0, thoát tại tp + slip = 1980.05)
        # R = (2000.0 - 1980.05) / 10.0 = +1.995 R
        exit_tp = 1980.05
        r_tp = compute_trade_r("SELL", entry_exec, exit_tp, r_dist)
        self.assertAlmostEqual(r_tp, 1.995, places=5)

        # Ca 2: Chạm SL (sl = 2010.0, thoát tại sl + slip = 2010.05)
        # R = (2000.0 - 2010.05) / 10.0 = -1.005 R
        exit_sl = 2010.05
        r_sl = compute_trade_r("SELL", entry_exec, exit_sl, r_dist)
        self.assertAlmostEqual(r_sl, -1.005, places=5)

        # Ca 3: Nhảy SL_GAP qua SL (Open = 2015.0, spread = 0.30, slip = 0.05 -> exit = 2015.35)
        # R = (2000.0 - 2015.35) / 10.0 = -1.535 R
        exit_gap = 2015.35
        r_gap = compute_trade_r("SELL", entry_exec, exit_gap, r_dist)
        self.assertAlmostEqual(r_gap, -1.535, places=5)

        # Ca 4: Hết giờ TIME_EXIT tại mức lãi nhẹ (Open = 1995.0, spread = 0.30, slip = 0.05 -> exit = 1995.35)
        # R = (2000.0 - 1995.35) / 10.0 = +0.465 R
        exit_time = 1995.35
        r_time = compute_trade_r("SELL", entry_exec, exit_time, r_dist)
        self.assertAlmostEqual(r_time, 0.465, places=5)

    def test_03_drawdown_hand_calculated(self):
        """Kiểm tra tính Max Drawdown theo thứ tự thời điểm thoát lệnh (exit_time)."""
        # Ca tính tay:
        # Lệnh:   T1   T2    T3    T4    T5   T6
        # PnL R: +1.0 +2.0  -1.5  -1.0  +0.5 +3.0
        # Vốn:    1.0  3.0   1.5   0.5   1.0  4.0
        # Đỉnh:   1.0  3.0   3.0   3.0   3.0  4.0
        # DD:     0.0  0.0   1.5   2.5   2.0  0.0 -> MDD = 2.5 R
        times = [
            pd.Timestamp("2024-01-01 10:00:00"),
            pd.Timestamp("2024-01-02 10:00:00"),
            pd.Timestamp("2024-01-03 10:00:00"),
            pd.Timestamp("2024-01-04 10:00:00"),
            pd.Timestamp("2024-01-05 10:00:00"),
            pd.Timestamp("2024-01-06 10:00:00"),
        ]
        pnls = [1.0, 2.0, -1.5, -1.0, 0.5, 3.0]

        df = pd.DataFrame({
            "direction": ["BUY"] * 6,
            "entry_exec": [2000.0] * 6,
            "exit_price": [2010.0, 2020.0, 1985.0, 1990.0, 2005.0, 2030.0],
            "r_dist": [10.0] * 6,
            "exit_time": times,
        })
        # Gán pnl_r thủ công để test
        df["pnl_r"] = pnls

        res = compute_drawdown_series(df, sort_by_exit=True)
        self.assertAlmostEqual(res["mdd_r"], 2.5, places=5)
        self.assertAlmostEqual(res["peak_r"], 4.0, places=5)
        np.testing.assert_allclose(res["drawdown_series"], [0.0, 0.0, 1.5, 2.5, 2.0, 0.0])

        # Thử đảo xáo trộn thứ tự dòng trong df, kiểm tra hàm sort_by_exit tự động sắp lại đúng
        df_shuffled = df.sample(frac=1.0, random_state=123).reset_index(drop=True)
        res_shuffled = compute_drawdown_series(df_shuffled, sort_by_exit=True)
        self.assertAlmostEqual(res_shuffled["mdd_r"], 2.5, places=5)

    def test_04_summarize_and_profit_factor_hand_cases(self):
        """Kiểm tra các chỉ số tổng kết (Expectancy, PF, Win Rate)."""
        df = pd.DataFrame({
            "direction": ["BUY", "BUY", "SELL", "SELL"],
            "entry_exec": [2000.0, 2000.0, 2000.0, 2000.0],
            "exit_price": [2020.0, 1990.0, 1990.0, 2005.0],
            "r_dist": [10.0, 10.0, 10.0, 10.0],
            "exit_reason": ["TP", "SL", "TP", "SL"],
        })
        # BUY 1: (2020 - 2000)/10 = +2.0
        # BUY 2: (1990 - 2000)/10 = -1.0
        # SELL 1: (2000 - 1990)/10 = +1.0
        # SELL 2: (2000 - 2005)/10 = -0.5
        # sum_pos = 2.0 + 1.0 = 3.0
        # sum_neg = |-1.0| + |-0.5| = 1.5
        # PF = 3.0 / 1.5 = 2.0
        # Expectancy = (3.0 - 1.5) / 4 = +0.375 R
        # Win rate = 2 / 4 = 50%
        s = summarize_trades(df)
        self.assertEqual(s["total_trades"], 4)
        self.assertAlmostEqual(s["expectancy"], 0.375, places=5)
        self.assertAlmostEqual(s["profit_factor"], 2.0, places=5)
        self.assertAlmostEqual(s["win_rate"], 0.50, places=5)
        self.assertAlmostEqual(s["loss_rate"], 0.50, places=5)
        self.assertEqual(s["n_win"], 2)
        self.assertEqual(s["n_loss"], 2)
        self.assertEqual(s["exit_counts"], {"TP": 2, "SL": 2})

    def test_05_year_assignment_cross_boundary_hand_cases(self):
        """Kiểm tra gán lệnh vào năm lịch: theo năm vào lệnh vs năm thoát lệnh."""
        # Tạo 3 lệnh mô phỏng 3 lệnh vắt qua ranh giới năm thực tế
        df = pd.DataFrame([
            {
                "signal_id": 1,
                "direction": "SELL",
                "close_time_c": "2019-12-31 02:00:00",
                "exit_time": "2020-01-02 16:00:00",
                "entry_exec": 2000.0,
                "exit_price": 1995.0,  # SELL thắng +0.5R
                "r_dist": 10.0,
            },
            {
                "signal_id": 2,
                "direction": "BUY",
                "close_time_c": "2020-12-31 02:00:00",
                "exit_time": "2021-01-04 08:00:00",
                "entry_exec": 2000.0,
                "exit_price": 2010.0,  # BUY thắng +1.0R
                "r_dist": 10.0,
            },
            {
                "signal_id": 3,
                "direction": "BUY",
                "close_time_c": "2023-12-29 14:00:00",
                "exit_time": "2024-01-02 05:47:00",
                "entry_exec": 2000.0,
                "exit_price": 1990.0,  # BUY thua -1.0R
                "r_dist": 10.0,
            },
        ])

        # Gán theo năm vào lệnh (by_column='entry')
        by_entry = breakdown_by_year(df, years=[2019, 2020, 2021, 2022, 2023, 2024], by_column="entry")
        row_2019 = by_entry[by_entry["year"] == 2019].iloc[0]
        row_2020 = by_entry[by_entry["year"] == 2020].iloc[0]
        row_2023 = by_entry[by_entry["year"] == 2023].iloc[0]
        row_2024 = by_entry[by_entry["year"] == 2024].iloc[0]

        self.assertEqual(row_2019["n_trades"], 1)
        self.assertAlmostEqual(row_2019["expectancy"], 0.5)
        self.assertEqual(row_2020["n_trades"], 1)
        self.assertAlmostEqual(row_2020["expectancy"], 1.0)
        self.assertEqual(row_2023["n_trades"], 1)
        self.assertAlmostEqual(row_2023["expectancy"], -1.0)
        self.assertEqual(row_2024["n_trades"], 0)

        # Gán theo năm thoát lệnh (by_column='exit')
        by_exit = breakdown_by_year(df, years=[2019, 2020, 2021, 2022, 2023, 2024], by_column="exit")
        row_exit_2019 = by_exit[by_exit["year"] == 2019].iloc[0]
        row_exit_2020 = by_exit[by_exit["year"] == 2020].iloc[0]
        row_exit_2023 = by_exit[by_exit["year"] == 2023].iloc[0]
        row_exit_2024 = by_exit[by_exit["year"] == 2024].iloc[0]

        self.assertEqual(row_exit_2019["n_trades"], 0)
        self.assertEqual(row_exit_2020["n_trades"], 1)  # Lệnh 1 chuyển sang 2020
        self.assertEqual(row_exit_2023["n_trades"], 0)
        self.assertEqual(row_exit_2024["n_trades"], 1)  # Lệnh 3 chuyển sang 2024

    def test_06_monte_carlo_reproducibility(self):
        """Kiểm tra tính tái lập tuyệt đối của Monte Carlo C4(c) với seed = 42."""
        buys = np.array([1.5, -1.0, 0.5, -1.0, 2.0])
        sells = np.array([-1.0, 1.2, -0.8, 1.5, -1.0])
        actual_exp = 0.5

        res1 = run_monte_carlo_permutation(buys, sells, actual_expectancy=actual_exp, n_simulations=500, seed=42)
        res2 = run_monte_carlo_permutation(buys, sells, actual_expectancy=actual_exp, n_simulations=500, seed=42)

        self.assertEqual(res1["p_value"], res2["p_value"])
        self.assertEqual(res1["n_exceed"], res2["n_exceed"])
        self.assertAlmostEqual(res1["sim_mean"], res2["sim_mean"], places=7)


if __name__ == "__main__":
    unittest.main()
