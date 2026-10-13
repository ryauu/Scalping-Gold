"""Kiểm thử mô-đun metrics.py (Bước 5: Đo lường hiệu suất và 5 tiêu chí PASS):

1. summarize tính tay: bảng 4 lệnh (+2, -1, -1, +2) cho kỳ vọng 0.5 và profit factor 2.0.
2. summarize edge cases: bảng rỗng, toàn lệnh thắng (profit factor = inf), lệnh hòa (pnl = 0).
3. by_year: bảng theo 8 năm lịch 2018-2025 tính đúng số năm dương (5/8 năm).
4. random_control ca cực đoan (hướng thật luôn thắng): p-value = 1 / 1001 ≈ 0.000999.
5. random_control ca đối xứng: p-value quanh 0.5.
6. evaluate_pass: kiểm tra cấu trúc chi tiết từng điều kiện trong 5 tiêu chí PASS và cờ all_passed.
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

from src.backtest.metrics import (
    DEFAULT_DEVELOPMENT_YEARS,
    by_year,
    evaluate_pass,
    evaluate_pass_robustness_holdout,
    random_control,
    summarize,
)
from src.backtest.simulate import simulate_trades



class TestMetricsModule(unittest.TestCase):
    def test_01_summarize_hand_calculated(self):
        """1. summarize tính tay: 4 lệnh [+2, -1, -1, +2] cho kỳ vọng 0.5, profit factor 2.0, win rate 0.5."""
        trades = pd.DataFrame({
            "pnl_r": [2.0, -1.0, -1.0, 2.0],
        })

        s = summarize(trades)

        self.assertEqual(s["total_trades"], 4)
        # Kỳ vọng = (2 - 1 - 1 + 2) / 4 = 2 / 4 = 0.5
        self.assertAlmostEqual(s["expectancy"], 0.5, places=6)
        # Profit factor = tổng dương (4.0) / |tổng âm| (2.0) = 2.0
        self.assertAlmostEqual(s["profit_factor"], 2.0, places=6)
        # Win rate = 2 / 4 = 0.5
        self.assertAlmostEqual(s["win_rate"], 0.5, places=6)
        self.assertAlmostEqual(s["loss_rate"], 0.5, places=6)
        self.assertEqual(s["n_win"], 2)
        self.assertEqual(s["n_loss"], 2)
        self.assertEqual(s["n_breakeven"], 0)
        self.assertAlmostEqual(s["sum_pnl_r"], 2.0, places=6)

    def test_02_summarize_edge_cases(self):
        """2. summarize với ca biên: bảng rỗng, toàn lệnh thắng, lệnh hòa không tính vào profit factor."""
        # A) Bảng rỗng
        s_empty = summarize(pd.DataFrame(columns=["pnl_r"]))
        self.assertEqual(s_empty["total_trades"], 0)
        self.assertEqual(s_empty["profit_factor"], 0.0)
        self.assertEqual(s_empty["expectancy"], 0.0)

        # B) Toàn lệnh thắng
        s_win = summarize(pd.DataFrame({"pnl_r": [1.5, 2.0, 0.5]}))
        self.assertEqual(s_win["profit_factor"], float("inf"))
        self.assertEqual(s_win["win_rate"], 1.0)

        # C) Có lệnh hòa (pnl_r = 0): C4 quy định không tính vào bên nào
        trades_zero = pd.DataFrame({"pnl_r": [2.0, 0.0, -1.0]})
        s_zero = summarize(trades_zero)
        # tổng dương = 2.0, tổng âm = 1.0 -> PF = 2.0
        self.assertAlmostEqual(s_zero["profit_factor"], 2.0, places=6)
        self.assertEqual(s_zero["n_breakeven"], 1)

    def test_03_by_year_distribution_hand_calculated(self):
        """3. by_year: bảng 8 năm lịch 2018-2025 tính đúng số năm có kỳ vọng dương (5/8 năm)."""
        # Tạo dữ liệu cho 8 năm lịch (2018-2025): 5 năm dương, 3 năm âm
        records = [
            # 2018: dương (+1.0)
            {"year": 2018, "pnl_r": 1.0},
            {"year": 2018, "pnl_r": 1.0},
            # 2019: dương (+0.5)
            {"year": 2019, "pnl_r": 0.5},
            # 2020: dương (+0.2)
            {"year": 2020, "pnl_r": 0.2},
            # 2021: dương (+0.1)
            {"year": 2021, "pnl_r": 0.1},
            # 2022: dương (+0.3)
            {"year": 2022, "pnl_r": 0.3},
            # 2023: âm (-0.3)
            {"year": 2023, "pnl_r": -0.3},
            # 2024: âm (-0.5)
            {"year": 2024, "pnl_r": -0.5},
            # 2025: âm (-0.2)
            {"year": 2025, "pnl_r": -0.2},
        ]
        df_trades = pd.DataFrame(records)

        yearly = by_year(df_trades, years=DEFAULT_DEVELOPMENT_YEARS)

        self.assertEqual(len(yearly), 8, "Phải có đủ đúng 8 năm lịch")
        self.assertEqual(list(yearly["year"]), list(range(2018, 2026)))
        # Đếm số năm dương
        n_pos = int(yearly["is_positive"].sum())
        self.assertEqual(n_pos, 5, "Phải có đúng 5 năm có kỳ vọng dương")
        self.assertEqual(yearly.attrs["n_positive_years"], 5)
        self.assertEqual(yearly.attrs["total_years"], 8)

    def test_04_random_control_extreme_case_always_win(self):
        """4. random_control ca cực đoan: Hướng thật luôn thắng (+2R), ngược lại thua (-1R) -> p = 1/1001 ≈ 0.000999."""
        n_trades = 50
        # 50 lệnh thật đều đạt +2.0R
        trades = pd.DataFrame({
            "pnl_r": np.full(n_trades, 2.0),
        })

        # Nhánh BUY luôn +2.0R, nhánh SELL luôn -1.0R
        pnl_buys = np.full(n_trades, 2.0)
        pnl_sells = np.full(n_trades, -1.0)

        res = random_control(
            trades,
            precomputed_pairs=(pnl_buys, pnl_sells),
            n_simulations=1000,
            seed=42,
        )

        # Kỳ vọng thật là 2.0
        self.assertAlmostEqual(res["actual_expectancy"], 2.0, places=6)
        # Rút ngẫu nhiên giữa 2.0 và -1.0: kỳ vọng ngẫu nhiên quanh 0.5, số lần >= 2.0 là 0
        self.assertEqual(res["n_exceed"], 0)
        # p-value = (1 + 0) / 1001 = 1 / 1001 ≈ 0.000999
        expected_p = 1.0 / 1001.0
        self.assertAlmostEqual(res["p_value"], expected_p, places=6)
        self.assertLessEqual(res["p_value"], 0.05, "Ca thật có edge vượt trội phải có p <= 0.05")

    def test_05_random_control_symmetric_distribution(self):
        """5. random_control ca đối xứng: BUY và SELL có cùng phân phối lợi nhuận -> p-value quanh 0.5."""
        n_trades = 100
        # Dữ liệu đối xứng hoàn toàn giữa BUY và SELL
        pnl_buys = np.tile([1.0, -1.0], n_trades // 2)
        pnl_sells = np.tile([-1.0, 1.0], n_trades // 2)

        trades = pd.DataFrame({
            "pnl_r": pnl_buys,  # actual expectancy = 0.0
        })

        res = random_control(
            trades,
            precomputed_pairs=(pnl_buys, pnl_sells),
            n_simulations=1000,
            seed=42,
        )

        self.assertAlmostEqual(res["actual_expectancy"], 0.0, places=6)
        # Khi đối xứng quanh 0.0, p-value phải rơi vào khoảng quanh 0.5 (ví dụ [0.40, 0.60])
        self.assertGreater(res["p_value"], 0.40)
        self.assertLess(res["p_value"], 0.60)

    def test_06_evaluate_pass_breakdown_and_all_passed(self):
        """6. evaluate_pass: trả về chi tiết cả 5 điều kiện (giá trị thực và bool passed) và cờ all_passed."""
        # Tạo bảng 800 lệnh thỏa mãn cả 5 điều kiện:
        # - 800 lệnh
        # - kỳ vọng ròng = +0.10R >= +0.05R
        # - PF >= 1.10
        # - 5/8 năm dương
        # - p-value <= 0.05
        years_seq = np.tile(list(range(2018, 2026)), 100)  # 800 lệnh chia đều cho 8 năm
        pnl_list = []
        for y in years_seq:
            if y in [2018, 2019, 2020, 2021, 2022]:  # 5 năm dương
                pnl_list.append(0.3)
            else:  # 3 năm âm
                pnl_list.append(-0.1)

        trades = pd.DataFrame({
            "year": years_seq,
            "pnl_r": pnl_list,
        })

        # Precomputed pairs cho random control để ra p <= 0.05
        pnl_buys = np.array(pnl_list)
        pnl_sells = -np.array(pnl_list)

        eval_res = evaluate_pass(
            trades,
            precomputed_pairs=(pnl_buys, pnl_sells),
            n_simulations=1000,
            seed=42,
        )

        # Kiểm tra cờ từng điều kiện
        self.assertTrue(eval_res["c1_total_trades"]["passed"])
        self.assertEqual(eval_res["c1_total_trades"]["actual"], 800)

        self.assertTrue(eval_res["c2_expectancy"]["passed"])
        self.assertGreaterEqual(eval_res["c2_expectancy"]["actual"], 0.05)

        self.assertTrue(eval_res["c3_profit_factor"]["passed"])
        self.assertGreaterEqual(eval_res["c3_profit_factor"]["actual"], 1.10)

        self.assertTrue(eval_res["c4_positive_years"]["passed"])
        self.assertEqual(eval_res["c4_positive_years"]["actual"], 5)

        self.assertTrue(eval_res["c5_random_control"]["passed"])
        self.assertLessEqual(eval_res["c5_random_control"]["actual_p"], 0.05)

        self.assertTrue(eval_res["all_passed"], "Khi thỏa mãn cả 5 điều kiện thì all_passed phải là True")

        # Thử nghiệm trường hợp thiếu 1 lệnh (799 lệnh) -> all_passed phải False
        trades_799 = trades.iloc[:799]
        eval_fail = evaluate_pass(
            trades_799,
            precomputed_pairs=(pnl_buys[:799], pnl_sells[:799]),
            n_simulations=1000,
            seed=42,
        )
        self.assertFalse(eval_fail["c1_total_trades"]["passed"])
        self.assertFalse(eval_fail["all_passed"], "Thiếu 1 lệnh (FAIL sát nút) vẫn phải là False theo PROTOCOL")

    def test_07_random_control_reproduces_actual_trade_exactly(self):
        """7. TEST BẮT LỖI QUAN TRỌNG NHẤT:

        Với mỗi lệnh thật, nhánh cùng hướng trong random_control PHẢI TÁI TẠO CHÍNH XÁC pnl_r của lệnh đó.
        Đảm bảo việc dùng trực tiếp entry_open loại bỏ hoàn toàn sự mong manh của việc suy ngược giá.
        """
        # Tạo nến M1 cho 2 ngày: ngày 1 lệnh BUY, ngày 2 lệnh SELL
        times_d1 = pd.date_range("2020-01-02 07:00", "2020-01-02 16:30", freq="1min", tz="UTC")
        times_d2 = pd.date_range("2020-01-03 07:00", "2020-01-03 16:30", freq="1min", tz="UTC")

        # Ngày 1 (BUY): Open nến vào lệnh = 1500.0, giá sau đó tăng lên 1530.0 (chạm TP)
        n1 = len(times_d1)
        opens1 = np.full(n1, 1500.0)
        highs1 = np.full(n1, 1502.0)
        lows1 = np.full(n1, 1498.0)
        closes1 = np.full(n1, 1500.0)
        # Tại nến thứ 30, giá tăng vọt chạm TP
        highs1[30:] = 1535.0

        # Ngày 2 (SELL): Open nến vào lệnh = 1520.0, giá sau đó giảm xuống 1480.0 (chạm TP)
        n2 = len(times_d2)
        opens2 = np.full(n2, 1520.0)
        highs2 = np.full(n2, 1522.0)
        lows2 = np.full(n2, 1518.0)
        closes2 = np.full(n2, 1520.0)
        # Tại nến thứ 25, giá giảm sâu chạm TP của SELL
        lows2[25:] = 1480.0

        df_m1_1 = pd.DataFrame({
            "time_utc": times_d1,
            "open": opens1,
            "high": highs1,
            "low": lows1,
            "close": closes1,
            "spread": 40.0,
        })
        df_m1_2 = pd.DataFrame({
            "time_utc": times_d2,
            "open": opens2,
            "high": highs2,
            "low": lows2,
            "close": closes2,
            "spread": 40.0,
        })
        df_m1 = pd.concat([df_m1_1, df_m1_2], ignore_index=True)

        # Tạo tín hiệu cho 2 ngày
        signals = pd.DataFrame([
            {
                "date": pd.Timestamp("2020-01-02", tz="UTC"),
                "direction": "BUY",
                "signal_time": pd.Timestamp("2020-01-02 07:05", tz="UTC"),
                "entry_time": pd.Timestamp("2020-01-02 07:06", tz="UTC"),
                "entry_open": 1500.0,
                "entry_price": 1500.0,
                "sl": 1490.0,
                "atr": 5.0,
                "asia_high": 1505.0,
                "asia_low": 1490.0,
            },
            {
                "date": pd.Timestamp("2020-01-03", tz="UTC"),
                "direction": "SELL",
                "signal_time": pd.Timestamp("2020-01-03 07:10", tz="UTC"),
                "entry_time": pd.Timestamp("2020-01-03 07:11", tz="UTC"),
                "entry_open": 1520.0,
                "entry_price": 1520.0,
                "sl": 1530.0,
                "atr": 5.0,
                "asia_high": 1530.0,
                "asia_low": 1515.0,
            },
        ])

        # Chạy mô phỏng lệnh thật
        trades = simulate_trades(signals, df_m1)
        self.assertEqual(len(trades), 2)
        self.assertIn("entry_open", trades.columns)

        # Chạy kiểm soát ngẫu nhiên trên dữ liệu M1
        rc = random_control(trades, m1=df_m1, n_simulations=10, seed=42)
        pnl_buys = rc["pnl_buys"]
        pnl_sells = rc["pnl_sells"]

        # Kiểm tra tính khớp 100% giữa nhánh cùng hướng và pnl_r của lệnh thật
        for i, row in trades.iterrows():
            dir_i = str(row["direction"]).upper()
            actual_pnl = float(row["pnl_r"])
            if dir_i == "BUY":
                buy_branch_pnl = float(pnl_buys[i])
                self.assertAlmostEqual(
                    buy_branch_pnl,
                    actual_pnl,
                    places=9,
                    msg=f"Lệnh BUY #{i}: nhánh BUY của random_control ({buy_branch_pnl}) lệch với lệnh thật ({actual_pnl})",
                )
            else:
                sell_branch_pnl = float(pnl_sells[i])
                self.assertAlmostEqual(
                    sell_branch_pnl,
                    actual_pnl,
                    places=9,
                    msg=f"Lệnh SELL #{i}: nhánh SELL của random_control ({sell_branch_pnl}) lệch với lệnh thật ({actual_pnl})",
                )

    def test_08_summarize_nan_guard(self):
        """8. summarize chốt chặn NaN: Nếu pnl_r có NaN, phải ném ValueError."""
        # A) DataFrame có NaN
        trades_nan = pd.DataFrame({"pnl_r": [1.0, np.nan, -0.5]})
        with self.assertRaises(ValueError) as ctx:
            summarize(trades_nan)
        self.assertIn("NaN trong pnl_r", str(ctx.exception))

        # B) Array/list có NaN
        with self.assertRaises(ValueError):
            summarize([np.nan, 2.0, -1.0])

    def test_09_random_control_missing_m1_raises_error(self):
        """9. random_control: Báo lỗi khi thiếu nến M1 thay vì âm thầm ghi 0.0."""
        trades = pd.DataFrame([
            {
                "date": pd.Timestamp("2020-01-02", tz="UTC"),
                "direction": "BUY",
                "entry_time": pd.Timestamp("2020-01-02 07:06", tz="UTC"),
                "entry_open": 1500.0,
                "entry_price": 1500.45,
                "sl": 1490.0,
                "pnl_r": 1.0,
            }
        ])

        # Tạo M1 rỗng hoặc chỉ có ngày khác (2020-01-05)
        times_other = pd.date_range("2020-01-05 07:00", "2020-01-05 16:30", freq="1min", tz="UTC")
        m1_other = pd.DataFrame({
            "time_utc": times_other,
            "open": 1500.0,
            "high": 1501.0,
            "low": 1499.0,
            "close": 1500.0,
        })

        with self.assertRaises(ValueError) as ctx:
            random_control(trades, m1=m1_other, n_simulations=10, seed=42)
        self.assertIn("Không tìm thấy nến M1", str(ctx.exception))

    def test_10_random_control_always_buy_and_sell_reference(self):
        """10. random_control trả về 2 số tham chiếu EXPLORATORY: always_buy_expectancy và always_sell_expectancy."""
        pnl_buys = np.array([2.0, 1.0, -1.0])
        pnl_sells = np.array([-1.0, -1.0, 2.0])
        trades = pd.DataFrame({"pnl_r": pnl_buys})

        res = random_control(
            trades,
            precomputed_pairs=(pnl_buys, pnl_sells),
            n_simulations=10,
            seed=42,
        )

        expected_buy_exp = float(np.mean(pnl_buys))  # (2 + 1 - 1) / 3 = 2/3 ≈ 0.666667
        expected_sell_exp = float(np.mean(pnl_sells))  # (-1 - 1 + 2) / 3 = 0.0

        self.assertIn("always_buy_expectancy", res)
        self.assertIn("always_sell_expectancy", res)
        self.assertAlmostEqual(res["always_buy_expectancy"], expected_buy_exp, places=6)
        self.assertAlmostEqual(res["always_sell_expectancy"], expected_sell_exp, places=6)

    def test_11_evaluate_pass_robustness_holdout(self):
        """11. evaluate_pass_robustness_holdout: chỉ kiểm tra điều kiện 2 (kỳ vọng >= 0.05R) và 3 (PF >= 1.10)."""
        # Ca đạt cả 2
        trades_pass = pd.DataFrame({"pnl_r": [2.0, 2.0, -1.0, -1.0]})  # exp = 0.5, PF = 2.0
        res_pass = evaluate_pass_robustness_holdout(trades_pass)
        self.assertTrue(res_pass["all_passed"])
        self.assertTrue(res_pass["c2_expectancy"]["passed"])
        self.assertTrue(res_pass["c3_profit_factor"]["passed"])

        # Ca fail điều kiện 2 (exp = 0.04 < 0.05) dù PF cao
        trades_fail_c2 = pd.DataFrame({"pnl_r": [0.04, 0.04]})
        res_fail_c2 = evaluate_pass_robustness_holdout(trades_fail_c2)
        self.assertFalse(res_fail_c2["all_passed"])
        self.assertFalse(res_fail_c2["c2_expectancy"]["passed"])

        # Ca fail điều kiện 3 (PF = 1.05 < 1.10)
        trades_fail_c3 = pd.DataFrame({"pnl_r": [1.05, -1.0]})
        res_fail_c3 = evaluate_pass_robustness_holdout(trades_fail_c3)
        self.assertFalse(res_fail_c3["all_passed"])
        self.assertFalse(res_fail_c3["c3_profit_factor"]["passed"])


if __name__ == "__main__":
    unittest.main()

