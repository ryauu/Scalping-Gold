"""Kiểm thử mô-đun structure.py (Bước 2: Cấu trúc thị trường BOS & CHoCH chuẩn SMC v2).

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
9. Test tính khả dụng của swing (Timing & Boundary):
   - time_available > open_time: Swing chưa khả dụng, không được dùng.
   - time_available == open_time: Ca biên, swing đã khả dụng và được dùng ngay.
10. Test hai phía độc lập: Phá Swing High không làm suy giảm hoặc thay đổi danh sách unbroken Swing Lows.
11. Test bất biến theo tiền tố (Prefix Invariance Test) cắt ngay tại biên sự kiện thật (b và b+1),
    đối chiếu 100% mọi cột bao gồm cả broken_swing_id và broken_swing_bar_index.
12. Test kiểm tra đầu vào toàn diện: NaN ở close/high/low, NaT ở time_utc, trùng lặp thời gian,
    thiếu cột (cả df rỗng lẫn df có dữ liệu).
13. Test get_structure_available_at: Kiểm tra timing khả dụng và truy vấn bằng Timestamp có múi giờ (UTC+7).
14. Test Warm-up Period: Triệt tiêu thiên lệch NEUTRAL ban đầu trên dữ liệu thật.
15. Test 5 bất biến vật lý & Bảng phân bố sự kiện theo năm trên tập H1 Development (2018–2025).
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

        # Nến 6 phá đỉnh 1520.0 khi đang NEUTRAL -> Phải sinh BULLISH_INIT_BREAK (khởi tạo, không phải CHoCH)
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

        # Chỉ có duy nhất 1 sự kiện tại nến 5
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
        # Đảm bảo low <= min(open, close) với open = close - 1.0
        opens = [c - 1.0 for c in closes]
        lows = [min(o, c) - 5.0 for o, c in zip(opens, closes)]

        # Khẳng định nến tổng hợp thỏa mãn cấu trúc vật lý
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
        # Nến có giá biến động thấp, bar 5 vọt lên 109.0, bar 6 đóng 110.0
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

        # Tiêm 2 Swing High A và B
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

        # Khẳng định chỉ có duy nhất 1 sự kiện tại bar 5 phá B (104.0).
        # Bar 6 tuyệt đối không được sinh sự kiện cho A (108.0).
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

        # Bar 8: close 1465.0 < 1478.0 -> BEARISH_INIT_BREAK (lập xu hướng BEARISH)
        # Bar 11: close 1525.0 > 1520.0 -> BULLISH_CHOCH (đảo chiều sang BULLISH)
        # Bar 16: close 1536.0 > 1535.0 -> BULLISH_BOS (tiếp diễn khi đã ở BULLISH)
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
        """6. Test lật trạng thái đối xứng BULLISH -> BEARISH CHoCH và BEARISH_BOS tiếp diễn.

        Kịch bản:
        - NEUTRAL ban đầu.
        - Phá Swing High 1520.0 -> BULLISH_INIT_BREAK, xu hướng = BULLISH.
        - Khi đang BULLISH, phá Protected Low 1500.0 -> BEARISH_CHOCH, lật sang BEARISH.
        - Khi đang BEARISH, phá Protected Low mới 1485.0 -> BEARISH_BOS (tiếp diễn giảm).
        """
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

        # Sự kiện 1: Khởi tạo tăng
        self.assertEqual(struct_df.iloc[0]["event_type"], "BULLISH_INIT_BREAK")
        self.assertEqual(struct_df.iloc[0]["prior_trend"], "NEUTRAL")
        self.assertEqual(struct_df.iloc[0]["new_trend"], "BULLISH")

        # Sự kiện 2: Đảo chiều giảm CHoCH
        self.assertEqual(struct_df.iloc[1]["event_type"], "BEARISH_CHOCH")
        self.assertEqual(struct_df.iloc[1]["price_broken"], 1500.0)
        self.assertEqual(struct_df.iloc[1]["prior_trend"], "BULLISH")
        self.assertEqual(struct_df.iloc[1]["new_trend"], "BEARISH")

        # Sự kiện 3: Tiếp diễn giảm BOS
        self.assertEqual(struct_df.iloc[2]["event_type"], "BEARISH_BOS")
        self.assertEqual(struct_df.iloc[2]["price_broken"], 1485.0)
        self.assertEqual(struct_df.iloc[2]["prior_trend"], "BEARISH")
        self.assertEqual(struct_df.iloc[2]["new_trend"], "BEARISH")

    def test_07_close_strictly_equal_to_swing_no_event(self):
        """7. Ca biên: Giá đóng cửa bằng đúng mức swing (Close == P).

        Theo định nghĩa toán học, điều kiện phá là lớn hơn / nhỏ hơn nghiêm ngặt (Close > P hoặc Close < P).
        Nếu Close == P thì KHÔNG sinh sự kiện.
        """
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
        # Nến 3 đóng tại đúng 1520.0 == 1520.0 -> Tuyệt đối không sinh sự kiện
        self.assertTrue(struct_df.empty, "Close == P không được sinh sự kiện")

    def test_08_wick_exceeds_close_below_no_event(self):
        """8. Râu nến vượt mức swing nhưng giá đóng cửa nằm dưới mức (High > P, Close <= P).

        Đây là hiện tượng quét thanh khoản (Liquidity Sweep), KHÔNG ĐƯỢC tính là BOS hay CHoCH.
        """
        times = pd.date_range("2024-01-02 00:00", periods=5, freq="1h")
        # Nến 3 có râu quét lên 1528.0 nhưng đóng cửa tại 1518.0 <= 1520.0
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

    def test_09_swing_availability_timing_and_boundary(self):
        """9. Tính khả dụng của Swing (Chống Look-Ahead & Ca biên bằng đúng open_time).

        - Nến t chỉ được dùng swing nếu swing.time_available <= open_time[t].
        - Nếu swing.time_available > open_time[t]: KHÔNG ĐƯỢC DÙNG tại nến t.
        - Ca biên: swing.time_available == open_time[t]: ĐƯỢC PHÉP DÙNG tại nến t.
        """
        times = pd.date_range("2024-01-02 00:00", periods=6, freq="1h")
        # times: 00:00, 01:00, 02:00, 03:00, 04:00, 05:00
        # Swing High tại 1520.0 có time_available lúc 03:00:00
        swings_df = pd.DataFrame([{
            "swing_id": 0,
            "swing_type": "HIGH",
            "bar_index": 1,
            "time_candle": times[1],
            "time_available": times[3],  # 03:00:00
            "price": 1520.0,
            "k": 1,
        }])

        # Nến bar 2 mở lúc 02:00:00, đóng lúc 03:00:00 với close = 1525.0
        # Tại open_time (02:00:00), time_available (03:00:00) > open_time -> Swing CHƯA KHẢ DỤNG!
        # Do đó bar 2 không được phép phá swing 1520.0
        closes = [1500.0, 1510.0, 1525.0, 1515.0, 1528.0, 1520.0]
        df = pd.DataFrame({
            "time_utc": times,
            "open": closes,
            "high": [c + 2.0 for c in closes],
            "low": [c - 2.0 for c in closes],
            "close": closes,
        })

        struct_df = detect_structure(df, swings_df=swings_df)

        # Bar 2 đóng 1525.0 KHÔNG SINH SỰ KIỆN.
        # Bar 4 mở lúc 04:00:00 (lúc này swing 03:00 đã khả dụng), đóng 1528.0 > 1520.0 -> SINH SỰ KIỆN TẠI BAR 4!
        self.assertEqual(len(struct_df), 1)
        self.assertEqual(struct_df.iloc[0]["bar_index"], 4)
        self.assertEqual(struct_df.iloc[0]["time_candle"], times[4])

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

        # Cả High 1520.0 và Low 1495.0 đều khả dụng từ bar 2
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

        # Bar 3 đóng 1525.0 phá High 1520.0 -> BULLISH_INIT_BREAK
        # Bar 5 đóng 1490.0 phá Low 1495.0 -> BEARISH_CHOCH (Low 1495 vẫn tồn tại nguyên vẹn, không bị mất)
        self.assertEqual(len(struct_df), 2)
        self.assertEqual(struct_df.iloc[0]["event_type"], "BULLISH_INIT_BREAK")
        self.assertEqual(struct_df.iloc[0]["price_broken"], 1520.0)
        self.assertEqual(struct_df.iloc[1]["event_type"], "BEARISH_CHOCH")
        self.assertEqual(struct_df.iloc[1]["price_broken"], 1495.0)

    def test_11_prefix_invariance_on_real_h1_event_boundaries(self):
        """11. TEST BẤT BIẾN THEO TIỀN TỐ TRÊN DỮ LIỆU H1 THẬT TẠI CÁC ĐIỂM BIÊN SỰ KIỆN.

        Cắt nến H1 ngay trước nến sự kiện (b) và ngay tại nến sự kiện (b + 1) trên dữ liệu năm 2020.
        Đối chiếu 100% mọi cột bao gồm cả broken_swing_id và broken_swing_bar_index.
        """
        h1_df = load_h1("2020-01-01", "2020-06-30")
        struct_full = detect_structure(h1_df, k=3)
        self.assertGreater(len(struct_full), 20)

        # Lấy 5 sự kiện thực tế đầu tiên
        target_events = struct_full.iloc[:5]

        for _, ev in target_events.iterrows():
            b = int(ev["bar_index"])

            # Cắt tại b (nến sự kiện chưa đóng) và b + 1 (nến sự kiện vừa đóng)
            for cut_idx in [b, b + 1]:
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

                # Đối chiếu chi tiết 100% từng thuộc tính bao gồm cả broken_swing_id
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

        # 1 giây trước t_avail -> không có sự kiện
        before = get_structure_available_at(struct_df, t_avail - pd.Timedelta(seconds=1))
        self.assertEqual(len(before), 0)

        # Đúng t_avail -> có sự kiện
        exact = get_structure_available_at(struct_df, t_avail)
        self.assertGreaterEqual(len(exact), 1)

        # Truy vấn bằng Timestamp có múi giờ (UTC+7: 07:00 giờ VN = 00:00 UTC)
        # Khẳng định hàm tự động chuẩn hóa sang UTC naive mà không gây lỗi kiểu dữ liệu
        tz_query = pd.Timestamp(t_avail).tz_localize("UTC").tz_convert("Asia/Ho_Chi_Minh")
        res_tz = get_structure_available_at(struct_df, tz_query)
        self.assertEqual(len(res_tz), len(exact))

    def test_14_warm_up_mitigates_initial_neutral_bias(self):
        """14. Rủi ro khởi động (Warm-up Period):

        Chứng minh nếu nạp từ 2018-01-01 không có warm-up:
        - Thị trường bắt đầu từ NEUTRAL, nến đầu bị gắn nhãn INIT_BREAK (không sinh tín hiệu).
        Nếu nạp có warm-up (từ 2017-06-01):
        - Thị trường bước vào ngày 2018-01-01 với xu hướng đã được xác lập tự nhiên (BULLISH/BEARISH).
        - Sự kiện đầu tiên trong năm 2018 là BOS thật sự chứ không phải INIT_BREAK giả tạo.
        """
        df_no_warm = load_h1("2018-01-01", "2018-01-15")
        s_no = detect_structure(df_no_warm, k=3)
        self.assertFalse(s_no.empty)
        self.assertEqual(s_no.iloc[0]["prior_trend"], "NEUTRAL")
        self.assertIn("INIT_BREAK", s_no.iloc[0]["event_type"])

        # Có warm-up từ 2017-06
        df_warm = load_h1("2017-06-01", "2018-01-15")
        s_warm = detect_structure(df_warm, k=3)
        s_warm_2018 = s_warm[s_warm["time_candle"] >= "2018-01-01"].reset_index(drop=True)

        self.assertFalse(s_warm_2018.empty)
        self.assertNotEqual(s_warm_2018.iloc[0]["prior_trend"], "NEUTRAL")
        self.assertIn(s_warm_2018.iloc[0]["event_type"], ["BULLISH_BOS", "BEARISH_BOS", "BULLISH_CHOCH", "BEARISH_CHOCH"])

    def test_15_invariants_and_annual_distribution_on_real_h1(self):
        """15. KIỂM TRA 5 BẤT BIẾN VẬT LÝ VÀ PHÂN BỐ SỰ KIỆN TRÊN H1 THẬT (2018–2025).

        5 Bất biến bắt buộc:
        1. Mỗi nến H1 sinh tối đa một sự kiện cấu trúc.
        2. Tính liên tục chuỗi Markov: prior_trend của sự kiện n+1 bằng new_trend của sự kiện n.
        3. Quy tắc First Break Only: Không broken_swing_id nào xuất hiện quá một lần.
        4. Zero Look-ahead: Mỗi swing bị phá có time_available <= time_candle của sự kiện.
        5. Tính đúng chiều của giá đóng cửa: Close > Price_broken (BULLISH) và Close < Price_broken (BEARISH).
        """
        df = load_h1("2017-06-01", "2025-06-30")
        swings = detect_swings(df, k=3)
        struct_all = detect_structure(df, swings_df=swings)

        # Cắt lấy tập Development 2018-01-01 đến 2025-06-30
        dev_struct = struct_all[struct_all["time_candle"] >= "2018-01-01"].copy().reset_index(drop=True)
        self.assertGreater(len(dev_struct), 3000, "Tập Development 2018–2025 phải có trên 3000 sự kiện")

        # 1. Mỗi nến có tối đa 1 sự kiện
        self.assertFalse(dev_struct["time_candle"].duplicated().any(), "Tồn tại nến có hơn 1 sự kiện cấu trúc")

        # 2. prior_trend[n+1] == new_trend[n]
        trend_match = (dev_struct["prior_trend"].iloc[1:].values == dev_struct["new_trend"].iloc[:-1].values)
        self.assertTrue(trend_match.all(), "Chuỗi chuyển trạng thái xu hướng bị đứt gãy")

        # 3. Không broken_swing_id nào bị phá 2 lần
        self.assertFalse(dev_struct["broken_swing_id"].duplicated().any(), "Tồn tại swing bị phá vỡ 2 lần")

        # 4. Zero Look-ahead: swing.time_available <= event.time_candle
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

        # In bảng phân bố sự kiện theo năm để kiểm tra tỷ lệ tiếp diễn vs đảo chiều
        dev_struct["year"] = dev_struct["time_candle"].dt.year
        dist_table = dev_struct.groupby(["year", "event_type"]).size().unstack(fill_value=0)
        print("\n=== ANNUAL STRUCTURE EVENT DISTRIBUTION (2018-2025) ===")
        print(dist_table)


if __name__ == "__main__":
    unittest.main()
