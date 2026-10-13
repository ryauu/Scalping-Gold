"""Kiểm thử mô-đun swings.py (Bước 1: Swing High / Swing Low chuẩn SMC).

Bao gồm 4 bài kiểm tra bắt buộc theo SMC_SPEC.md mục 9:
1. Test bất biến theo tiền tố (Prefix Invariance Test - BẮT BUỘC): Đảm bảo tuyệt đối không look-ahead.
2. Test chuỗi giả tính tay (Synthetic test): Kiểm tra đúng định nghĩa toán học và trường hợp biên (hai đỉnh bằng nhau).
3. Test hàm get_swings_available_at: Lọc chính xác theo thời điểm khả dụng time_available.
4. Thống kê tần suất xuất hiện theo năm trên H1 Development (đếm sự kiện, không tính P&L).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
import pandas as pd

from src.backtest.data import load_h1
from src.backtest.smc.swings import (
    DEFAULT_H1_DURATION,
    detect_swings,
    get_swings_available_at,
)


class TestSwingsModule(unittest.TestCase):
    def test_01_synthetic_hand_calculated_swing(self):
        """1. Kiểm tra chuỗi giả lập tính tay: Đỉnh tại nến 3 (k=2) có time_available tại nến 5 đóng cửa."""
        # 7 nến H1: nến 0, 1, 2, 3, 4, 5, 6
        times = pd.date_range("2020-01-02 00:00", periods=7, freq="1h", tz="UTC")
        # Nến 3 có đỉnh cao nhất (1520.0), hai bên thấp hơn
        highs = [1500.0, 1505.0, 1510.0, 1520.0, 1515.0, 1508.0, 1502.0]
        # Nến 5 có đáy thấp nhất (1490.0)
        lows = [1498.0, 1501.0, 1504.0, 1505.0, 1500.0, 1490.0, 1495.0]

        df = pd.DataFrame({
            "time_utc": times,
            "open": highs,
            "high": highs,
            "low": lows,
            "close": lows,
        })

        k = 2
        swings = detect_swings(df, k=k)

        # Nến 3 là Swing High: 1520 > 1505, 1510 và 1520 > 1515, 1508
        high_swings = swings[swings["swing_type"] == "HIGH"]
        self.assertEqual(len(high_swings), 1)
        sw_h = high_swings.iloc[0]
        self.assertEqual(sw_h["bar_index"], 3)
        self.assertEqual(sw_h["price"], 1520.0)
        self.assertEqual(sw_h["time_candle"], times[3])
        # Nến 3 + k = nến 5 (05:00 UTC). Đóng cửa lúc 06:00 UTC (times[5] + 1h)
        expected_available = times[3 + k] + pd.Timedelta(hours=1)
        self.assertEqual(sw_h["time_available"], expected_available)

    def test_02_synthetic_equal_highs_strictly_greater_rule(self):
        """2. Trường hợp biên: Hai đỉnh bằng nhau liên tiếp không được xem là Swing High đơn lẻ."""
        times = pd.date_range("2020-01-02 00:00", periods=7, freq="1h", tz="UTC")
        # Nến 3 và 4 có High bằng nhau (1520.0)
        highs = [1500.0, 1505.0, 1510.0, 1520.0, 1520.0, 1508.0, 1502.0]
        lows = [1498.0, 1501.0, 1504.0, 1505.0, 1500.0, 1490.0, 1495.0]

        df = pd.DataFrame({
            "time_utc": times,
            "high": highs,
            "low": lows,
        })

        # Với k=2: Nến 3 so với nến 4 không lớn hơn nghiêm ngặt -> Không phải Swing High.
        # Nến 4 so với nến 3 không lớn hơn nghiêm ngặt -> Không phải Swing High.
        swings = detect_swings(df, k=2)
        high_swings = swings[swings["swing_type"] == "HIGH"]
        self.assertEqual(len(high_swings), 0, "Hai đỉnh bằng nhau không được tạo Swing High đơn lẻ")

    def test_03_prefix_invariance_test_no_lookahead(self):
        """3. TEST BẮT BUỘC QUAN TRỌNG NHẤT: Bất biến theo tiền tố (Prefix Invariance).

        Chạy phát hiện swing trên tập dữ liệu cắt ngắn đến thời điểm t1,
        sau đó chạy trên toàn bộ dữ liệu dài hơn đến t2 > t1.
        Mọi swing có time_available <= t1 PHẢI GIỐNG NHAU HOÀN TOÀN TỪNG GIÁ TRỊ.
        """
        # Tạo chuỗi 100 nến H1
        np.random.seed(42)
        n_bars = 100
        times = pd.date_range("2020-01-01 00:00", periods=n_bars, freq="1h", tz="UTC")
        base_price = 1500.0 + np.cumsum(np.random.randn(n_bars) * 2.0)
        highs = base_price + np.random.uniform(0.5, 3.0, n_bars)
        lows = base_price - np.random.uniform(0.5, 3.0, n_bars)

        full_df = pd.DataFrame({
            "time_utc": times,
            "high": highs,
            "low": lows,
        })

        k = 3
        # Cắt tiền tố tại nến thứ 60 (t1 = times[60])
        cut_idx = 60
        prefix_df = full_df.iloc[:cut_idx].copy()
        t1_close = times[cut_idx - 1] + pd.Timedelta(hours=1)

        swings_prefix = detect_swings(prefix_df, k=k)
        swings_full = detect_swings(full_df, k=k)

        # Lọc các swing trong tập full có time_available <= t1_close
        swings_full_filtered = swings_full[swings_full["time_available"] <= t1_close].reset_index(drop=True)
        swings_prefix_reset = swings_prefix.reset_index(drop=True)

        self.assertGreater(len(swings_prefix_reset), 0, "Tập tiền tố phải phát hiện được swing")
        self.assertEqual(
            len(swings_prefix_reset),
            len(swings_full_filtered),
            "Số lượng swing có time_available <= t1_close ở hai lần chạy phải bằng nhau",
        )

        # Đối chiếu từng dòng: bar_index, price, swing_type, time_candle, time_available
        for i in range(len(swings_prefix_reset)):
            row_p = swings_prefix_reset.iloc[i]
            row_f = swings_full_filtered.iloc[i]

            self.assertEqual(row_p["swing_type"], row_f["swing_type"])
            self.assertEqual(row_p["bar_index"], row_f["bar_index"])
            self.assertEqual(row_p["time_candle"], row_f["time_candle"])
            self.assertEqual(row_p["time_available"], row_f["time_available"])
            self.assertAlmostEqual(row_p["price"], row_f["price"], places=6)

    def test_04_get_swings_available_at(self):
        """4. get_swings_available_at: Chỉ trả về các swing đã khả dụng tại thời điểm xét."""
        times = pd.date_range("2020-01-02 00:00", periods=10, freq="1h", tz="UTC")
        highs = [10, 12, 15, 20, 14, 11, 13, 22, 16, 12]
        lows = [8, 9, 10, 11, 10, 8, 9, 10, 11, 10]

        df = pd.DataFrame({"time_utc": times, "high": highs, "low": lows})
        swings = detect_swings(df, k=2)

        # Nến 3 là Swing High (20.0), time_available là nến 3+2=5 đóng cửa (times[5] + 1h = 06:00 UTC)
        sw_3 = swings[swings["bar_index"] == 3].iloc[0]
        t_avail = sw_3["time_available"]

        # Trước time_available 1 phút: chưa có
        sw_before = get_swings_available_at(swings, t_avail - pd.Timedelta(minutes=1))
        self.assertNotIn(3, list(sw_before["bar_index"]))

        # Đúng time_available: đã có
        sw_exact = get_swings_available_at(swings, t_avail)
        self.assertIn(3, list(sw_exact["bar_index"]))

    def test_05_development_h1_event_counts_by_year(self):
        """5. Đếm tần suất xuất hiện Swing trên nến H1 lớp Development (2018–2025).

        Chỉ đếm số lượng sự kiện theo năm (không tính P&L), kiểm tra quy mô mẫu.
        """
        # Nạp dữ liệu H1 Development
        h1_df = load_h1("2018-01-01", "2025-06-30")
        self.assertFalse(h1_df.empty, "Dữ liệu H1 Development không được rỗng")

        # Chạy với k=3 (nhịp trung) và k=5 (nhịp lớn)
        swings_k3 = detect_swings(h1_df, k=3)
        swings_k5 = detect_swings(h1_df, k=5)

        self.assertGreater(len(swings_k3), 1000, "Với k=3, số lượng swing phải dồi dào qua 7.5 năm")
        self.assertGreater(len(swings_k5), 500, "Với k=5, số lượng swing phải đủ lớn")
        self.assertLess(len(swings_k5), len(swings_k3), "k=5 phải có ít swing hơn k=3")

        # Phân phối theo năm
        swings_k3_copy = swings_k3.copy()
        swings_k3_copy["year"] = pd.to_datetime(swings_k3_copy["time_candle"]).dt.year
        counts_by_year = swings_k3_copy.groupby(["year", "swing_type"]).size().unstack(fill_value=0)

        # Kiểm tra mọi năm từ 2018 đến 2024 đều có ít nhất 100 swings mỗi năm
        for y in range(2018, 2025):
            self.assertIn(y, counts_by_year.index)
            self.assertGreater(counts_by_year.loc[y, "HIGH"], 50)
            self.assertGreater(counts_by_year.loc[y, "LOW"], 50)


if __name__ == "__main__":
    unittest.main()
