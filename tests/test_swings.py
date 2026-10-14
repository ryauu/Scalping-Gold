"""Kiểm thử mô-đun swings.py (Bước 1: Swing High / Swing Low chuẩn SMC).

Bao gồm các bài kiểm tra bắt buộc theo SMC_SPEC.md mục 9:
1. Test các ca tính tay (Hand-calculated synthetic tests):
   - Chuỗi high = [1, 2, 5, 2, 1] với k=2 cho Swing High tại chỉ số 2.
   - Chuỗi high = [1, 5, 5, 2, 1] với k=2 không cho swing (ca hòa bị loại bỏ nghiêm ngặt).
   - Nến ngoài (outside bar) đồng thời là cả Swing High và Swing Low trên cùng 1 nến.
   - Kiểm tra time_available ở hai phía của một khoảng trống cuối tuần (weekend gap).
2. Test bảo vệ chống NaN (NaN protection test):
   - Chặn tuyệt đối NaN trên time_utc, high, low, ngăn ngừa lỗi so sánh <= NaN ra False.
3. Test bất biến theo tiền tố (Prefix Invariance Test - BẮT BUỘC):
   - Cắt theo các hàng của bảng H1 thật (không cắt M1), so sánh với tập full.
   - Khớp 100% từng thuộc tính tại mọi điểm cắt time_available <= cutoff_close.
4. Test hàm get_swings_available_at:
   - Lọc chính xác theo thời điểm khả dụng time_available và lọc theo loại swing.
5. Thống kê tần suất xuất hiện theo năm và tỷ lệ ca hòa trên dữ liệu H1 Development (2018–2025):
   - Đếm số lượng sự kiện theo năm (không tính P&L), đo tỷ lệ ca hòa trên giá vàng thật.
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
    def test_01_hand_calculated_high_k2(self):
        """1. Ca tính tay 1: high = [1, 2, 5, 2, 1] với k=2 cho Swing High tại chỉ số 2."""
        times = pd.date_range("2024-01-02 00:00", periods=5, freq="1h")
        highs = [1.0, 2.0, 5.0, 2.0, 1.0]
        lows = [0.5, 1.0, 1.5, 1.0, 0.5]

        df = pd.DataFrame({
            "time_utc": times,
            "high": highs,
            "low": lows,
        })

        swings = detect_swings(df, k=2)
        high_swings = swings[swings["swing_type"] == "HIGH"]

        self.assertEqual(len(high_swings), 1, "Phải có đúng 1 Swing High")
        sw = high_swings.iloc[0]
        self.assertEqual(sw["bar_index"], 2, "Swing High phải nằm ở chỉ số 2")
        self.assertEqual(sw["price"], 5.0, "Mức giá đỉnh phải là 5.0")
        self.assertEqual(sw["time_candle"], times[2])
        # nến 2 + k = nến 4, đóng cửa lúc times[4] + 1h
        self.assertEqual(sw["time_available"], times[4] + DEFAULT_H1_DURATION)

    def test_02_hand_calculated_tie_rejected(self):
        """2. Ca tính tay 2: high = [1, 5, 5, 2, 1] với k=2 không cho swing (ca hòa bị loại)."""
        times = pd.date_range("2024-01-02 00:00", periods=5, freq="1h")
        highs = [1.0, 5.0, 5.0, 2.0, 1.0]
        lows = [0.5, 1.0, 1.0, 1.0, 0.5]

        df = pd.DataFrame({
            "time_utc": times,
            "high": highs,
            "low": lows,
        })

        swings = detect_swings(df, k=2)
        high_swings = swings[swings["swing_type"] == "HIGH"]

        self.assertEqual(
            len(high_swings), 0, "Hai đỉnh bằng nhau liên tiếp không được tạo Swing High đơn lẻ"
        )

    def test_03_same_bar_outside_candle_both_swings(self):
        """3. Ca tính tay 3: Nến ngoài (outside bar) đồng thời là cả Swing High và Swing Low."""
        times = pd.date_range("2024-01-02 00:00", periods=5, freq="1h")
        # Nến 2 vượt đỉnh 2 bên (20 > 12, 10 và 20 > 11, 9) VÀ thủng đáy 2 bên (2 < 7, 8 và 2 < 6, 8)
        highs = [10.0, 12.0, 20.0, 11.0, 9.0]
        lows = [8.0, 7.0, 2.0, 6.0, 8.0]

        df = pd.DataFrame({
            "time_utc": times,
            "high": highs,
            "low": lows,
        })

        swings = detect_swings(df, k=2)

        self.assertEqual(len(swings), 2, "Nến ngoài phải sinh đủ 2 swing: HIGH và LOW")
        sw_high = swings[swings["swing_type"] == "HIGH"].iloc[0]
        sw_low = swings[swings["swing_type"] == "LOW"].iloc[0]

        self.assertEqual(sw_high["bar_index"], 2)
        self.assertEqual(sw_high["price"], 20.0)
        self.assertEqual(sw_low["bar_index"], 2)
        self.assertEqual(sw_low["price"], 2.0)

        # Cùng thời điểm mở nến và cùng thời điểm khả dụng
        self.assertEqual(sw_high["time_candle"], times[2])
        self.assertEqual(sw_low["time_candle"], times[2])
        expected_available = times[4] + DEFAULT_H1_DURATION
        self.assertEqual(sw_high["time_available"], expected_available)
        self.assertEqual(sw_low["time_available"], expected_available)

    def test_04_weekend_gap_time_available(self):
        """4. Ca tính tay 4: Kiểm tra time_available ở hai phía của khoảng trống cuối tuần."""
        # Mô phỏng nến qua cuối tuần (UTC naive):
        # Nến 0: Thứ Sáu 2024-01-05 19:00
        # Nến 1: Thứ Sáu 2024-01-05 20:00
        # Nến 2: Thứ Sáu 2024-01-05 21:00 (Nến cuối tuần, đóng lúc 22:00 - Tạo Swing High)
        # --- Khoảng trống cuối tuần 49 tiếng ---
        # Nến 3: Chủ Nhật 2024-01-07 23:00 (Nến mở cửa tuần mới, đóng lúc T2 00:00)
        # Nến 4: Thứ Hai 2024-01-08 00:00 (Nến sau gap - Tạo Swing Low)
        # Nến 5: Thứ Hai 2024-01-08 01:00
        # Nến 6: Thứ Hai 2024-01-08 02:00
        times = [
            pd.Timestamp("2024-01-05 19:00:00"),
            pd.Timestamp("2024-01-05 20:00:00"),
            pd.Timestamp("2024-01-05 21:00:00"),
            pd.Timestamp("2024-01-07 23:00:00"),
            pd.Timestamp("2024-01-08 00:00:00"),
            pd.Timestamp("2024-01-08 01:00:00"),
            pd.Timestamp("2024-01-08 02:00:00"),
        ]
        # Với k=2:
        # Nến 2 có High=2060 > 2050 (nến 0), 2055 (nến 1) và > 2045 (nến 3), 2040 (nến 4) -> Swing High
        # Nến 4 có Low=2030 < 2048 (nến 2), 2040 (nến 3) và < 2035 (nến 5), 2038 (nến 6) -> Swing Low
        highs = [2050.0, 2055.0, 2060.0, 2045.0, 2040.0, 2038.0, 2036.0]
        lows = [2045.0, 2048.0, 2052.0, 2040.0, 2030.0, 2035.0, 2038.0]

        df = pd.DataFrame({
            "time_utc": times,
            "high": highs,
            "low": lows,
        })

        k = 2
        swings = detect_swings(df, k=k)

        # 1. Swing High tại nến 2 (Thứ Sáu 21:00 - nến cuối tuần):
        # Nến 2 + 2 = nến 4 (Thứ Hai 00:00).
        # Thời điểm khả dụng PHẢI LÀ lúc nến 4 đóng cửa: Thứ Hai 01:00!
        sw_high = swings[swings["swing_type"] == "HIGH"]
        self.assertEqual(len(sw_high), 1)
        sw_fri = sw_high.iloc[0]
        self.assertEqual(sw_fri["bar_index"], 2)
        self.assertEqual(sw_fri["time_candle"], times[2])
        self.assertEqual(
            sw_fri["time_available"],
            pd.Timestamp("2024-01-08 01:00:00"),
            "Swing trước gap cuối tuần phải chờ nến i+k bên kia gap đóng cửa mới được khả dụng",
        )

        # 2. Swing Low tại nến 4 (Thứ Hai 00:00 - sau khoảng trống cuối tuần):
        # Lân cận trái gồm nến 3 (Chủ Nhật) và nến 2 (Thứ Sáu trước gap).
        # Nến 4 + 2 = nến 6 (Thứ Hai 02:00).
        # Thời điểm khả dụng là lúc nến 6 đóng cửa: Thứ Hai 03:00.
        sw_low = swings[swings["swing_type"] == "LOW"]
        self.assertEqual(len(sw_low), 1)
        sw_mon = sw_low.iloc[0]
        self.assertEqual(sw_mon["bar_index"], 4)
        self.assertEqual(sw_mon["time_candle"], times[4])
        self.assertEqual(
            sw_mon["time_available"],
            pd.Timestamp("2024-01-08 03:00:00"),
        )

    def test_05_nan_protection_assertion(self):
        """5. Bảo vệ chống NaN: DataFrame đầu vào chứa NaN phải bị từ chối ngay lập tức."""
        times = pd.date_range("2024-01-02 00:00", periods=5, freq="1h", tz="UTC")

        # Cột high có NaN
        df_nan_high = pd.DataFrame({
            "time_utc": times,
            "high": [1.0, 2.0, np.nan, 2.0, 1.0],
            "low": [0.5, 1.0, 1.5, 1.0, 0.5],
        })
        with self.assertRaises(ValueError):
            detect_swings(df_nan_high, k=1)

        # Cột low có NaN
        df_nan_low = pd.DataFrame({
            "time_utc": times,
            "high": [1.0, 2.0, 3.0, 2.0, 1.0],
            "low": [0.5, np.nan, 1.5, 1.0, 0.5],
        })
        with self.assertRaises(ValueError):
            detect_swings(df_nan_low, k=1)

        # Cột time_utc có NaT
        times_with_nat = list(times)
        times_with_nat[2] = pd.NaT
        df_nan_time = pd.DataFrame({
            "time_utc": times_with_nat,
            "high": [1.0, 2.0, 3.0, 2.0, 1.0],
            "low": [0.5, 1.0, 1.5, 1.0, 0.5],
        })
        with self.assertRaises(ValueError):
            detect_swings(df_nan_time, k=1)

    def test_06_prefix_invariance_h1_sliced_by_rows(self):
        """6. TEST BẤT BIẾN THEO TIỀN TỐ (BẮT BUỘC): Cắt theo các hàng của bảng H1 thật.

        Đảm bảo không look-ahead bias:
        Khi chạy detect_swings trên tập cắt đến hàng M của bảng H1,
        mọi swing có time_available <= cutoff_time (thời điểm đóng nến hàng M)
        PHẢI KHỚP 100% TỪNG PHẦN TỬ với kết quả chạy trên toàn bộ tập dữ liệu H1.
        """
        # Nạp dữ liệu H1 thật của 1 năm (2020)
        h1_df = load_h1("2020-01-01", "2020-12-31")
        self.assertGreater(len(h1_df), 1000, "Bảng H1 phải có đủ nến để test")

        k = 3
        swings_full = detect_swings(h1_df, k=k)

        # Thử nghiệm tại nhiều mốc cắt hàng khác nhau của bảng H1
        cut_rows = [300, 750, 1500, 2500, 4000]

        for cut_idx in cut_rows:
            if cut_idx >= len(h1_df):
                continue

            prefix_df = h1_df.iloc[:cut_idx].copy()
            # Thời điểm đóng cửa của nến cuối cùng trong tập tiền tố
            t_cutoff_close = prefix_df.iloc[-1]["time_utc"] + DEFAULT_H1_DURATION

            swings_prefix = detect_swings(prefix_df, k=k)
            # Lọc từ tập full các swing có time_available <= t_cutoff_close
            swings_full_filtered = swings_full[
                swings_full["time_available"] <= t_cutoff_close
            ].reset_index(drop=True)
            swings_prefix_reset = swings_prefix.reset_index(drop=True)

            self.assertEqual(
                len(swings_prefix_reset),
                len(swings_full_filtered),
                f"Tại điểm cắt hàng {cut_idx}: số lượng swing khả dụng phải bằng nhau tuyệt đối.",
            )

            # Đối chiếu từng dòng và từng trường
            for i in range(len(swings_prefix_reset)):
                r_p = swings_prefix_reset.iloc[i]
                r_f = swings_full_filtered.iloc[i]

                self.assertEqual(r_p["swing_type"], r_f["swing_type"])
                self.assertEqual(r_p["bar_index"], r_f["bar_index"])
                self.assertEqual(r_p["time_candle"], r_f["time_candle"])
                self.assertEqual(r_p["time_available"], r_f["time_available"])
                self.assertAlmostEqual(r_p["price"], r_f["price"], places=6)

    def test_07_get_swings_available_at(self):
        """7. get_swings_available_at: Lọc chính xác theo thời điểm khả dụng và loại swing."""
        times = pd.date_range("2024-01-02 00:00", periods=10, freq="1h")
        highs = [10.0, 12.0, 15.0, 20.0, 14.0, 11.0, 13.0, 22.0, 16.0, 12.0]
        lows = [8.0, 9.0, 10.0, 11.0, 10.0, 8.0, 9.0, 10.0, 11.0, 10.0]

        df = pd.DataFrame({"time_utc": times, "high": highs, "low": lows})
        swings = detect_swings(df, k=2)

        # Nến 3 là Swing High (20.0), time_available là nến 3+2=5 đóng cửa (times[5] + 1h = 06:00 UTC)
        sw_3 = swings[swings["bar_index"] == 3].iloc[0]
        t_avail = sw_3["time_available"]

        # 1 giây trước time_available: tuyệt đối không xuất hiện
        sw_before = get_swings_available_at(swings, t_avail - pd.Timedelta(seconds=1))
        self.assertNotIn(3, list(sw_before["bar_index"]))

        # Đúng time_available: xuất hiện chính xác
        sw_exact = get_swings_available_at(swings, t_avail)
        self.assertIn(3, list(sw_exact["bar_index"]))

        # Lọc theo loại:
        sw_high_only = get_swings_available_at(swings, t_avail, swing_type="HIGH")
        self.assertTrue((sw_high_only["swing_type"] == "HIGH").all())
        sw_low_only = get_swings_available_at(swings, t_avail, swing_type="LOW")
        self.assertTrue((sw_low_only["swing_type"] == "LOW").all())

    def test_08_development_h1_counts_and_tie_rate(self):
        """8. Đếm tần suất xuất hiện theo năm và tỷ lệ ca hòa trên H1 Development (2018–2025).

        Đóng băng tham số PRIMARY k=3 và EXPLORATORY k=5.
        Chỉ đếm số lượng sự kiện, không tính P&L, kiểm tra quy mô mẫu và tỷ lệ ca hòa.
        """
        h1_df = load_h1("2018-01-01", "2025-06-30")
        self.assertFalse(h1_df.empty, "Dữ liệu H1 Development không được rỗng")

        # 1. Phát hiện swings với k=3 (PRIMARY) và k=5 (EXPLORATORY)
        swings_k3 = detect_swings(h1_df, k=3)
        swings_k5 = detect_swings(h1_df, k=5)

        self.assertGreater(len(swings_k3), 4000, "Với k=3 PRIMARY, số swing phải >= 4000 qua 7.5 năm")
        self.assertGreater(len(swings_k5), 2500, "Với k=5 EXPLORATORY, số swing phải >= 2500")
        self.assertLess(len(swings_k5), len(swings_k3), "k=5 phải có ít swing hơn k=3")

        # 2. Phân phối theo năm với k=3 PRIMARY
        swings_k3_copy = swings_k3.copy()
        swings_k3_copy["year"] = pd.to_datetime(swings_k3_copy["time_candle"]).dt.year
        counts_by_year = swings_k3_copy.groupby(["year", "swing_type"]).size().unstack(fill_value=0)

        for y in range(2018, 2025):
            self.assertIn(y, counts_by_year.index)
            # Mỗi năm phải có dồi dào cả đỉnh và đáy (> 200 mỗi loại)
            self.assertGreater(counts_by_year.loc[y, "HIGH"], 200)
            self.assertGreater(counts_by_year.loc[y, "LOW"], 200)

        # 3. Đo tỷ lệ ca hòa trên dữ liệu giá vàng thật H1
        highs = h1_df["high"].to_numpy()
        k = 3
        candidates = 0
        ties = 0
        for i in range(k, len(highs) - k):
            h = highs[i]
            if all(h >= highs[i - j] and h >= highs[i + j] for j in range(1, k + 1)):
                candidates += 1
                if any(h == highs[i - j] or h == highs[i + j] for j in range(1, k + 1)):
                    ties += 1

        tie_pct = (ties / candidates) * 100.0 if candidates > 0 else 0.0
        # Xác nhận tỷ lệ ca hòa trên giá vàng thật (0.01 tick) rất thấp (< 2.5%)
        self.assertLess(
            tie_pct,
            2.5,
            f"Tỷ lệ ca hòa trên dữ liệu vàng thật ({tie_pct:.2f}%) phải < 2.5%",
        )

    def test_09_timezone_non_utc_conversion(self):
        """9. Kiểm tra xử lý múi giờ non-UTC: Không bị lệch giờ khi truyền dữ liệu có timezone khác UTC."""
        # Chuỗi nến có múi giờ Asia/Bangkok (UTC+7)
        # 07:00 Bangkok = 00:00 UTC
        times_bkk = pd.date_range("2024-01-02 07:00", periods=5, freq="1h", tz="Asia/Bangkok")
        highs = [1.0, 2.0, 5.0, 2.0, 1.0]
        lows = [0.5, 1.0, 1.5, 1.0, 0.5]

        df = pd.DataFrame({"time_utc": times_bkk, "high": highs, "low": lows})
        swings = detect_swings(df, k=2)

        self.assertEqual(len(swings), 1)
        sw = swings.iloc[0]
        # time_candle phải chuẩn hóa về UTC naive: 2024-01-02 02:00:00 (tức 09:00 Bangkok chuyển sang UTC)
        expected_utc = pd.Timestamp("2024-01-02 02:00:00")
        self.assertEqual(sw["time_candle"], expected_utc)

        # get_swings_available_at với mốc giờ New York (UTC-5):
        # 2024-01-02 00:00:00 NY = 2024-01-02 05:00:00 UTC -> Đúng lúc available (times[4] + 1h = 04:00+1h = 05:00 UTC)
        t_ny = pd.Timestamp("2024-01-02 00:00:00", tz="America/New_York")
        sw_avail = get_swings_available_at(swings, t_ny)
        self.assertEqual(len(sw_avail), 1, "Truy vấn bằng timestamp có timezone New York phải tự đổi sang UTC đúng")

    def test_10_missing_columns_precedence_on_empty_df(self):
        """10. Thứ tự kiểm tra: DataFrame rỗng thiếu cột phải raise KeyError trước."""
        empty_df_wrong_cols = pd.DataFrame()
        with self.assertRaises(KeyError):
            detect_swings(empty_df_wrong_cols, k=2)


if __name__ == "__main__":
    unittest.main()

