"""Kiểm thử mô-đun structure.py (Bước 2: Cấu trúc thị trường BOS & CHoCH chuẩn SMC).

Bao gồm các bài kiểm tra bắt buộc:
1. Test khởi tạo NEUTRAL: Không sinh tín hiệu sai lệch, chỉ sinh BOS đầu tiên khi phá vỡ mức đầu tiên, không sinh CHoCH khi đang NEUTRAL.
2. Test quy tắc "Chỉ phá một lần" (First Break Only): Nến đóng trên mức cũ không được sinh liên tiếp BOS ở các nến sau.
3. Test ca "Swing cũ bị che khuất" (Shadowed Old Swings Elimination):
   Ví dụ Swing 100 và Swing 110; nến đóng 105 không BOS, nến đóng 112 sinh BOS phá 110 và xóa sổ 100; nến đóng 108 không sinh BOS ma.
4. Test CHoCH lật trạng thái:
   - BEARISH gặp nến đóng vượt Protected High -> BULLISH_CHOCH, lật sang BULLISH.
   - BULLISH gặp nến đóng xuyên Protected Low -> BEARISH_CHOCH, lật sang BEARISH.
   - Các lần phá cùng chiều sau đó là BOS (tiếp diễn).
5. Test bất biến theo tiền tố (Prefix Invariance Test):
   Cắt theo hàng của bảng H1 thật từ load_h1, đối chiếu 100% từng dòng với tập full.
6. Test bảo vệ chống NaN.
7. Test get_structure_available_at.
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
from src.backtest.smc.structure import detect_structure, get_structure_available_at
from src.backtest.smc.swings import DEFAULT_H1_DURATION, detect_swings


class TestStructureModule(unittest.TestCase):
    def test_01_neutral_initialization_no_false_choch(self):
        """1. NEUTRAL ban đầu: Chỉ sinh BOS khởi tạo xu hướng, không sinh CHoCH."""
        # 10 nến H1:
        # Nến 0, 1, 2, 3, 4 tạo Swing High tại nến 2 (k=2) giá 1520.0
        # Swing High khả dụng tại nến 4 đóng cửa (times[4] + 1h = times[5])
        times = pd.date_range("2024-01-02 00:00", periods=10, freq="1h", tz="UTC")
        highs = [1500.0, 1510.0, 1520.0, 1512.0, 1505.0, 1508.0, 1525.0, 1522.0, 1520.0, 1518.0]
        lows = [1495.0, 1502.0, 1508.0, 1504.0, 1498.0, 1500.0, 1506.0, 1515.0, 1512.0, 1510.0]
        # Nến 6 đóng cửa tại 1524.0 (> 1520.0)
        closes = [1498.0, 1508.0, 1515.0, 1508.0, 1502.0, 1505.0, 1524.0, 1520.0, 1515.0, 1512.0]

        df = pd.DataFrame({
            "time_utc": times,
            "open": highs,
            "high": highs,
            "low": lows,
            "close": closes,
        })

        struct_df = detect_structure(df, k=2)

        # Nến 6 phá đỉnh 1520.0 khi đang NEUTRAL -> Phải sinh BULLISH_BOS (không phải CHoCH)
        self.assertEqual(len(struct_df), 1)
        ev = struct_df.iloc[0]
        self.assertEqual(ev["event_type"], "BULLISH_BOS")
        self.assertEqual(ev["bar_index"], 6)
        self.assertEqual(ev["price_broken"], 1520.0)
        self.assertEqual(ev["prior_trend"], "NEUTRAL")
        self.assertEqual(ev["new_trend"], "BULLISH")
        self.assertEqual(ev["time_available"], times[6] + DEFAULT_H1_DURATION)

    def test_02_bos_first_break_only_rule(self):
        """2. Quy tắc First Break Only: Nến tiếp theo đóng trên mức cũ không được sinh thêm BOS."""
        times = pd.date_range("2024-01-02 00:00", periods=9, freq="1h", tz="UTC")
        # Nến 2 tạo Swing High tại 1520.0 (k=2), khả dụng tại nến 4 đóng
        highs = [1500.0, 1510.0, 1520.0, 1512.0, 1505.0, 1525.0, 1528.0, 1526.0, 1522.0]
        lows = [1495.0, 1502.0, 1508.0, 1504.0, 1498.0, 1500.0, 1520.0, 1518.0, 1515.0]
        # Nến 5 đóng tại 1523.0 (> 1520.0) -> BOS
        # Nến 6 đóng tại 1526.0 (> 1520.0) -> KHÔNG ĐƯỢC sinh thêm BOS
        # Nến 7 đóng tại 1524.0 (> 1520.0) -> KHÔNG ĐƯỢC sinh thêm BOS
        closes = [1498.0, 1508.0, 1515.0, 1508.0, 1502.0, 1523.0, 1526.0, 1524.0, 1520.0]

        df = pd.DataFrame({
            "time_utc": times,
            "open": highs,
            "high": highs,
            "low": lows,
            "close": closes,
        })

        struct_df = detect_structure(df, k=2)

        # Chỉ có duy nhất 1 BOS tại nến 5
        self.assertEqual(len(struct_df), 1)
        self.assertEqual(struct_df.iloc[0]["bar_index"], 5)
        self.assertEqual(struct_df.iloc[0]["event_type"], "BULLISH_BOS")

    def test_03_shadowed_swings_elimination(self):
        """3. Lỗ hổng 'Swing cũ bị che khuất' (Shadowed Swings):

        Tái hiện chính xác ví dụ của user:
        - Swing High 1 tại 100.0 (bar 2, khả dụng bar 4)
        - Swing High 2 tại 110.0 (bar 6, hình thành nhờ râu vượt 100 nhưng không nến nào đóng trên 100)
        - Bar 9 đóng tại 105.0: Không BOS (tham chiếu là 110.0)
        - Bar 10 đóng tại 112.0: Phá 110.0 -> Sinh BULLISH_BOS, ĐỒNG THỜI xóa sổ Swing 1 (100.0 <= 110.0)
        - Bar 11 đóng tại 108.0: KHÔNG ĐƯỢC sinh BOS ma cho mức 100.0!
        """
        times = pd.date_range("2024-01-02 00:00", periods=14, freq="1h", tz="UTC")
        highs = [
            90.0, 95.0, 100.0, 96.0, 92.0,  # 0..4: Bar 2 là Swing High 100.0 (k=2)
            98.0, 110.0, 97.0, 95.0,        # 5..8: Bar 6 là Swing High 110.0 (râu lên 110 nhưng close <= 98)
            106.0, 113.0, 109.0, 102.0, 100.0
        ]
        lows = [h - 10.0 for h in highs]
        # Close prices:
        # bar 0..8: đều <= 98.0 (không đóng trên 100.0)
        # bar 9: close 105.0 (không phá 110.0)
        # bar 10: close 112.0 (phá 110.0)
        # bar 11: close 108.0 (100.0 đã bị xóa, không sinh BOS ma!)
        closes = [
            88.0, 93.0, 95.0, 94.0, 90.0,
            96.0, 98.0, 97.0, 95.0,
            105.0, 112.0, 108.0, 100.0, 98.0
        ]

        df = pd.DataFrame({
            "time_utc": times,
            "open": highs,
            "high": highs,
            "low": lows,
            "close": closes,
        })

        struct_df = detect_structure(df, k=2)

        # Kiểm tra: Chỉ có duy nhất 1 BOS tại bar 10 phá mức 110.0
        # Bar 11 đóng tại 108.0 tuyệt đối KHÔNG ĐƯỢC sinh BOS cho mức 100.0
        self.assertEqual(len(struct_df), 1, "Chỉ được sinh đúng 1 BOS, không có BOS ma tại bar 11")
        ev = struct_df.iloc[0]
        self.assertEqual(ev["bar_index"], 10)
        self.assertEqual(ev["price_broken"], 110.0)
        self.assertEqual(ev["event_type"], "BULLISH_BOS")

    def test_04_choch_state_flip(self):
        """4. Test CHoCH lật trạng thái:

        - Đang BEARISH (sau Bearish BOS), nến đóng trên Protected High -> BULLISH_CHOCH, lật sang BULLISH.
        - Đang BULLISH, nến đóng dưới Protected Low -> BEARISH_CHOCH, lật sang BEARISH.
        """
        # Tạo chuỗi nến có đủ cả giảm tạo đáy rồi đảo chiều vượt đỉnh
        times = pd.date_range("2024-01-02 00:00", periods=16, freq="1h", tz="UTC")

        # Nến 2 tạo Swing High tại 1520.0
        # Nến 5 tạo Swing Low tại 1478.0
        # Nến 8 đóng tại 1465.0 (< 1478.0) -> Xác lập BEARISH_BOS (xu hướng chuyển sang BEARISH)
        # Nến 11 đóng tại 1525.0 (> 1520.0) -> Khi đang BEARISH mà phá đỉnh 1520.0 -> BULLISH_CHOCH!
        highs = [
            1500.0, 1515.0, 1520.0, 1510.0, 1495.0, 1490.0, 1485.0, 1480.0,
            1475.0, 1500.0, 1515.0, 1530.0, 1535.0, 1532.0, 1528.0, 1525.0
        ]
        lows = [
            1490.0, 1505.0, 1510.0, 1490.0, 1482.0, 1478.0, 1470.0, 1465.0,
            1460.0, 1485.0, 1500.0, 1518.0, 1520.0, 1522.0, 1520.0, 1515.0
        ]
        closes = [
            1495.0, 1510.0, 1515.0, 1495.0, 1488.0, 1482.0, 1475.0, 1470.0,
            1465.0, 1498.0, 1512.0, 1525.0, 1530.0, 1528.0, 1522.0, 1518.0
        ]

        df = pd.DataFrame({
            "time_utc": times,
            "open": highs,
            "high": highs,
            "low": lows,
            "close": closes,
        })

        # Cung cấp swings thủ công để kiểm soát chính xác trạng thái
        # Swing High tại bar 2 (1520.0, khả dụng bar 4 close = times[5])
        # Swing Low tại bar 5 (1478.0, khả dụng bar 7 close = times[8])
        swings_data = [
            {
                "swing_id": 0,
                "swing_type": "HIGH",
                "bar_index": 2,
                "time_candle": times[2],
                "time_available": times[4] + DEFAULT_H1_DURATION,
                "price": 1520.0,
                "k": 2,
            },
            {
                "swing_id": 1,
                "swing_type": "LOW",
                "bar_index": 5,
                "time_candle": times[5],
                "time_available": times[7] + DEFAULT_H1_DURATION,
                "price": 1478.0,
                "k": 2,
            },
        ]
        swings_df = pd.DataFrame(swings_data)

        # Bar 8 mở lúc times[8]. Swing Low (1478.0) khả dụng lúc times[8].
        # Close bar 8 là 1465.0 < 1478.0 -> Khởi tạo BEARISH_BOS!
        # Bar 11 đóng lúc 1525.0 > 1520.0 (Protected High lúc này là Swing 0) -> BULLISH_CHOCH!
        struct_df = detect_structure(df, swings_df=swings_df, bar_duration=DEFAULT_H1_DURATION)

        self.assertEqual(len(struct_df), 2)

        # Sự kiện 1: Phá đáy 1478.0 khi NEUTRAL -> BEARISH_BOS
        ev1 = struct_df.iloc[0]
        self.assertEqual(ev1["event_type"], "BEARISH_BOS")
        self.assertEqual(ev1["price_broken"], 1478.0)
        self.assertEqual(ev1["new_trend"], "BEARISH")

        # Sự kiện 2: Khi đang BEARISH, phá đỉnh 1520.0 -> BULLISH_CHOCH
        ev2 = struct_df.iloc[1]
        self.assertEqual(ev2["event_type"], "BULLISH_CHOCH")
        self.assertEqual(ev2["price_broken"], 1520.0)
        self.assertEqual(ev2["prior_trend"], "BEARISH")
        self.assertEqual(ev2["new_trend"], "BULLISH")

    def test_05_prefix_invariance_on_real_h1(self):
        """5. TEST BẤT BIẾN THEO TIỀN TỐ TRÊN DỮ LIỆU H1 THẬT (BẮT BUỘC).

        Cắt nến H1 theo từng hàng cụ thể (không cắt M1), chạy detect_structure trên tập cắt ngắn
        và so sánh với tập full. Mọi sự kiện có time_available <= cutoff_time phải khớp 100% từng giá trị.
        """
        h1_df = load_h1("2020-01-01", "2020-12-31")
        self.assertGreater(len(h1_df), 1000)

        # Chạy detect_structure trên toàn bộ năm 2020
        struct_full = detect_structure(h1_df, k=3)
        self.assertGreater(len(struct_full), 20, "Năm 2020 phải có nhiều sự kiện cấu trúc")

        # Cắt tại các hàng khác nhau của bảng H1
        cut_rows = [500, 1200, 2500, 4000]

        for cut_idx in cut_rows:
            if cut_idx >= len(h1_df):
                continue

            prefix_df = h1_df.iloc[:cut_idx].copy()
            cutoff_close = prefix_df.iloc[-1]["time_utc"] + DEFAULT_H1_DURATION

            struct_prefix = detect_structure(prefix_df, k=3)
            struct_full_filtered = struct_full[
                struct_full["time_available"] <= cutoff_close
            ].reset_index(drop=True)
            struct_prefix_reset = struct_prefix.reset_index(drop=True)

            self.assertEqual(
                len(struct_prefix_reset),
                len(struct_full_filtered),
                f"Tại điểm cắt hàng {cut_idx}: số lượng sự kiện cấu trúc phải bằng nhau",
            )

            # Đối chiếu chi tiết từng thuộc tính
            for i in range(len(struct_prefix_reset)):
                r_p = struct_prefix_reset.iloc[i]
                r_f = struct_full_filtered.iloc[i]

                self.assertEqual(r_p["event_type"], r_f["event_type"])
                self.assertEqual(r_p["bar_index"], r_f["bar_index"])
                self.assertEqual(r_p["time_candle"], r_f["time_candle"])
                self.assertEqual(r_p["time_available"], r_f["time_available"])
                self.assertAlmostEqual(r_p["price_broken"], r_f["price_broken"], places=6)
                self.assertAlmostEqual(r_p["close_price"], r_f["close_price"], places=6)
                self.assertEqual(r_p["prior_trend"], r_f["prior_trend"])
                self.assertEqual(r_p["new_trend"], r_f["new_trend"])

    def test_06_nan_protection(self):
        """6. Bảo vệ chống NaN trong detect_structure."""
        times = pd.date_range("2024-01-02 00:00", periods=5, freq="1h", tz="UTC")

        # Close có NaN
        df_nan = pd.DataFrame({
            "time_utc": times,
            "open": [1.0, 2.0, 3.0, 4.0, 5.0],
            "high": [1.0, 2.0, 3.0, 4.0, 5.0],
            "low": [0.5, 1.0, 1.5, 2.0, 2.5],
            "close": [1.0, np.nan, 3.0, 4.0, 5.0],
        })
        with self.assertRaises(ValueError):
            detect_structure(df_nan, k=1)

    def test_07_get_structure_available_at(self):
        """7. get_structure_available_at: Lọc chính xác theo thời điểm khả dụng và event_type."""
        times = pd.date_range("2024-01-02 00:00", periods=10, freq="1h", tz="UTC")
        highs = [10.0, 12.0, 15.0, 20.0, 14.0, 11.0, 25.0, 22.0, 16.0, 12.0]
        lows = [8.0, 9.0, 10.0, 11.0, 10.0, 8.0, 10.0, 10.0, 11.0, 10.0]
        closes = [9.0, 11.0, 14.0, 18.0, 12.0, 10.0, 22.0, 20.0, 15.0, 11.0]

        df = pd.DataFrame({
            "time_utc": times,
            "open": highs,
            "high": highs,
            "low": lows,
            "close": closes,
        })

        struct_df = detect_structure(df, k=2)
        if not struct_df.empty:
            t_avail = struct_df.iloc[0]["time_available"]
            # 1 giây trước t_avail -> không có
            before = get_structure_available_at(struct_df, t_avail - pd.Timedelta(seconds=1))
            self.assertEqual(len(before), 0)

            # Đúng t_avail -> có
            exact = get_structure_available_at(struct_df, t_avail)
            self.assertGreaterEqual(len(exact), 1)


if __name__ == "__main__":
    unittest.main()
