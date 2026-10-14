"""Kiểm thử mô-đun structure.py (Bước 2: Cấu trúc thị trường BOS & CHoCH chuẩn SMC v2.1).

Bao gồm các bài kiểm tra toàn diện và chặt chẽ:
1. Test khởi tạo NEUTRAL: Chỉ sinh BULLISH_INIT_BREAK / BEARISH_INIT_BREAK, không sinh CHoCH hay BOS.
2. Test quy tắc "Chỉ phá một lần" (First Break Only): Nến đóng trên mức cũ không sinh liên tiếp sự kiện ở các nến sau.
3. Test tính hợp lệ nến & che khuất cơ bản: Dữ liệu nến luôn thỏa low <= close <= high; swing 100 bị xóa khi phá 110.
4. Test quy tắc che khuất v2 (Shadowed Swings v2 - Max Rule):
   High A=108 (cũ) và High B=104 (mới). Bar 5 đóng 109 phá B, quy tắc max(104, 109)=109 xóa luôn A (108).
   Bar 6 đóng 110 không được sinh sự kiện ma.
5. Test lật trạng thái BEARISH -> BULLISH CHoCH và BULLISH_BOS tiếp diễn (tiêm swing độc lập).
6. Test lật trạng thái đối xứng BULLISH -> BEARISH CHoCH và BEARISH_BOS tiếp diễn (tiêm swing độc lập).
7. Test ca biên Close bằng đúng mức swing (Close == P): So sánh nghiêm ngặt (> hoặc <) nên không sinh sự kiện.
8. Test râu vượt mức nhưng đóng cửa dưới mức (High > P, Close <= P): Không sinh sự kiện (chỉ là sweep).
9. Test tính khả dụng của swing (Timing & Strict Boundary Test):
   - time_available > open_time: Swing chưa khả dụng, không được dùng.
   - time_available == open_time: Ca biên, swing đã khả dụng và được nến đó phá ngay (kiểm tra chặt chẽ <=).
10. Test hai phía độc lập: Phá Swing High không làm suy giảm hoặc thay đổi danh sách unbroken Swing Lows.
11. Test bất biến theo tiền tố (Prefix Invariance Test) trên 12 sự kiện thực tế rải đều:
    - Cắt tại biên sự kiện (b và b+1).
    - Cắt tại biên xác nhận swing (c_conf và c_conf+1) để chống rò rỉ look-ahead.
    - Đối chiếu 100% mọi cột bao gồm cả broken_swing_id và broken_swing_bar_index.
12. Test kiểm tra đầu vào toàn diện: NaN ở close/high/low, NaT ở time_utc, trùng lặp thời gian,
    thiếu cột (cả df rỗng lẫn df có dữ liệu).
13. Test get_structure_available_at: Kiểm tra timing khả dụng và truy vấn bằng Timestamp có múi giờ (UTC+7).
14. Test Hội tụ Warm-up Period (Empirical Convergence Test):
    Chứng minh 24 tháng warm-up hội tụ 100% trên toàn bộ 4.486 sự kiện của tập Development 2018-2025
    với mặc định toàn bộ lịch sử (warmup_months=None). Kiểm tra tỷ lệ trùng khớp thực sự của 12m và 6m.
15. Test 5 bất biến vật lý & Bảng phân bố sự kiện theo năm trên tập H1 Development (2018–2025):
    Không có INIT_BREAK, prior_trend đầu tiên không phải NEUTRAL.
16. Test truyền swings_df rỗng một cách tường minh phải trả về DataFrame rỗng (không tự tính lại).
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
from src.backtest.smc.structure import detect_structure, get_structure_available_at, load_structure
from src.backtest.smc.swings import DEFAULT_H1_DURATION, detect_swings


class TestStructureModule(unittest.TestCase):
    def test_01_neutral_initialization_no_false_choch(self):
        """1. NEUTRAL ban đầu: Chỉ sinh INIT_BREAK khởi tạo xu hướng, không sinh CHoCH hay BOS."""
        times = pd.date_range("2024-01-02 00:00", periods=10, freq="1h")
        highs = [1500.0, 1510.0, 1520.0, 1512.0, 1505.0, 1508.0, 1525.0, 1522.0, 1520.0, 1518.0]
        lows = [1495.0, 1502.0, 1508.0, 1504.0, 1498.0, 1500.0, 1506.0, 1515.0, 1512.0, 1510.0]
        closes = [1498.0, 1508.0, 1515.0, 1508.0, 1502.0, 1505.0, 1524.0, 1520.0, 1515.0, 1512.0]

        df = pd.DataFrame({
            "time_utc": times,
            "open": highs,
            "high": highs,
            "low": lows,
            "close": closes,
        })

        struct_df = detect_structure(df, k=2)

        self.assertEqual(len(struct_df), 1)
        ev = struct_df.iloc[0]
        self.assertEqual(ev["event_type"], "BULLISH_INIT_BREAK")
        self.assertEqual(ev["bar_index"], 6)
        self.assertEqual(ev["price_broken"], 1520.0)
        self.assertEqual(ev["prior_trend"], "NEUTRAL")
        self.assertEqual(ev["new_trend"], "BULLISH")
        self.assertEqual(ev["time_available"], times[6] + DEFAULT_H1_DURATION)

    def test_02_bos_first_break_only_rule(self):
        """2. Quy tắc First Break Only: Nến tiếp theo đóng trên mức cũ không được sinh thêm sự kiện."""
        times = pd.date_range("2024-01-02 00:00", periods=9, freq="1h")
        highs = [1500.0, 1510.0, 1520.0, 1512.0, 1505.0, 1525.0, 1528.0, 1526.0, 1522.0]
        lows = [1495.0, 1502.0, 1508.0, 1504.0, 1498.0, 1500.0, 1520.0, 1518.0, 1515.0]
        closes = [1498.0, 1508.0, 1515.0, 1508.0, 1502.0, 1523.0, 1526.0, 1524.0, 1520.0]

        df = pd.DataFrame({
            "time_utc": times,
            "open": highs,
            "high": highs,
            "low": lows,
            "close": closes,
        })

        struct_df = detect_structure(df, k=2)

        self.assertEqual(len(struct_df), 1)
        self.assertEqual(struct_df.iloc[0]["bar_index"], 5)
        self.assertEqual(struct_df.iloc[0]["event_type"], "BULLISH_INIT_BREAK")

    def test_03_shadowed_swings_elimination_candle_validity(self):
        """3. Lỗ hổng 'Swing cũ bị che khuất' & Kiểm tra tính hợp lệ của nến tổng hợp:

        - low <= close <= high và low <= open <= high cho mọi nến.
        - Bar 2 là Swing High 100.0 (k=2).
        - Bar 6 là Swing High 110.0 (râu lên 110 nhưng close=98.0).
        - Bar 9 đóng 105.0: Không có sự kiện (tham chiếu là 110.0).
        - Bar 10 đóng 112.0: Phá 110.0 -> Sinh BULLISH_INIT_BREAK, đồng thời xóa Swing 1 (100.0 <= 112.0).
        - Bar 11 đóng 108.0: Không sinh sự kiện ma cho mức 100.0.
        """
        times = pd.date_range("2024-01-02 00:00", periods=14, freq="1h")
        highs = [
            90.0, 95.0, 100.0, 96.0, 92.0,
            98.0, 110.0, 97.0, 95.0,
            106.0, 113.0, 109.0, 102.0, 100.0
        ]
        closes = [
            88.0, 93.0, 95.0, 94.0, 90.0,
            96.0, 98.0, 97.0, 95.0,
            105.0, 112.0, 108.0, 100.0, 98.0
        ]
        opens = [c - 1.0 for c in closes]
        lows = [min(o, c) - 5.0 for o, c in zip(opens, closes)]

        for b in range(14):
            self.assertLessEqual(lows[b], opens[b])
            self.assertLessEqual(lows[b], closes[b])
            self.assertGreaterEqual(highs[b], opens[b])
            self.assertGreaterEqual(highs[b], closes[b])

        df = pd.DataFrame({
            "time_utc": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
        })

        struct_df = detect_structure(df, k=2)

        self.assertEqual(len(struct_df), 1, "Chỉ được sinh đúng 1 sự kiện, không có BOS ma tại bar 11")
        ev = struct_df.iloc[0]
        self.assertEqual(ev["bar_index"], 10)
        self.assertEqual(ev["price_broken"], 110.0)
        self.assertEqual(ev["event_type"], "BULLISH_INIT_BREAK")

    def test_04_shadowed_swings_v2_max_rule(self):
        """4. QUY TẮC CHE KHUẤT V2 (Phân biệt rạch ròi giữa <= P và <= max(P, Close[t])):

        Kịch bản phản chứng quy tắc v1:
        - Swing High A = 108.0 (cũ hơn, khả dụng tại bar 3).
        - Swing High B = 104.0 (mới nhất, khả dụng tại bar 4).
        - Tại bar 5: Protected High là B (104.0).
        - Bar 5 đóng cửa tại 109.0:
          * Phá vỡ B (104.0) -> Sinh BULLISH_INIT_BREAK.
          * Với quy tắc v1 (xóa <= P = 104.0): A (108.0) > 104.0 nên KHÔNG BỊ XÓA!
          * Với quy tắc v2 (xóa <= max(P, Close[t]) = max(104, 109) = 109.0): A (108.0) <= 109.0 BỊ XÓA NGAY!
        - Bar 6 đóng cửa tại 110.0:
          * Theo v1: Sẽ sinh thêm BOS ma phá mức 108.0 của A (SAI!).
          * Theo v2: A đã bị xóa sạch, không sinh bất kỳ sự kiện nào (ĐÚNG!).
        """
        times = pd.date_range("2024-01-02 00:00", periods=8, freq="1h")
        highs = [102.0, 102.0, 102.0, 102.0, 102.0, 112.0, 115.0, 105.0]
        closes = [100.0, 100.0, 100.0, 100.0, 100.0, 109.0, 110.0, 101.0]
        lows = [95.0] * 8
        opens = [100.0] * 8

        df = pd.DataFrame({
            "time_utc": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
        })

        swings_data = [
            {
                "swing_id": 0,
                "swing_type": "HIGH",
                "bar_index": 1,
                "time_candle": times[1],
                "time_available": times[3],
                "price": 108.0,
                "k": 2,
            },
            {
                "swing_id": 1,
                "swing_type": "HIGH",
                "bar_index": 2,
                "time_candle": times[2],
                "time_available": times[4],
                "price": 104.0,
                "k": 2,
            },
        ]
        swings_df = pd.DataFrame(swings_data)

        struct_df = detect_structure(df, swings_df=swings_df)

        self.assertEqual(len(struct_df), 1)
        self.assertEqual(struct_df.iloc[0]["bar_index"], 5)
        self.assertEqual(struct_df.iloc[0]["price_broken"], 104.0)
        self.assertEqual(struct_df.iloc[0]["broken_swing_id"], 1)

    def test_05_choch_bearish_to_bullish_and_subsequent_bos(self):
        """5. Test lật trạng thái BEARISH -> BULLISH CHoCH và BULLISH_BOS tiếp diễn.

        Lưu ý: Test này sử dụng swings_df được cung cấp trực tiếp (injected swings)
        để kiểm thử độc lập máy trạng thái (state machine) của detect_structure
        mà không phụ thuộc vào bộ lọc hình học của detect_swings.
        """
        times = pd.date_range("2024-01-02 00:00", periods=20, freq="1h")
        highs = [
            1500.0, 1515.0, 1520.0, 1510.0, 1495.0, 1490.0, 1485.0, 1480.0,
            1475.0, 1500.0, 1515.0, 1530.0, 1535.0, 1532.0, 1528.0, 1525.0,
            1538.0, 1542.0, 1539.0, 1535.0
        ]
        lows = [h - 15.0 for h in highs]
        closes = [
            1495.0, 1510.0, 1515.0, 1495.0, 1488.0, 1482.0, 1475.0, 1470.0,
            1465.0, 1498.0, 1512.0, 1525.0, 1530.0, 1528.0, 1522.0, 1524.0,
            1536.0, 1540.0, 1537.0, 1533.0
        ]

        df = pd.DataFrame({
            "time_utc": times,
            "open": highs,
            "high": highs,
            "low": lows,
            "close": closes,
        })

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
            {
                "swing_id": 2,
                "swing_type": "HIGH",
                "bar_index": 12,
                "time_candle": times[12],
                "time_available": times[14] + DEFAULT_H1_DURATION,
                "price": 1535.0,
                "k": 2,
            },
        ]
        swings_df = pd.DataFrame(swings_data)

        struct_df = detect_structure(df, swings_df=swings_df)

        self.assertEqual(len(struct_df), 3)

        ev1 = struct_df.iloc[0]
        self.assertEqual(ev1["event_type"], "BEARISH_INIT_BREAK")
        self.assertEqual(ev1["price_broken"], 1478.0)
        self.assertEqual(ev1["new_trend"], "BEARISH")

        ev2 = struct_df.iloc[1]
        self.assertEqual(ev2["event_type"], "BULLISH_CHOCH")
        self.assertEqual(ev2["price_broken"], 1520.0)
        self.assertEqual(ev2["prior_trend"], "BEARISH")
        self.assertEqual(ev2["new_trend"], "BULLISH")

        ev3 = struct_df.iloc[2]
        self.assertEqual(ev3["event_type"], "BULLISH_BOS")
        self.assertEqual(ev3["price_broken"], 1535.0)
        self.assertEqual(ev3["prior_trend"], "BULLISH")
        self.assertEqual(ev3["new_trend"], "BULLISH")

    def test_06_choch_bullish_to_bearish_and_subsequent_bos(self):
        """6. Test lật trạng thái đối xứng BULLISH -> BEARISH CHoCH và BEARISH_BOS tiếp diễn."""
        times = pd.date_range("2024-01-02 00:00", periods=16, freq="1h")
        closes = [
            1510.0, 1515.0, 1512.0, 1514.0,
            1525.0,  # Bar 4: close 1525 > 1520 -> BULLISH_INIT_BREAK (BULLISH)
            1518.0, 1510.0,
            1495.0,  # Bar 7: close 1495 < 1500 -> BEARISH_CHOCH (Lật sang BEARISH)
            1490.0, 1488.0,
            1480.0,  # Bar 10: close 1480 < 1485 -> BEARISH_BOS (Tiếp diễn giảm)
            1478.0, 1475.0, 1470.0, 1468.0, 1465.0
        ]
        highs = [c + 5.0 for c in closes]
        lows = [c - 5.0 for c in closes]

        df = pd.DataFrame({
            "time_utc": times,
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
        })

        swings_data = [
            {
                "swing_id": 0,
                "swing_type": "HIGH",
                "bar_index": 1,
                "time_candle": times[1],
                "time_available": times[3],
                "price": 1520.0,
                "k": 2,
            },
            {
                "swing_id": 1,
                "swing_type": "LOW",
                "bar_index": 2,
                "time_candle": times[2],
                "time_available": times[4],
                "price": 1500.0,
                "k": 2,
            },
            {
                "swing_id": 2,
                "swing_type": "LOW",
                "bar_index": 8,
                "time_candle": times[8],
                "time_available": times[9] + DEFAULT_H1_DURATION,
                "price": 1485.0,
                "k": 2,
            },
        ]
        swings_df = pd.DataFrame(swings_data)

        struct_df = detect_structure(df, swings_df=swings_df)

        self.assertEqual(len(struct_df), 3)

        self.assertEqual(struct_df.iloc[0]["event_type"], "BULLISH_INIT_BREAK")
        self.assertEqual(struct_df.iloc[0]["prior_trend"], "NEUTRAL")
        self.assertEqual(struct_df.iloc[0]["new_trend"], "BULLISH")

        self.assertEqual(struct_df.iloc[1]["event_type"], "BEARISH_CHOCH")
        self.assertEqual(struct_df.iloc[1]["price_broken"], 1500.0)
        self.assertEqual(struct_df.iloc[1]["prior_trend"], "BULLISH")
        self.assertEqual(struct_df.iloc[1]["new_trend"], "BEARISH")

        self.assertEqual(struct_df.iloc[2]["event_type"], "BEARISH_BOS")
        self.assertEqual(struct_df.iloc[2]["price_broken"], 1485.0)
        self.assertEqual(struct_df.iloc[2]["prior_trend"], "BEARISH")
        self.assertEqual(struct_df.iloc[2]["new_trend"], "BEARISH")

    def test_07_close_strictly_equal_to_swing_no_event(self):
        """7. Ca biên: Giá đóng cửa bằng đúng mức swing (Close == P)."""
        times = pd.date_range("2024-01-02 00:00", periods=5, freq="1h")
        closes = [1500.0, 1510.0, 1515.0, 1520.0, 1518.0]
        highs = [c + 2.0 for c in closes]
        lows = [c - 2.0 for c in closes]

        df = pd.DataFrame({
            "time_utc": times,
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
        })

        swings_df = pd.DataFrame([{
            "swing_id": 0,
            "swing_type": "HIGH",
            "bar_index": 1,
            "time_candle": times[1],
            "time_available": times[2],
            "price": 1520.0,
            "k": 1,
        }])

        struct_df = detect_structure(df, swings_df=swings_df)
        self.assertTrue(struct_df.empty, "Close == P không được sinh sự kiện")

    def test_08_wick_exceeds_close_below_no_event(self):
        """8. Râu nến vượt mức swing nhưng giá đóng cửa nằm dưới mức (High > P, Close <= P)."""
        times = pd.date_range("2024-01-02 00:00", periods=5, freq="1h")
        highs = [1510.0, 1515.0, 1518.0, 1528.0, 1515.0]
        closes = [1505.0, 1510.0, 1512.0, 1518.0, 1510.0]
        lows = [1495.0] * 5

        df = pd.DataFrame({
            "time_utc": times,
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
        })

        swings_df = pd.DataFrame([{
            "swing_id": 0,
            "swing_type": "HIGH",
            "bar_index": 1,
            "time_candle": times[1],
            "time_available": times[2],
            "price": 1520.0,
            "k": 1,
        }])

        struct_df = detect_structure(df, swings_df=swings_df)
        self.assertTrue(struct_df.empty, "Râu nến vượt đỉnh nhưng close nằm dưới không được sinh BOS/CHoCH")

    def test_09_swing_availability_timing_and_strict_boundary(self):
        """9. Tính khả dụng của Swing (Kiểm tra chặt chẽ ca biên time_available == open_time).

        - Nến t chỉ được dùng swing nếu swing.time_available <= open_time[t].
        - Bar 2 mở lúc 02:00, đóng 03:00 tại 1525.0 (> 1520.0). Nhưng time_available là 03:00 > 02:00 -> KHÔNG ĐƯỢC PHÁ.
        - Bar 3 mở lúc 03:00, đóng 04:00 tại 1522.0 (> 1520.0). Tại đây time_available (03:00) == open_time (03:00) -> ĐƯỢC PHÁ!
        - Khẳng định sự kiện xảy ra đúng tại Bar 3 (không phải bar 2, cũng không phải bar 4).
        """
        times = pd.date_range("2024-01-02 00:00", periods=6, freq="1h")
        # Swing High 1520.0 có time_available đúng lúc 03:00:00
        swings_df = pd.DataFrame([{
            "swing_id": 0,
            "swing_type": "HIGH",
            "bar_index": 1,
            "time_candle": times[1],
            "time_available": times[3],  # 03:00:00
            "price": 1520.0,
            "k": 1,
        }])

        # Bar 2 đóng 1525 (>1520) nhưng chưa đến giờ khả dụng.
        # Bar 3 đóng 1522 (>1520) đúng giờ khả dụng (03:00 == 03:00).
        # Bar 4 và 5 đóng 1510 (<= 1520).
        closes = [1500.0, 1510.0, 1525.0, 1522.0, 1510.0, 1515.0]
        df = pd.DataFrame({
            "time_utc": times,
            "open": closes,
            "high": [c + 2.0 for c in closes],
            "low": [c - 2.0 for c in closes],
            "close": closes,
        })

        struct_df = detect_structure(df, swings_df=swings_df)

        self.assertEqual(len(struct_df), 1, "Chỉ được sinh đúng 1 sự kiện")
        self.assertEqual(struct_df.iloc[0]["bar_index"], 3, "Sự kiện bắt buộc phải rơi vào đúng Bar 3 (ca biên time_available == open_time)")
        self.assertEqual(struct_df.iloc[0]["time_candle"], times[3])
        self.assertEqual(struct_df.iloc[0]["price_broken"], 1520.0)

    def test_10_independent_high_and_low_sides(self):
        """10. Hai phía độc lập: Phá Swing High không làm ảnh hưởng danh sách unbroken Swing Lows."""
        times = pd.date_range("2024-01-02 00:00", periods=7, freq="1h")
        closes = [1505.0, 1500.0, 1510.0, 1525.0, 1510.0, 1490.0, 1485.0]
        df = pd.DataFrame({
            "time_utc": times,
            "open": closes,
            "high": [c + 2.0 for c in closes],
            "low": [c - 2.0 for c in closes],
            "close": closes,
        })

        swings_df = pd.DataFrame([
            {
                "swing_id": 0,
                "swing_type": "HIGH",
                "bar_index": 1,
                "time_candle": times[1],
                "time_available": times[2],
                "price": 1520.0,
                "k": 1,
            },
            {
                "swing_id": 1,
                "swing_type": "LOW",
                "bar_index": 1,
                "time_candle": times[1],
                "time_available": times[2],
                "price": 1495.0,
                "k": 1,
            },
        ])

        struct_df = detect_structure(df, swings_df=swings_df)

        self.assertEqual(len(struct_df), 2)
        self.assertEqual(struct_df.iloc[0]["event_type"], "BULLISH_INIT_BREAK")
        self.assertEqual(struct_df.iloc[0]["price_broken"], 1520.0)
        self.assertEqual(struct_df.iloc[1]["event_type"], "BEARISH_CHOCH")
        self.assertEqual(struct_df.iloc[1]["price_broken"], 1495.0)

    def test_11_prefix_invariance_on_real_h1_event_and_confirmation_boundaries(self):
        """11. TEST BẤT BIẾN THEO TIỀN TỐ TRÊN DỮ LIỆU H1 THẬT (12 SỰ KIỆN: 3 MỖI LOẠI + BIÊN XÁC NHẬN SWING).

        Lấy mẫu 12 sự kiện rải đều qua đủ 4 loại sự kiện (BOS/CHoCH) trên H1 năm 2020:
        1. Cắt tại biên nến sự kiện: b (chưa đóng) và b + 1 (vừa đóng).
        2. Cắt tại biên xác nhận swing bị phá: c_conf (nến i+k chưa đóng) và c_conf + 1 (nến i+k vừa đóng).
        Đối chiếu 100% mọi thuộc tính bao gồm cả broken_swing_id và broken_swing_bar_index.
        """
        h1_df = load_h1("2020-01-01", "2020-12-31")
        swings_full = detect_swings(h1_df, k=3)
        struct_full = detect_structure(h1_df, swings_df=swings_full, k=3)
        self.assertGreater(len(struct_full), 100)

        # Lấy 12 sự kiện rải đều qua đủ 4 loại sự kiện (BOS/CHoCH) với seed cố định
        rng = np.random.default_rng(42)
        sample_indices_list = []
        for ev_type in ["BULLISH_BOS", "BEARISH_BOS", "BULLISH_CHOCH", "BEARISH_CHOCH"]:
            type_indices = struct_full.index[struct_full["event_type"] == ev_type].tolist()
            if type_indices:
                chosen = rng.choice(type_indices, size=min(3, len(type_indices)), replace=False)
                sample_indices_list.extend(chosen)

        sample_indices = np.sort(sample_indices_list)
        sample_events = struct_full.iloc[sample_indices]

        sw_dict = swings_full.set_index("swing_id").to_dict(orient="index")

        for _, ev in sample_events.iterrows():
            b = int(ev["bar_index"])
            broken_id = int(ev["broken_swing_id"])
            broken_sw = sw_dict[broken_id]
            c_conf = int(broken_sw["bar_index"]) + int(broken_sw["k"])

            # Tập các điểm cắt cần kiểm tra:
            # - c_conf: Nến xác nhận swing chưa đóng -> swing chưa khả dụng
            # - c_conf + 1: Nến xác nhận swing vừa đóng -> swing bắt đầu khả dụng
            # - b: Nến sự kiện chưa đóng -> sự kiện chưa sinh
            # - b + 1: Nến sự kiện vừa đóng -> sự kiện xuất hiện
            cut_points = {b, b + 1}
            if c_conf < len(h1_df):
                cut_points.add(c_conf)
                cut_points.add(c_conf + 1)

            for cut_idx in sorted(cut_points):
                if cut_idx <= 0 or cut_idx > len(h1_df):
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
                    f"Tại điểm cắt {cut_idx} (event tại {b}, swing tại {c_conf}): số lượng sự kiện phải bằng nhau",
                )

                for i in range(len(struct_prefix_reset)):
                    r_p = struct_prefix_reset.iloc[i]
                    r_f = struct_full_filtered.iloc[i]

                    self.assertEqual(r_p["event_type"], r_f["event_type"])
                    self.assertEqual(r_p["bar_index"], r_f["bar_index"])
                    self.assertEqual(r_p["time_candle"], r_f["time_candle"])
                    self.assertEqual(r_p["time_available"], r_f["time_available"])
                    self.assertAlmostEqual(r_p["price_broken"], r_f["price_broken"], places=6)
                    self.assertEqual(r_p["broken_swing_id"], r_f["broken_swing_id"])
                    self.assertEqual(r_p["broken_swing_bar_index"], r_f["broken_swing_bar_index"])
                    self.assertAlmostEqual(r_p["close_price"], r_f["close_price"], places=6)
                    self.assertEqual(r_p["prior_trend"], r_f["prior_trend"])
                    self.assertEqual(r_p["new_trend"], r_f["new_trend"])

    def test_12_input_validation_and_nan_protection(self):
        """12. Bảo vệ chống NaN/NaT, trùng thời gian và thiếu cột bắt buộc."""
        times = pd.date_range("2024-01-02 00:00", periods=5, freq="1h")

        # 1. NaN ở close
        df_nan_close = pd.DataFrame({
            "time_utc": times,
            "open": [1.0] * 5,
            "high": [2.0] * 5,
            "low": [0.5] * 5,
            "close": [1.0, np.nan, 1.0, 1.0, 1.0],
        })
        with self.assertRaises(ValueError):
            detect_structure(df_nan_close, k=1)

        # 2. NaN ở high
        df_nan_high = pd.DataFrame({
            "time_utc": times,
            "open": [1.0] * 5,
            "high": [2.0, np.nan, 2.0, 2.0, 2.0],
            "low": [0.5] * 5,
            "close": [1.0] * 5,
        })
        with self.assertRaises(ValueError):
            detect_structure(df_nan_high, k=1)

        # 3. NaN ở low
        df_nan_low = pd.DataFrame({
            "time_utc": times,
            "open": [1.0] * 5,
            "high": [2.0] * 5,
            "low": [0.5, 0.5, np.nan, 0.5, 0.5],
            "close": [1.0] * 5,
        })
        with self.assertRaises(ValueError):
            detect_structure(df_nan_low, k=1)

        # 4. NaT ở time_utc
        times_nat = times.copy()
        times_nat = pd.Series(times_nat)
        times_nat[1] = pd.NaT
        df_nat = pd.DataFrame({
            "time_utc": times_nat,
            "open": [1.0] * 5,
            "high": [2.0] * 5,
            "low": [0.5] * 5,
            "close": [1.0] * 5,
        })
        with self.assertRaises(ValueError):
            detect_structure(df_nat, k=1)

        # 5. Trùng lặp time_utc
        times_dup = [times[0], times[1], times[1], times[3], times[4]]
        df_dup = pd.DataFrame({
            "time_utc": times_dup,
            "open": [1.0] * 5,
            "high": [2.0] * 5,
            "low": [0.5] * 5,
            "close": [1.0] * 5,
        })
        with self.assertRaises(ValueError):
            detect_structure(df_dup, k=1)

        # 6. Thiếu cột bắt buộc khi DataFrame rỗng -> ném KeyError
        with self.assertRaises(KeyError):
            detect_structure(pd.DataFrame(columns=["a", "b"]))

        # 7. Thiếu cột bắt buộc khi DataFrame có dữ liệu -> ném KeyError
        with self.assertRaises(KeyError):
            detect_structure(pd.DataFrame({"time_utc": times, "close": [1.0] * 5}))

    def test_13_get_structure_available_at_and_tz_query(self):
        """13. get_structure_available_at: Lọc chính xác, không rỗng và hỗ trợ múi giờ."""
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
        self.assertFalse(struct_df.empty, "struct_df không được rỗng để kiểm thử get_structure_available_at")

        t_avail = struct_df.iloc[0]["time_available"]

        before = get_structure_available_at(struct_df, t_avail - pd.Timedelta(seconds=1))
        self.assertEqual(len(before), 0)

        exact = get_structure_available_at(struct_df, t_avail)
        self.assertGreaterEqual(len(exact), 1)

        tz_query = pd.Timestamp(t_avail).tz_localize("UTC").tz_convert("Asia/Ho_Chi_Minh")
        res_tz = get_structure_available_at(struct_df, tz_query)
        self.assertEqual(len(res_tz), len(exact))

    def test_14_warm_up_empirical_convergence(self):
        """14. KIỂM CHỨNG THỰC NGHIỆM HỘI TỤ WARM-UP TRÊN TOÀN BỘ 4.486 SỰ KIỆN DEVELOPMENT (2018–2025):

        Chứng minh thực nghiệm:
        - Mặc định warmup_months=None (nạp toàn bộ lịch sử từ 2014) và warmup_months=24 (từ 2016-01-01)
          cho kết quả trùng khớp hoàn hảo 100% trên toàn bộ 4.486 sự kiện cấu trúc giai đoạn 2018–2025
          về mọi thuộc tính thị trường (time_candle, time_available, event_type, price_broken, close_price, prior_trend, new_trend).
        - So sánh tỷ lệ khớp thực sự (ghép theo time_available và event_type):
          + 12 tháng warm-up khớp 99,978% (4.485/4.486 sự kiện).
          + 6 tháng warm-up khớp 99,889% (4.481/4.486 sự kiện).
        """
        # 1. Nạp với mặc định warmup_months=None (toàn bộ lịch sử từ đầu DB)
        s_default = load_structure("2018-01-01", "2025-06-30", warmup_months=None, k=3)
        self.assertEqual(len(s_default), 4486, "Tổng số sự kiện cấu trúc 2018-2025 phải là 4.486")

        # 2. Nạp qua load_structure với warmup_months=24 (từ 2016-01-01)
        s_24m = load_structure("2018-01-01", "2025-06-30", warmup_months=24, k=3)
        self.assertEqual(len(s_24m), 4486, "Số lượng sự kiện 24m phải bằng đúng 4.486")

        # Khẳng định hội tụ tuyệt đối 100% trên toàn bộ 4.486 sự kiện
        # (loại trừ bar_index và structure_id là chỉ số hàng cục bộ của lát cắt nạp)
        market_cols = ["event_type", "time_candle", "time_available", "price_broken", "close_price", "prior_trend", "new_trend"]
        for col in market_cols:
            self.assertTrue(
                (s_default[col].values == s_24m[col].values).all(),
                f"Cột '{col}' không khớp 100% giữa warmup_months=None và warmup_months=24 trên toàn bộ 2018–2025",
            )

        # 3. Kiểm chứng tỷ lệ ghép sự kiện thực sự với 12 tháng (từ 2017-01-01)
        s_12m = load_structure("2018-01-01", "2025-06-30", warmup_months=12, k=3)
        m_12m = pd.merge(s_default, s_12m, on=["time_available", "event_type"])
        self.assertEqual(len(m_12m), 4485, "Khớp thực sự 12m phải đạt 4.485 / 4.486 (99,978%)")

        # 4. Kiểm chứng tỷ lệ ghép sự kiện thực sự với 6 tháng (từ 2017-06-01)
        s_6m = load_structure("2018-01-01", "2025-06-30", warmup_months=6, k=3)
        m_6m = pd.merge(s_default, s_6m, on=["time_available", "event_type"])
        self.assertEqual(len(m_6m), 4481, "Khớp thực sự 6m phải đạt 4.481 / 4.486 (99,889%)")

    def test_15_invariants_and_annual_distribution_on_real_h1(self):
        """15. KIỂM TRA 5 BẤT BIẾN VẬT LÝ VÀ PHÂN BỐ SỰ KIỆN TRÊN H1 THẬT (2018–2025).

        5 Bất biến bắt buộc:
        1. Mỗi nến H1 sinh tối đa một sự kiện cấu trúc.
        2. Tính liên tục chuỗi Markov: prior_trend của sự kiện n+1 bằng new_trend của sự kiện n.
        3. Quy tắc First Break Only: Không broken_swing_id nào xuất hiện quá một lần.
        4. Zero Look-ahead: Mỗi swing bị phá có time_available <= time_candle của sự kiện.
        5. Tính đúng chiều của giá đóng cửa: Close > Price_broken (BULLISH) và Close < Price_broken (BEARISH).
        Khẳng định bổ sung:
        6. Không có sự kiện *_INIT_BREAK nào trong tập sau warm-up.
        7. prior_trend của sự kiện đầu tiên sau warm-up không phải là NEUTRAL.
        """
        dev_struct = load_structure("2018-01-01", "2025-06-30", warmup_months=None, k=3)
        self.assertEqual(len(dev_struct), 4486, "Tập Development 2018–2025 phải có đúng 4.486 sự kiện")

        # Khẳng định 6 & 7: Triệt tiêu hoàn toàn trạng thái NEUTRAL nhân tạo, xác minh xu hướng mở đầu là BULLISH
        self.assertFalse(dev_struct["event_type"].str.contains("INIT_BREAK").any(), "Không được có sự kiện INIT_BREAK nào sau khi lọc warm-up")
        self.assertNotEqual(dev_struct.iloc[0]["prior_trend"], "NEUTRAL", "prior_trend của sự kiện đầu tiên sau warm-up không được là NEUTRAL")
        self.assertEqual(dev_struct.iloc[0]["prior_trend"], "BULLISH", "Xu hướng mở đầu năm 2018 phải là BULLISH tự nhiên đã được kiểm chứng")
        self.assertEqual(dev_struct.iloc[0]["event_type"], "BULLISH_BOS", "Sự kiện đầu tiên năm 2018 phải là BULLISH_BOS")
        first_ev = dev_struct.iloc[0]
        print(f"\n[VERIFIED] First event 2018: bar {first_ev['time_candle']} | {first_ev['event_type']} | broken {first_ev['price_broken']} | close {first_ev['close_price']} | prior={first_ev['prior_trend']}")

        # 1. Mỗi nến có tối đa 1 sự kiện
        self.assertFalse(dev_struct["time_candle"].duplicated().any(), "Tồn tại nến có hơn 1 sự kiện cấu trúc")

        # 2. prior_trend[n+1] == new_trend[n]
        trend_match = (dev_struct["prior_trend"].iloc[1:].values == dev_struct["new_trend"].iloc[:-1].values)
        self.assertTrue(trend_match.all(), "Chuỗi chuyển trạng thái xu hướng bị đứt gãy")

        # 3. Không broken_swing_id nào bị phá 2 lần
        self.assertFalse(dev_struct["broken_swing_id"].duplicated().any(), "Tồn tại swing bị phá vỡ 2 lần")

        # 4. Zero Look-ahead: swing.time_available <= event.time_candle
        # Nạp lại toàn bộ swings từ đầu DB (khớp với base nạp của load_structure mặc định) để đối chiếu
        df_full = load_h1(None, "2025-06-30")
        swings = detect_swings(df_full, k=3)
        sw_map = swings.set_index("swing_id")["time_available"].to_dict()

        for _, row in dev_struct.iterrows():
            sw_avail = sw_map[row["broken_swing_id"]]
            self.assertLessEqual(sw_avail, row["time_candle"], "Swing bị phá vi phạm look-ahead")

        # 5. Giá đóng cửa phá đúng phía
        for _, row in dev_struct.iterrows():
            if "BULLISH" in row["event_type"]:
                self.assertGreater(row["close_price"], row["price_broken"])
            elif "BEARISH" in row["event_type"]:
                self.assertLess(row["close_price"], row["price_broken"])

        # In bảng phân bố sự kiện theo năm
        dev_struct["year"] = dev_struct["time_candle"].dt.year
        dist_table = dev_struct.groupby(["year", "event_type"]).size().unstack(fill_value=0)
        print("\n=== ANNUAL STRUCTURE EVENT DISTRIBUTION (2018-2025) ===")
        print(dist_table)

    def test_16_explicit_empty_swings_df_returns_empty_structure(self):
        """16. Truyền swings_df rỗng một cách tường minh phải trả về DataFrame rỗng (không tự tính lại)."""
        times = pd.date_range("2024-01-02 00:00", periods=10, freq="1h")
        df = pd.DataFrame({
            "time_utc": times,
            "open": [10.0] * 10,
            "high": [12.0] * 10,
            "low": [8.0] * 10,
            "close": [11.0] * 10,
        })
        empty_swings = pd.DataFrame(columns=["swing_id", "swing_type", "bar_index", "time_candle", "time_available", "price", "k"])
        res = detect_structure(df, swings_df=empty_swings)
        self.assertTrue(res.empty)
        self.assertIn("structure_id", res.columns)


if __name__ == "__main__":
    unittest.main()
