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
    random_control,
    summarize,
)


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


if __name__ == "__main__":
    unittest.main()
