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

    def test_07_simulate_c4c_pairs_same_direction_identity(self):
        """KIỂM TOÁN C4(c): Nhánh cùng hướng trong simulate_c4c_pairs BẮT BUỘC bằng bit-for-bit với lệnh thật."""
        from src.backtest.smc.pnl import simulate_c4c_pairs

        base_t = pd.Timestamp("2024-05-10 00:00:00")
        h1_times = [base_t + pd.Timedelta(hours=i) for i in range(40)]
        h1_df = pd.DataFrame([{
            "time_utc": t, "open": 2000.0, "high": 2010.0, "low": 1990.0, "close": 2000.0, "atr_14": 10.0
        } for t in h1_times])

        # Tạo chuỗi nến M1
        m1_times = [base_t + pd.Timedelta(minutes=i) for i in range(40 * 60)]
        # Cho giá dao động nhẹ quanh 2000
        m1_rows = []
        for i, t in enumerate(m1_times):
            m1_rows.append({
                "time_utc": t,
                "open": 2000.0 + (i % 5) * 0.5,
                "high": 2002.0 + (i % 5) * 0.5,
                "low": 1998.0 + (i % 5) * 0.5,
                "close": 2000.0 + (i % 5) * 0.5,
            })
        m1_df = pd.DataFrame(m1_rows)

        spreads_cfg = {
            "spread_multiplier": 1.25,
            "slip_points": 5.0,
            "point_value_usd": 0.01,
            "yearly_median_spreads_points": {"2024": 31.0},
        }

        # 1 lệnh BUY và 1 lệnh SELL
        t_entry1 = pd.Timestamp("2024-05-10 05:00:00")
        t_entry2 = pd.Timestamp("2024-05-10 08:00:00")

        # Giả lập lệnh thật
        # BUY: entry_open = 2000.0, sl = 1990.0
        spread_val = 31.0 * 1.25 * 0.01  # 0.3875
        slip_val = 0.05
        entry_exec_buy = 2000.0 + spread_val + slip_val  # 2000.4375
        sl_buy = 1990.0
        r_dist_buy = entry_exec_buy - sl_buy  # 10.4375
        tp_buy = entry_exec_buy + 2.0 * r_dist_buy

        # Thoát lệnh thật 1 qua scan_position_exit_m1
        from src.backtest.smc.signals import scan_position_exit_m1
        max_h1_time = t_entry1 + pd.Timedelta(hours=24)
        split_end = pd.Timestamp("2025-06-30 23:59:00")
        t_ex1, r_ex1, p_ex1 = scan_position_exit_m1(
            "BUY", t_entry1, entry_exec_buy, sl_buy, tp_buy, spread_val, slip_val, m1_df, max_h1_time, split_end
        )
        real_pnl_1 = compute_trade_r("BUY", entry_exec_buy, p_ex1, r_dist_buy)

        # SELL: entry_open = 2002.0, sl = 2012.0
        entry_exec_sell = 2002.0 - slip_val  # 2001.95
        sl_sell = 2012.0
        r_dist_sell = sl_sell - entry_exec_sell  # 10.05
        tp_sell = entry_exec_sell - 2.0 * r_dist_sell
        t_ex2, r_ex2, p_ex2 = scan_position_exit_m1(
            "SELL", t_entry2, entry_exec_sell, sl_sell, tp_sell, spread_val, slip_val, m1_df, t_entry2 + pd.Timedelta(hours=24), split_end
        )
        real_pnl_2 = compute_trade_r("SELL", entry_exec_sell, p_ex2, r_dist_sell)

        trades_df = pd.DataFrame([
            {
                "direction": "BUY",
                "close_time_c": t_entry1,
                "entry_open": 2000.0,
                "entry_exec": entry_exec_buy,
                "sl": sl_buy,
                "tp": tp_buy,
                "r_dist": r_dist_buy,
                "exit_time": t_ex1,
                "exit_reason": r_ex1,
                "exit_price": p_ex1,
                "pnl_r": real_pnl_1,
            },
            {
                "direction": "SELL",
                "close_time_c": t_entry2,
                "entry_open": 2002.0,
                "entry_exec": entry_exec_sell,
                "sl": sl_sell,
                "tp": tp_sell,
                "r_dist": r_dist_sell,
                "exit_time": t_ex2,
                "exit_reason": r_ex2,
                "exit_price": p_ex2,
                "pnl_r": real_pnl_2,
            },
        ])

        pnl_buys, pnl_sells = simulate_c4c_pairs(
            trades_df, m1_df, h1_df, spreads_cfg=spreads_cfg, split_end_str=str(split_end)
        )

        # KHẲNG ĐỊNH ĐẲNG THỨC:
        # Lệnh 0 là BUY: nhánh BUY mô phỏng PHẢI BẰNG EXACT thực tế
        self.assertAlmostEqual(pnl_buys[0], real_pnl_1, places=5)
        # Lệnh 1 là SELL: nhánh SELL mô phỏng PHẢI BẰNG EXACT thực tế
        self.assertAlmostEqual(pnl_sells[1], real_pnl_2, places=5)

    def test_08_monte_carlo_p_value_hand_calculated(self):
        """Kiểm tra công thức toán học p-value: p = (1 + n_exceed) / (N_sim + 1)."""
        # Ca 1: Không có lần nào mô phỏng ngẫu nhiên vượt được actual (n_exceed = 0)
        # p = (1 + 0) / (1000 + 1) = 1 / 1001
        buys_low = np.array([-1.0, -1.0])
        sells_low = np.array([-1.0, -1.0])
        res_min = run_monte_carlo_permutation(buys_low, sells_low, actual_expectancy=10.0, n_simulations=1000, seed=42)
        self.assertEqual(res_min["n_exceed"], 0)
        self.assertAlmostEqual(res_min["p_value"], 1.0 / 1001.0, places=7)

        # Ca 2: Mọi lần mô phỏng ngẫu nhiên đều >= actual (n_exceed = 1000)
        # p = (1 + 1000) / (1000 + 1) = 1001 / 1001 = 1.0
        buys_high = np.array([2.0, 2.0])
        sells_high = np.array([2.0, 2.0])
        res_max = run_monte_carlo_permutation(buys_high, sells_high, actual_expectancy=-10.0, n_simulations=1000, seed=42)
        self.assertEqual(res_max["n_exceed"], 1000)
        self.assertAlmostEqual(res_max["p_value"], 1.0, places=7)

        # Ca 3: Kiểm tra tính đúng đắn với mảng đối xứng BUY (+1) / SELL (-1)
        # choices chọn BUY hoặc SELL với p = 0.5
        # sim_mean lý thuyết = (1.0 - 1.0) / 2 = 0.0
        buys = np.array([1.0, 1.0])
        sells = np.array([-1.0, -1.0])
        res_sym = run_monte_carlo_permutation(buys, sells, actual_expectancy=0.0, n_simulations=1000, seed=42)
        # Xác suất để choices = (BUY, BUY) là 0.25 -> E = +1.0 >= 0
        # Xác suất choices = (BUY, SELL) hoặc (SELL, BUY) là 0.50 -> E = 0.0 >= 0
        # Xác suất choices = (SELL, SELL) là 0.25 -> E = -1.0 < 0
        # Lý thuyết: 75% số lần có E >= 0 -> n_exceed ~ 750
        self.assertTrue(700 <= res_sym["n_exceed"] <= 800)
        expected_p = (1 + res_sym["n_exceed"]) / 1001.0
        self.assertAlmostEqual(res_sym["p_value"], expected_p, places=7)


if __name__ == "__main__":
    unittest.main()

