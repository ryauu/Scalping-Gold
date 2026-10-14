"""Kiểm thử toàn diện mô-đun fvg.py (Fair Value Gap & Nhật ký vòng đời chuẩn SMC v2.3).

Bao gồm:
1. Test ngưỡng lọc kích thước tối thiểu gap_size >= gamma * ATR[i-1] với số chẵn nhị phân chính xác (dấu >=).
2. Test nến i không tự test chính FVG nó tạo ra.
3. Test sự kiện TESTED chỉ ghi nhận 1 lần duy nhất dù giá chạm nhiều lần.
4. Test xung đột cùng nến: Vừa chạm mép gần vừa đóng xuyên mép xa chỉ ghi INVALIDATED.
5. Test nến thứ M chạm mép gần: TESTED ở M, EXPIRED ở M+1.
6. Test nến thứ M không chạm: EXPIRED ở M.
7. Test FVG đã TESTED trước đó thì EXPIRED ở nến thứ M.
8. Test bổ sung vòng đời:
   - TESTED rồi sau đó bị INVALIDATED ở nến tiếp theo.
   - Nến thứ M chạm mép gần (TESTED ở M), nến M+1 đóng xuyên mép xa (INVALIDATED ở M+1).
   - Tại chính nến thứ M vừa chạm mép gần vừa đóng xuyên mép xa -> chỉ INVALIDATED.
9. Test đối xứng gương (Mirror Symmetry Test):
   - Chạy trên chuỗi tổng hợp và H1 thật 2020: p' = K - p (đảo ngược chiều và các mép FVG 100%).
10. Test cờ spans_gap mở rộng đầy đủ các ca:
    - Chỉ bước nhảy i-2 -> i-1 (>1h).
    - Chỉ bước nhảy i-1 -> i (>1h).
    - Đúng 1 giờ liên tục (False).
    - 2 giờ kiểu giờ nghỉ bảo trì hàng ngày (True).
    - Cuối tuần 48h (True).
11. Test tính độc lập chống look-ahead của ATR[i-1].
12. Test ATR: một nguồn sự thật duy nhất (khớp 100% với compute_wilder_atr từ data.py).
13. Test 5 bất biến vật lý & bất biến mức FVG trên toàn bộ tập Development 2018–2025:
    - CREATED đầu tiên, TESTED <= 1 lần, kết thúc bằng INVALIDATED hoặc EXPIRED.
    - Bảng lifecycle_df được sắp xếp ổn định tăng dần theo time_available.
    - gap_size >= gamma * atr_ref; Bullish near_edge > far_edge; Bearish ngược lại.
14. Test bất biến theo tiền tố (Prefix Invariance Test) đối chiếu 100% mọi thuộc tính FVG và Lifecycle.
15. Test get_active_fvg_at toàn diện:
    - Hết active sau INVALIDATED / EXPIRED; cờ is_tested bật sau TESTED.
    - Bộ lọc valid_only: Loại bỏ FVG spans_gap=True khi valid_only=True, giữ lại khi valid_only=False.
    - Hỗ trợ Timestamp có múi giờ (UTC, Asia/Ho_Chi_Minh) không lỗi .dt.
16. Test load_fvg bảo toàn các FVG warm-up còn sống tại mốc start_date.
17. Test kiểm tra đầu vào, bảo vệ chống NaN/NaT/trùng lặp thời gian/thiếu cột, và hỗ trợ time_utc có múi giờ (UTC hoặc địa phương) khớp 100% với bản UTC naive.
18. Thống kê phân bố FVG theo năm và theo hướng trên tập Development 2018–2025 (khóa hồi quy).
19. Test ca biên nghiêm ngặt:
    - Ca bằng nhau: Low[i] == High[i-2] (và Bearish) với gamma=0.0 không được sinh FVG (kiểm tra dấu > nghiêm ngặt).
    - Biên 14 nến: Cùng một gap, đặt ở nến 13 (không có FVG do ATR[12]=NaN), đặt ở nến 14 (có FVG do ATR[13] hợp lệ).
    - Nến chưa đủ 14 nến khởi động ATR và atr_ref <= 0.
20. Test đối chiếu vét cạn (Brute-force Exhaustive Comparison):
    - Chạy qua nhiều seed (42, 2024, 7777), chèn khoảng trống thời gian (>1h và 48h cuối tuần), lặp qua các giá trị m (m=5 và m=24).
    - Khẳng định không qua trống rỗng: Có FVG, có cả spans_gap=True/False, đủ cả 4 loại sự kiện (CREATED, TESTED, INVALIDATED, EXPIRED), và kích hoạt nhánh biên TESTED đúng ở nến thứ M.
    - Đối chiếu 100% khớp cột phân loại/thời gian và khớp trong dung sai số thực (np.allclose) cho các cột giá/ATR với hàm ngây thơ độc lập.
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

from src.backtest.data import compute_wilder_atr, load_h1
from src.backtest.smc.fvg import (
    DEFAULT_ATR_PERIOD,
    DEFAULT_GAMMA,
    DEFAULT_M,
    detect_fvg,
    get_active_fvg_at,
    load_fvg,
)
from src.backtest.smc.swings import DEFAULT_H1_DURATION


def assert_candles_valid(tc: unittest.TestCase, df: pd.DataFrame) -> None:
    """Khẳng định mọi nến đều thỏa mãn bất biến vật lý low <= open, close <= high."""
    tc.assertTrue((df["low"] <= df["open"]).all(), "Tồn tại nến có open < low")
    tc.assertTrue((df["open"] <= df["high"]).all(), "Tồn tại nến có open > high")
    tc.assertTrue((df["low"] <= df["close"]).all(), "Tồn tại nến có close < low")
    tc.assertTrue((df["close"] <= df["high"]).all(), "Tồn tại nến có close > high")


def _make_steady_candles(n: int, high: float = 110.0, low: float = 100.0, close: float = 105.0) -> pd.DataFrame:
    """Tạo chuỗi n nến H1 hợp lệ với TR = high - low = 10.0, ATR = 10.0."""
    times = pd.date_range("2024-01-02 00:00", periods=n, freq="1h")
    return pd.DataFrame({
        "time_utc": times,
        "open": [close] * n,
        "high": [high] * n,
        "low": [low] * n,
        "close": [close] * n,
    })


class TestFVGModule(unittest.TestCase):
    def test_01_exact_gamma_atr_binary_float_threshold(self):
        """1. gap_size bằng đúng gamma * ATR[i-1] với số chẵn nhị phân chính xác (dấu >=)."""
        # Sử dụng lũy thừa của 2 để tránh làm tròn dấu phẩy động IEEE 754:
        # TR = 8.0 cho mọi nến 0..13 -> ATR[13] = 8.0 chính xác tuyệt đối.
        # Ngưỡng gap tối thiểu = 0.25 * 8.0 = 2.0.
        times = pd.date_range("2024-01-02 00:00", periods=14, freq="1h")
        base_df = pd.DataFrame({
            "time_utc": times,
            "open": [104.0] * 12 + [100.0, 104.0],
            "high": [108.0] * 12 + [104.0, 108.0],
            "low": [100.0] * 12 + [96.0, 100.0],
            "close": [104.0] * 12 + [100.0, 104.0],
        })
        assert_candles_valid(self, base_df)

        # High[12] = 104.0. ATR[13] = 8.0. gamma = 0.25 -> min_gap = 2.0.
        # 1. Ca bằng đúng 2.0: Low[14] = 106.0 -> gap = 106.0 - 104.0 = 2.0 == min_gap
        bar_exact = pd.DataFrame([{
            "time_utc": pd.Timestamp("2024-01-02 14:00"),
            "open": 108.0,
            "high": 115.0,
            "low": 106.0,
            "close": 112.0,
        }])
        df_exact = pd.concat([base_df, bar_exact], ignore_index=True)
        assert_candles_valid(self, df_exact)

        fvg_exact, _ = detect_fvg(df_exact, gamma=0.25, atr_period=14)
        self.assertEqual(len(fvg_exact), 1, "gap_size == 2.0 phải được giữ (dấu >=)")
        self.assertEqual(fvg_exact.iloc[0]["gap_size"], 2.0)
        self.assertEqual(fvg_exact.iloc[0]["atr_ref"], 8.0)

        # 2. Ca dưới ngưỡng: Low[14] = 105.99 -> gap = 1.99 < 2.0 -> Bị loại
        df_fail = df_exact.copy()
        df_fail.loc[14, "low"] = 105.99
        df_fail.loc[14, "open"] = 108.0
        fvg_fail, _ = detect_fvg(df_fail, gamma=0.25, atr_period=14)
        self.assertTrue(fvg_fail.empty, "gap_size < 2.0 phải bị loại bỏ")

        # 3. Ca trên ngưỡng: Low[14] = 106.01 -> gap = 2.01 > 2.0 -> Được giữ
        df_pass = df_exact.copy()
        df_pass.loc[14, "low"] = 106.01
        fvg_pass, _ = detect_fvg(df_pass, gamma=0.25, atr_period=14)
        self.assertEqual(len(fvg_pass), 1, "gap_size > 2.0 phải được giữ")

    def test_02_bar_i_does_not_self_test(self):
        """2. Nến i không tự test chính FVG nó tạo ra."""
        base_df = _make_steady_candles(14, high=110.0, low=100.0, close=105.0)
        base_df.loc[12, ["open", "high", "low", "close"]] = [96.0, 100.0, 95.0, 96.0]

        # Nến 14 tạo Bullish FVG: Low = 105.0, High = 120.0
        # Dù nến 14 có Low = 105.0 chạm vào vùng top = 105.0, nến 14 không tự test
        bar_14 = pd.DataFrame([{
            "time_utc": pd.Timestamp("2024-01-02 14:00"),
            "open": 106.0,
            "high": 120.0,
            "low": 105.0,
            "close": 118.0,
        }])
        df = pd.concat([base_df, bar_14], ignore_index=True)
        assert_candles_valid(self, df)

        fvg_df, lc_df = detect_fvg(df, gamma=0.15, atr_period=14)
        self.assertEqual(len(fvg_df), 1)
        # Chỉ có 1 sự kiện CREATED, TUYỆT ĐỐI KHÔNG có TESTED tại nến 14
        self.assertEqual(len(lc_df), 1)
        self.assertEqual(lc_df.iloc[0]["event_type"], "CREATED")
        self.assertEqual(lc_df.iloc[0]["bar_index"], 14)

    def test_03_tested_recorded_only_once_on_multiple_touches(self):
        """3. TESTED chỉ ghi nhận 1 lần duy nhất dù giá chạm nhiều lần."""
        base_df = _make_steady_candles(14, high=110.0, low=100.0, close=105.0)
        base_df.loc[12, ["open", "high", "low", "close"]] = [96.0, 100.0, 95.0, 96.0]

        # Nến 14: Bullish FVG (top=105.0, bottom=100.0)
        # Nến 15, 16, 17: Đều chạm top (Low <= 105.0, Close >= 100.0)
        extra_times = pd.date_range("2024-01-02 14:00", periods=4, freq="1h")
        extra_df = pd.DataFrame({
            "time_utc": extra_times,
            "open": [106.0, 107.0, 106.0, 107.0],
            "high": [120.0, 110.0, 110.0, 110.0],
            "low": [105.0, 103.0, 102.0, 104.0],  # 15, 16, 17 đều chạm vùng gap
            "close": [118.0, 106.0, 107.0, 108.0],
        })
        df = pd.concat([base_df, extra_df], ignore_index=True)
        assert_candles_valid(self, df)

        fvg_df, lc_df = detect_fvg(df, gamma=0.15, atr_period=14)
        self.assertEqual(len(fvg_df), 1)

        tested_events = lc_df[lc_df["event_type"] == "TESTED"]
        self.assertEqual(len(tested_events), 1, "TESTED chỉ được ghi nhận đúng 1 lần duy nhất")
        self.assertEqual(tested_events.iloc[0]["bar_index"], 15, "TESTED phải được ghi nhận tại nến 15 (chạm đầu tiên)")

    def test_04_same_bar_touch_and_invalidate_records_only_invalidated(self):
        """4. Cùng nến vừa chạm mép gần vừa đóng xuyên mép xa chỉ ghi INVALIDATED."""
        base_df = _make_steady_candles(14, high=110.0, low=100.0, close=105.0)
        base_df.loc[12, ["open", "high", "low", "close"]] = [96.0, 100.0, 95.0, 96.0]

        # Nến 14: Bullish FVG (top=105.0, bottom=100.0)
        # Nến 15: Low = 96.0 (chạm vào gap và xuyên thủng), Close = 97.0 (< bottom 100.0)
        extra_times = pd.date_range("2024-01-02 14:00", periods=2, freq="1h")
        extra_df = pd.DataFrame({
            "time_utc": extra_times,
            "open": [106.0, 102.0],
            "high": [120.0, 105.0],
            "low": [105.0, 96.0],
            "close": [118.0, 97.0],  # Đóng cửa xuyên mép xa
        })
        df = pd.concat([base_df, extra_df], ignore_index=True)
        assert_candles_valid(self, df)

        fvg_df, lc_df = detect_fvg(df, gamma=0.15, atr_period=14)
        self.assertEqual(len(fvg_df), 1)

        # Thứ tự ưu tiên INVALIDATED > TESTED: Chỉ có CREATED và INVALIDATED
        self.assertEqual(len(lc_df), 2)
        self.assertEqual(lc_df.iloc[0]["event_type"], "CREATED")
        self.assertEqual(lc_df.iloc[1]["event_type"], "INVALIDATED")
        self.assertNotIn("TESTED", lc_df["event_type"].values)

    def test_05_m_th_bar_touch_tested_at_m_expired_at_m_plus_1(self):
        """5. Nến thứ M chạm mép gần: TESTED ở M, EXPIRED ở M+1."""
        base_df = _make_steady_candles(14, high=110.0, low=100.0, close=105.0)
        base_df.loc[12, ["open", "high", "low", "close"]] = [96.0, 100.0, 95.0, 96.0]

        m = 5
        n_extra = m + 2
        extra_times = pd.date_range("2024-01-02 14:00", periods=n_extra, freq="1h")
        opens = [106.0] + [107.0] * (m + 1)
        highs = [120.0] + [110.0] * (m + 1)
        lows = [105.0] + [106.0] * (m - 1) + [104.0, 106.0]
        closes = [118.0] + [108.0] * (m + 1)

        extra_df = pd.DataFrame({
            "time_utc": extra_times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
        })
        df = pd.concat([base_df, extra_df], ignore_index=True)
        assert_candles_valid(self, df)

        fvg_df, lc_df = detect_fvg(df, gamma=0.15, m=m, atr_period=14)
        self.assertEqual(len(fvg_df), 1)

        # CREATED (14), TESTED (19 = 14 + 5), EXPIRED (20 = 14 + 6)
        self.assertEqual(len(lc_df), 3)
        self.assertEqual(lc_df.iloc[0]["event_type"], "CREATED")
        self.assertEqual(lc_df.iloc[1]["event_type"], "TESTED")
        self.assertEqual(lc_df.iloc[1]["bar_index"], 14 + m)
        self.assertEqual(lc_df.iloc[2]["event_type"], "EXPIRED")
        self.assertEqual(lc_df.iloc[2]["bar_index"], 14 + m + 1)

    def test_06_m_th_bar_no_touch_expired_at_m(self):
        """6. Nến thứ M không chạm: EXPIRED ở M."""
        base_df = _make_steady_candles(14, high=110.0, low=100.0, close=105.0)
        base_df.loc[12, ["open", "high", "low", "close"]] = [96.0, 100.0, 95.0, 96.0]

        m = 5
        n_extra = m + 1
        extra_times = pd.date_range("2024-01-02 14:00", periods=n_extra, freq="1h")
        extra_df = pd.DataFrame({
            "time_utc": extra_times,
            "open": [106.0] + [107.0] * m,
            "high": [120.0] + [110.0] * m,
            "low": [105.0] + [106.0] * m,
            "close": [118.0] + [108.0] * m,
        })
        df = pd.concat([base_df, extra_df], ignore_index=True)
        assert_candles_valid(self, df)

        fvg_df, lc_df = detect_fvg(df, gamma=0.15, m=m, atr_period=14)
        self.assertEqual(len(fvg_df), 1)

        self.assertEqual(len(lc_df), 2)
        self.assertEqual(lc_df.iloc[0]["event_type"], "CREATED")
        self.assertEqual(lc_df.iloc[1]["event_type"], "EXPIRED")
        self.assertEqual(lc_df.iloc[1]["bar_index"], 14 + m)

    def test_07_fvg_tested_earlier_expires_at_m(self):
        """7. FVG đã TESTED trước đó thì EXPIRED ở nến thứ M."""
        base_df = _make_steady_candles(14, high=110.0, low=100.0, close=105.0)
        base_df.loc[12, ["open", "high", "low", "close"]] = [96.0, 100.0, 95.0, 96.0]

        m = 5
        n_extra = m + 1
        extra_times = pd.date_range("2024-01-02 14:00", periods=n_extra, freq="1h")
        lows = [105.0, 104.0] + [106.0] * (m - 1)
        extra_df = pd.DataFrame({
            "time_utc": extra_times,
            "open": [106.0] + [107.0] * m,
            "high": [120.0] + [110.0] * m,
            "low": lows,
            "close": [118.0] + [108.0] * m,
        })
        df = pd.concat([base_df, extra_df], ignore_index=True)
        assert_candles_valid(self, df)

        fvg_df, lc_df = detect_fvg(df, gamma=0.15, m=m, atr_period=14)
        self.assertEqual(len(fvg_df), 1)

        self.assertEqual(len(lc_df), 3)
        self.assertEqual(lc_df.iloc[0]["event_type"], "CREATED")
        self.assertEqual(lc_df.iloc[1]["event_type"], "TESTED")
        self.assertEqual(lc_df.iloc[1]["bar_index"], 15)
        self.assertEqual(lc_df.iloc[2]["event_type"], "EXPIRED")
        self.assertEqual(lc_df.iloc[2]["bar_index"], 14 + m)

    def test_08_additional_lifecycle_branches(self):
        """8. Bổ sung các ca vòng đời còn thiếu:

        - TESTED rồi sau đó bị INVALIDATED.
        - Chạm ở nến M (TESTED ở M), nhưng nến M+1 bị INVALIDATED (INVALIDATED ở M+1 thay vì EXPIRED).
        - Tại nến thứ M vừa chạm mép gần vừa đóng xuyên mép xa -> chỉ ghi INVALIDATED.
        """
        # Ca 1: TESTED tại nến 15, sau đó nến 16 đóng cửa xuyên mép xa -> INVALIDATED
        base_df = _make_steady_candles(14, high=110.0, low=100.0, close=105.0)
        base_df.loc[12, ["open", "high", "low", "close"]] = [96.0, 100.0, 95.0, 96.0]

        extra_df_1 = pd.DataFrame({
            "time_utc": pd.date_range("2024-01-02 14:00", periods=3, freq="1h"),
            "open": [106.0, 106.0, 102.0],
            "high": [120.0, 110.0, 104.0],
            "low": [105.0, 103.0, 96.0],
            "close": [118.0, 106.0, 97.0],  # Nến 16 đóng thủng 100.0
        })
        df_1 = pd.concat([base_df, extra_df_1], ignore_index=True)
        assert_candles_valid(self, df_1)

        _, lc_1 = detect_fvg(df_1, gamma=0.15, atr_period=14)
        self.assertEqual(len(lc_1), 3)
        self.assertEqual(lc_1.iloc[0]["event_type"], "CREATED")
        self.assertEqual(lc_1.iloc[1]["event_type"], "TESTED")
        self.assertEqual(lc_1.iloc[1]["bar_index"], 15)
        self.assertEqual(lc_1.iloc[2]["event_type"], "INVALIDATED")
        self.assertEqual(lc_1.iloc[2]["bar_index"], 16)

        # Ca 2: Nến thứ M (j=i+5) TESTED, nến M+1 (j=i+6) INVALIDATED
        m = 5
        n_extra = m + 2
        extra_times = pd.date_range("2024-01-02 14:00", periods=n_extra, freq="1h")
        opens = [106.0] + [107.0] * (m - 1) + [106.0, 102.0]
        highs = [120.0] + [110.0] * (m - 1) + [108.0, 107.0]  # high[20]=107.0 > low[18]=106.0 tránh tạo Bearish FVG
        lows = [105.0] + [106.0] * (m - 1) + [104.0, 96.0]   # Nến M chạm (104.0), nến M+1 xuyên (96.0)
        closes = [118.0] + [108.0] * (m - 1) + [106.0, 97.0] # Nến M+1 đóng < 100.0

        extra_df_2 = pd.DataFrame({
            "time_utc": extra_times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
        })
        df_2 = pd.concat([base_df, extra_df_2], ignore_index=True)
        assert_candles_valid(self, df_2)

        _, lc_2 = detect_fvg(df_2, gamma=0.15, m=m, atr_period=14)
        self.assertEqual(len(lc_2), 3)
        self.assertEqual(lc_2.iloc[0]["event_type"], "CREATED")
        self.assertEqual(lc_2.iloc[1]["event_type"], "TESTED")
        self.assertEqual(lc_2.iloc[1]["bar_index"], 14 + m)
        self.assertEqual(lc_2.iloc[2]["event_type"], "INVALIDATED")
        self.assertEqual(lc_2.iloc[2]["bar_index"], 14 + m + 1)
        self.assertNotIn("EXPIRED", lc_2["event_type"].values)

        # Ca 3: Tại chính nến thứ M (j=i+m), vừa chạm mép gần vừa đóng xuyên mép xa -> Chỉ INVALIDATED
        opens_3 = [106.0] + [107.0] * (m - 1) + [102.0]
        highs_3 = [120.0] + [110.0] * (m - 1) + [107.0]  # high[19]=107.0 > low[17]=106.0 tránh tạo Bearish FVG
        lows_3 = [105.0] + [106.0] * (m - 1) + [96.0]
        closes_3 = [118.0] + [108.0] * (m - 1) + [97.0]

        extra_df_3 = pd.DataFrame({
            "time_utc": pd.date_range("2024-01-02 14:00", periods=m + 1, freq="1h"),
            "open": opens_3,
            "high": highs_3,
            "low": lows_3,
            "close": closes_3,
        })
        df_3 = pd.concat([base_df, extra_df_3], ignore_index=True)
        assert_candles_valid(self, df_3)

        _, lc_3 = detect_fvg(df_3, gamma=0.15, m=m, atr_period=14)
        self.assertEqual(len(lc_3), 2)
        self.assertEqual(lc_3.iloc[0]["event_type"], "CREATED")
        self.assertEqual(lc_3.iloc[1]["event_type"], "INVALIDATED")
        self.assertEqual(lc_3.iloc[1]["bar_index"], 14 + m)
        self.assertNotIn("TESTED", lc_3["event_type"].values)
        self.assertNotIn("EXPIRED", lc_3["event_type"].values)

    def test_09_mirror_symmetry_synthetic_and_real(self):
        """9. TEST ĐỐI XỨNG GƯƠNG (MIRROR SYMMETRY TEST):

        Lấy p' = K - p (K = 10.000). Đảo ngược toàn bộ giá.
        Kỳ vọng trên cả Synthetic và Real H1:
        - Số lượng FVG bằng nhau 100%.
        - time_candle, time_available, gap_size, atr_ref bằng nhau 100%.
        - direction: BULLISH đảo thành BEARISH và ngược lại.
        - lifecycle: Cùng bar_index, cùng time_available, cùng event_type 100%.
        """
        K = 10000.0

        # 1. Chạy trên synthetic data
        base_df = _make_steady_candles(14, high=108.0, low=100.0, close=104.0)
        base_df.loc[12, ["open", "high", "low", "close"]] = [96.0, 100.0, 95.0, 96.0]
        bar_14 = pd.DataFrame([{
            "time_utc": pd.Timestamp("2024-01-02 14:00"),
            "open": 106.0,
            "high": 115.0,
            "low": 105.0,
            "close": 112.0,
        }])
        syn_bull = pd.concat([base_df, bar_14], ignore_index=True)

        syn_bear = syn_bull.copy()
        syn_bear["open"] = K - syn_bull["open"]
        syn_bear["high"] = K - syn_bull["low"]
        syn_bear["low"] = K - syn_bull["high"]
        syn_bear["close"] = K - syn_bull["close"]
        assert_candles_valid(self, syn_bull)
        assert_candles_valid(self, syn_bear)

        fvg_bull, lc_bull = detect_fvg(syn_bull, gamma=0.15, atr_period=14)
        fvg_bear, lc_bear = detect_fvg(syn_bear, gamma=0.15, atr_period=14)

        self.assertEqual(len(fvg_bull), 1)
        self.assertEqual(len(fvg_bear), 1)
        self.assertEqual(fvg_bull.iloc[0]["direction"], "BULLISH")
        self.assertEqual(fvg_bear.iloc[0]["direction"], "BEARISH")
        self.assertAlmostEqual(fvg_bull.iloc[0]["gap_size"], fvg_bear.iloc[0]["gap_size"], places=6)
        self.assertAlmostEqual(fvg_bull.iloc[0]["atr_ref"], fvg_bear.iloc[0]["atr_ref"], places=6)

        # 2. Chạy trên H1 thật năm 2020 (6 tháng)
        real_df = load_h1("2020-01-01", "2020-06-30")
        real_mirror = real_df.copy()
        real_mirror["open"] = K - real_df["open"]
        real_mirror["high"] = K - real_df["low"]
        real_mirror["low"] = K - real_df["high"]
        real_mirror["close"] = K - real_df["close"]
        assert_candles_valid(self, real_df)
        assert_candles_valid(self, real_mirror)

        fvg_r, lc_r = detect_fvg(real_df, gamma=DEFAULT_GAMMA, m=DEFAULT_M)
        fvg_m, lc_m = detect_fvg(real_mirror, gamma=DEFAULT_GAMMA, m=DEFAULT_M)

        self.assertEqual(len(fvg_r), len(fvg_m), "Số lượng FVG đối xứng gương phải bằng nhau 100%")
        self.assertEqual(len(lc_r), len(lc_m), "Số lượng sự kiện vòng đời đối xứng gương phải bằng nhau 100%")

        # Đối chiếu 100% từng dòng FVG
        for i in range(len(fvg_r)):
            r1 = fvg_r.iloc[i]
            r2 = fvg_m.iloc[i]
            self.assertEqual(r1["time_candle"], r2["time_candle"])
            self.assertEqual(r1["time_available"], r2["time_available"])
            self.assertAlmostEqual(r1["gap_size"], r2["gap_size"], places=5)
            self.assertAlmostEqual(r1["atr_ref"], r2["atr_ref"], places=5)
            self.assertEqual(r1["spans_gap"], r2["spans_gap"])
            expected_dir = "BEARISH" if r1["direction"] == "BULLISH" else "BULLISH"
            self.assertEqual(r2["direction"], expected_dir)

        # Đối chiếu 100% sự kiện vòng đời
        for j in range(len(lc_r)):
            e1 = lc_r.iloc[j]
            e2 = lc_m.iloc[j]
            self.assertEqual(e1["time_available"], e2["time_available"])
            self.assertEqual(e1["event_type"], e2["event_type"])
            self.assertEqual(e1["bar_index"], e2["bar_index"])

    def test_10_spans_gap_comprehensive_cases(self):
        """10. Test cờ spans_gap mở rộng đầy đủ 5 trường hợp thời gian:

        - Ca 1: dt1 > 1h, dt2 = 1h -> spans_gap = True.
        - Ca 2: dt1 = 1h, dt2 > 1h -> spans_gap = True.
        - Ca 3: dt1 = 1h, dt2 = 1h -> spans_gap = False.
        - Ca 4: Giờ nghỉ bảo trì hàng ngày 2h -> spans_gap = True.
        - Ca 5: Cuối tuần 48h -> spans_gap = True.
        """
        def make_test_trio(times: list[pd.Timestamp]) -> pd.DataFrame:
            base = _make_steady_candles(12, high=108.0, low=100.0, close=104.0)
            trio = pd.DataFrame({
                "time_utc": times,
                "open": [96.0, 102.0, 106.0],
                "high": [100.0, 110.0, 115.0],
                "low": [95.0, 101.0, 105.0],  # Low[i] = 105 > High[i-2] = 100
                "close": [96.0, 108.0, 112.0],
            })
            df = pd.concat([base, trio], ignore_index=True)
            assert_candles_valid(self, df)
            return df

        # Ca 1: dt1 = 2h (>1h), dt2 = 1h -> spans_gap = True
        t_ca1 = [pd.Timestamp("2024-01-02 12:00"), pd.Timestamp("2024-01-02 14:00"), pd.Timestamp("2024-01-02 15:00")]
        fvg_1, _ = detect_fvg(make_test_trio(t_ca1), gamma=0.15)
        self.assertTrue(fvg_1.iloc[0]["spans_gap"], "dt1 > 1h phải có spans_gap = True")

        # Ca 2: dt1 = 1h, dt2 = 2h (>1h) -> spans_gap = True
        t_ca2 = [pd.Timestamp("2024-01-02 12:00"), pd.Timestamp("2024-01-02 13:00"), pd.Timestamp("2024-01-02 15:00")]
        fvg_2, _ = detect_fvg(make_test_trio(t_ca2), gamma=0.15)
        self.assertTrue(fvg_2.iloc[0]["spans_gap"], "dt2 > 1h phải có spans_gap = True")

        # Ca 3: dt1 = 1h, dt2 = 1h (liên tục) -> spans_gap = False
        t_ca3 = [pd.Timestamp("2024-01-02 12:00"), pd.Timestamp("2024-01-02 13:00"), pd.Timestamp("2024-01-02 14:00")]
        fvg_3, _ = detect_fvg(make_test_trio(t_ca3), gamma=0.15)
        self.assertFalse(fvg_3.iloc[0]["spans_gap"], "1h liên tục phải có spans_gap = False")

        # Ca 4: Giờ nghỉ bảo trì hàng ngày 2h -> spans_gap = True
        t_ca4 = [pd.Timestamp("2024-01-02 20:00"), pd.Timestamp("2024-01-02 21:00"), pd.Timestamp("2024-01-02 23:00")]
        fvg_4, _ = detect_fvg(make_test_trio(t_ca4), gamma=0.15)
        self.assertTrue(fvg_4.iloc[0]["spans_gap"], "Giờ nghỉ hàng ngày phải có spans_gap = True")

        # Ca 5: Cuối tuần 48h -> spans_gap = True
        t_ca5 = [pd.Timestamp("2024-01-05 20:00"), pd.Timestamp("2024-01-05 21:00"), pd.Timestamp("2024-01-07 22:00")]
        fvg_5, _ = detect_fvg(make_test_trio(t_ca5), gamma=0.15)
        self.assertTrue(fvg_5.iloc[0]["spans_gap"], "Cuối tuần phải có spans_gap = True")

    def test_11_zero_lookahead_atr_independence(self):
        """11. Thay High/Low của nến i thì ATR[i-1] không đổi (kiểm tra không look-ahead)."""
        base_df = _make_steady_candles(14, high=110.0, low=100.0, close=105.0)
        bar_14_orig = pd.DataFrame([{
            "time_utc": pd.Timestamp("2024-01-02 14:00"),
            "open": 112.0,
            "high": 120.0,
            "low": 112.0,
            "close": 118.0,
        }])
        df_orig = pd.concat([base_df, bar_14_orig], ignore_index=True)
        assert_candles_valid(self, df_orig)
        fvg_orig, _ = detect_fvg(df_orig, gamma=0.15, atr_period=14)

        df_mod = df_orig.copy()
        df_mod.loc[14, "high"] = 250.0
        df_mod.loc[14, "low"] = 115.0
        df_mod.loc[14, "open"] = 116.0
        df_mod.loc[14, "close"] = 240.0
        assert_candles_valid(self, df_mod)
        fvg_mod, _ = detect_fvg(df_mod, gamma=0.15, atr_period=14)

        self.assertFalse(fvg_orig.empty)
        self.assertFalse(fvg_mod.empty)
        self.assertEqual(fvg_orig.iloc[0]["atr_ref"], fvg_mod.iloc[0]["atr_ref"])

    def test_12_atr_single_source_of_truth_matches_data_module(self):
        """12. ATR: một nguồn sự thật duy nhất (khớp 100% với compute_wilder_atr từ data.py)."""
        df_h1 = load_h1("2020-01-01", "2020-03-31")
        fvg_df, _ = detect_fvg(df_h1, gamma=DEFAULT_GAMMA)

        # Tính trực tiếp theo chuẩn data.py
        highs = df_h1["high"].to_numpy(dtype=np.float64)
        lows = df_h1["low"].to_numpy(dtype=np.float64)
        closes = df_h1["close"].to_numpy(dtype=np.float64)
        atr_raw = compute_wilder_atr(highs, lows, closes, period=14)

        # Với mọi FVG tạo tại nến i, atr_ref bắt buộc bằng atr_raw[i-1]
        for _, r in fvg_df.iterrows():
            i = int(r["bar_index"])
            expected_atr = atr_raw[i - 1]
            self.assertEqual(r["atr_ref"], expected_atr, f"atr_ref tại nến {i} không khớp với compute_wilder_atr")

    def test_13_physical_invariants_and_fvg_levels_across_development(self):
        """13. KIỂM TRA 5 BẤT BIẾN VẬT LÝ & BẤT BIẾN MỨC FVG TRÊN TOÀN BỘ TẬP DEVELOPMENT (2018–2025):

        - Mỗi FVG đúng 1 sự kiện CREATED.
        - Mỗi FVG có tối đa 1 sự kiện TESTED.
        - Mỗi FVG có tối đa 1 sự kiện kết thúc (INVALIDATED hoặc EXPIRED).
        - Tuyệt đối không có bất kỳ sự kiện nào sau sự kiện kết thúc.
        - Bảng lifecycle_df được sắp xếp ổn định tăng dần theo time_available.
        - Bất biến mức FVG: gap_size >= gamma * atr_ref;
          Bullish near_edge > far_edge (top > bottom);
          Bearish near_edge < far_edge (bottom < top).
        """
        fvg_df, lc_df = load_fvg("2018-01-01", "2025-06-30", warmup_months=None, gamma=DEFAULT_GAMMA, m=DEFAULT_M)
        self.assertGreater(len(fvg_df), 5000, "Tập Development 2018-2025 phải có trên 5.000 FVG (khóa hồi quy)")

        # 1. Bất biến mức FVG
        for _, r in fvg_df.iterrows():
            self.assertGreaterEqual(r["gap_size"], DEFAULT_GAMMA * r["atr_ref"] - 1e-9)
            if r["direction"] == "BULLISH":
                self.assertGreater(r["near_edge"], r["far_edge"])
                self.assertEqual(r["top"], r["near_edge"])
                self.assertEqual(r["bottom"], r["far_edge"])
            else:
                self.assertLess(r["near_edge"], r["far_edge"])
                self.assertEqual(r["bottom"], r["near_edge"])
                self.assertEqual(r["top"], r["far_edge"])

        # 2. Mỗi FVG đúng 1 CREATED
        created_events = lc_df[lc_df["event_type"] == "CREATED"]
        self.assertEqual(len(created_events), len(fvg_df))
        self.assertFalse(created_events["fvg_id"].duplicated().any())

        # 3. Mỗi FVG tối đa 1 TESTED
        tested_events = lc_df[lc_df["event_type"] == "TESTED"]
        self.assertFalse(tested_events["fvg_id"].duplicated().any())

        # 4. Mỗi FVG tối đa 1 sự kiện kết thúc
        term_events = lc_df[lc_df["event_type"].isin(["INVALIDATED", "EXPIRED"])]
        self.assertFalse(term_events["fvg_id"].duplicated().any())

        # 5. Bảng lifecycle_df sắp xếp tăng dần theo time_available
        lc_avails = pd.to_datetime(lc_df["time_available"])
        self.assertTrue((lc_avails.iloc[1:].values >= lc_avails.iloc[:-1].values).all(), "lifecycle_df chưa được sắp xếp tăng dần theo time_available")

        # 6. Tuyệt đối không có sự kiện nào sau kết thúc
        grouped = lc_df.groupby("fvg_id")
        for fid, group in grouped:
            types = group["event_type"].tolist()
            for idx, ev_type in enumerate(types):
                if ev_type in ["INVALIDATED", "EXPIRED"]:
                    self.assertEqual(idx, len(types) - 1, f"FVG {fid} có sự kiện sau khi đã kết thúc ({types})")

    def test_14_prefix_invariance_full_attribute_comparison(self):
        """14. TEST BẤT BIẾN THEO TIỀN TỐ (PREFIX INVARIANCE) NÂNG CẤP ĐỐI CHIẾU 100% NỘI DUNG:

        Lấy mẫu 8 FVG rải đều trên H1 năm 2020:
        Cắt tại biên nến tạo FVG (i, i+1) và nến sự kiện vòng đời (j, j+1).
        Đối chiếu 100% từng thuộc tính FVG và Lifecycle giữa chạy tiền tố và chạy toàn bộ.
        """
        h1_df = load_h1("2020-01-01", "2020-06-30")
        fvg_full, lc_full = detect_fvg(h1_df, gamma=0.15, m=24)
        self.assertGreater(len(fvg_full), 200)

        rng = np.random.default_rng(42)
        sample_fvg_ids = rng.choice(fvg_full["fvg_id"].values, size=8, replace=False)

        for fid in sample_fvg_ids:
            fvg_row = fvg_full[fvg_full["fvg_id"] == fid].iloc[0]
            i_bar = int(fvg_row["bar_index"])

            events = lc_full[lc_full["fvg_id"] == fid]
            cut_bars = {i_bar, i_bar + 1}
            for _, ev in events.iterrows():
                j_bar = int(ev["bar_index"])
                cut_bars.add(j_bar)
                cut_bars.add(j_bar + 1)

            for cut_point in sorted(cut_bars):
                if cut_point <= 14 or cut_point > len(h1_df):
                    continue

                prefix_df = h1_df.iloc[:cut_point].copy()
                cutoff_time = prefix_df.iloc[-1]["time_utc"] + DEFAULT_H1_DURATION

                fvg_pre, lc_pre = detect_fvg(prefix_df, gamma=0.15, m=24)

                fvg_full_filt = fvg_full[fvg_full["time_available"] <= cutoff_time].reset_index(drop=True)
                lc_full_filt = lc_full[lc_full["time_available"] <= cutoff_time].reset_index(drop=True)

                self.assertEqual(len(fvg_pre), len(fvg_full_filt))
                self.assertEqual(len(lc_pre), len(lc_full_filt))

                # Đối chiếu nội dung bảng FVG
                fvg_cols = ["direction", "time_candle", "time_available", "top", "bottom", "near_edge", "far_edge", "gap_size", "atr_ref", "spans_gap"]
                for col in fvg_cols:
                    self.assertTrue((fvg_pre[col].values == fvg_full_filt[col].values).all())

                # Đối chiếu nội dung bảng Lifecycle
                lc_cols = ["event_type", "time_candle", "time_available", "price_trigger"]
                for col in lc_cols:
                    v1 = lc_pre[col].fillna(-999).to_numpy()
                    v2 = lc_full_filt[col].fillna(-999).to_numpy()
                    self.assertTrue((v1 == v2).all(), f"Sai khác cột lifecycle '{col}' tại điểm cắt {cut_point}")

    def test_15_get_active_fvg_at_comprehensive_and_timezone(self):
        """15. get_active_fvg_at: Kiểm tra đầy đủ active, is_tested, valid_only, và Timestamp có múi giờ."""
        times = pd.date_range("2024-01-02 00:00", periods=20, freq="1h")
        base_df = pd.DataFrame({
            "time_utc": times,
            "open": [105.0] * 20,
            "high": [110.0] * 20,
            "low": [100.0] * 20,
            "close": [105.0] * 20,
        })
        base_df.loc[12, ["open", "high", "low", "close"]] = [96.0, 100.0, 95.0, 96.0]
        # Nến 14: Bullish FVG (top=105.0, bottom=100.0)
        base_df.loc[14, ["open", "high", "low", "close"]] = [106.0, 120.0, 105.0, 118.0]
        # Nến 15: Chạm mép gần (Low = 104.0) -> TESTED
        base_df.loc[15, ["open", "high", "low", "close"]] = [106.0, 110.0, 104.0, 108.0]
        # Nến 16: Đóng xuyên mép xa (Close = 97.0 < 100.0) -> INVALIDATED
        base_df.loc[16, ["open", "high", "low", "close"]] = [102.0, 104.0, 96.0, 97.0]
        assert_candles_valid(self, base_df)

        fvg_df, lc_df = detect_fvg(base_df, gamma=0.15, m=24, atr_period=14)
        self.assertEqual(len(fvg_df), 1)

        t_avail = fvg_df.iloc[0]["time_available"]
        t_tested = lc_df[lc_df["event_type"] == "TESTED"].iloc[0]["time_available"]
        t_inv = lc_df[lc_df["event_type"] == "INVALIDATED"].iloc[0]["time_available"]

        # 1. Trước khi tạo: không active
        self.assertTrue(get_active_fvg_at(fvg_df, lc_df, t_avail - pd.Timedelta(seconds=1)).empty)

        # 2. Lúc tạo (t_avail): active và is_tested = False
        res_created = get_active_fvg_at(fvg_df, lc_df, t_avail)
        self.assertEqual(len(res_created), 1)
        self.assertFalse(res_created.iloc[0]["is_tested"])

        # 3. Sau khi TESTED (t_tested): active và is_tested = True
        res_tested = get_active_fvg_at(fvg_df, lc_df, t_tested)
        self.assertEqual(len(res_tested), 1)
        self.assertTrue(res_tested.iloc[0]["is_tested"], "Cờ is_tested phải bật thành True sau khi TESTED")

        # 4. Sau khi INVALIDATED (t_inv): KHÔNG còn active
        res_inv = get_active_fvg_at(fvg_df, lc_df, t_inv)
        self.assertTrue(res_inv.empty, "FVG phải chuyển sang không active sau khi bị INVALIDATED")

        # 5. Kiểm tra truy vấn bằng Timestamp có múi giờ (UTC và Asia/Ho_Chi_Minh) không lỗi .dt
        tz_utc = pd.Timestamp(t_avail).tz_localize("UTC")
        res_tz_utc = get_active_fvg_at(fvg_df, lc_df, tz_utc)
        self.assertEqual(len(res_tz_utc), 1)

        tz_vn = pd.Timestamp(t_avail).tz_localize("UTC").tz_convert("Asia/Ho_Chi_Minh")
        res_tz_vn = get_active_fvg_at(fvg_df, lc_df, tz_vn)
        self.assertEqual(len(res_tz_vn), 1)

        # 6. Kiểm tra bộ lọc valid_only với FVG spans_gap=True:
        # Tạo chuỗi nến có 1 khoảng trống thời gian > 1h (nhảy 2h từ 14:00 đến 16:00) nằm giữa bộ ba nến [12, 13, 14]
        times_gap = list(pd.date_range("2024-01-02 00:00", periods=14, freq="1h"))
        times_gap.append(pd.Timestamp("2024-01-02 16:00"))  # Nến 14 nhảy qua khoảng trống > 1h
        times_gap.append(pd.Timestamp("2024-01-02 17:00"))
        gap_df = pd.DataFrame({
            "time_utc": times_gap,
            "open": [105.0] * 16,
            "high": [110.0] * 16,
            "low": [100.0] * 16,
            "close": [105.0] * 16,
        })
        gap_df.loc[12, ["open", "high", "low", "close"]] = [96.0, 100.0, 95.0, 96.0]
        gap_df.loc[14, ["open", "high", "low", "close"]] = [106.0, 120.0, 105.0, 118.0]
        assert_candles_valid(self, gap_df)

        fvg_gap, lc_gap = detect_fvg(gap_df, gamma=0.15, m=24, atr_period=14)
        self.assertEqual(len(fvg_gap), 1)
        self.assertTrue(fvg_gap.iloc[0]["spans_gap"], "FVG qua khoảng trống thời gian > 1h phải có spans_gap=True")
        t_gap_avail = fvg_gap.iloc[0]["time_available"]

        # Khẳng định valid_only=True LOẠI BỎ FVG spans_gap=True (kết quả rỗng)
        act_valid = get_active_fvg_at(fvg_gap, lc_gap, t_gap_avail, valid_only=True)
        self.assertTrue(act_valid.empty, "valid_only=True bắt buộc phải loại bỏ FVG có spans_gap=True")

        # Khẳng định valid_only=False GIỮ LẠI FVG spans_gap=True
        act_all = get_active_fvg_at(fvg_gap, lc_gap, t_gap_avail, valid_only=False)
        self.assertEqual(len(act_all), 1, "valid_only=False bắt buộc phải giữ lại FVG có spans_gap=True")
        self.assertTrue(act_all.iloc[0]["spans_gap"])

    def test_16_load_fvg_preserves_active_warmup_fvgs(self):
        """16. load_fvg: Bảo toàn các FVG tạo trước start_date nhưng còn sống tại start_date."""
        fvg_df, lc_df = load_fvg("2018-01-01", "2025-06-30", warmup_months=None)
        start_ts = pd.Timestamp("2018-01-01")

        # Phải có ít nhất 4 FVG tạo trước 2018-01-01 (từ 29/12/2017) được bảo toàn
        warmup_fvgs = fvg_df[fvg_df["time_available"] < start_ts]
        self.assertEqual(len(warmup_fvgs), 4, "Phải bảo toàn đúng 4 FVG còn sống từ cuối năm 2017")

        # Kiểm tra sự kiện CREATED của các FVG này có mặt trong lc_df
        for fid in warmup_fvgs["fvg_id"]:
            fvg_events = lc_df[lc_df["fvg_id"] == fid]
            self.assertIn("CREATED", fvg_events["event_type"].values)
            # Khẳng định FVG này chưa kết thúc trước start_ts
            term_events = fvg_events[fvg_events["event_type"].isin(["INVALIDATED", "EXPIRED"])]
            if not term_events.empty:
                self.assertGreaterEqual(pd.Timestamp(term_events.iloc[0]["time_available"]), start_ts)

    def test_17_input_validation(self):
        """17. Kiểm tra đầu vào, bảo vệ chống NaN/NaT/trùng thời gian/thiếu cột."""
        times = pd.date_range("2024-01-02 00:00", periods=5, freq="1h")

        # 1. NaN ở giá
        df_nan = pd.DataFrame({
            "time_utc": times,
            "open": [1.0, np.nan, 1.0, 1.0, 1.0],
            "high": [2.0] * 5,
            "low": [0.5] * 5,
            "close": [1.0] * 5,
        })
        with self.assertRaises(ValueError):
            detect_fvg(df_nan)

        # 2. NaT ở time_utc
        times_nat = pd.Series(times).copy()
        times_nat[2] = pd.NaT
        df_nat = pd.DataFrame({
            "time_utc": times_nat,
            "open": [1.0] * 5,
            "high": [2.0] * 5,
            "low": [0.5] * 5,
            "close": [1.0] * 5,
        })
        with self.assertRaises(ValueError):
            detect_fvg(df_nat)

        # 3. Trùng thời gian
        df_dup = pd.DataFrame({
            "time_utc": [times[0], times[1], times[1], times[3], times[4]],
            "open": [1.0] * 5,
            "high": [2.0] * 5,
            "low": [0.5] * 5,
            "close": [1.0] * 5,
        })
        with self.assertRaises(ValueError):
            detect_fvg(df_dup)

        # 4. Thiếu cột khi rỗng
        with self.assertRaises(KeyError):
            detect_fvg(pd.DataFrame(columns=["a", "b"]))

        # 5. time_utc có múi giờ: detect_fvg trên chuỗi có tz (UTC hoặc UTC+7) phải khớp 100% với bản không tz
        df_naive = _make_steady_candles(20)
        df_naive.loc[12, ["open", "high", "low", "close"]] = [96.0, 100.0, 95.0, 96.0]
        df_naive.loc[14, ["open", "high", "low", "close"]] = [106.0, 120.0, 105.0, 118.0]
        assert_candles_valid(self, df_naive)

        fvg_naive, lc_naive = detect_fvg(df_naive, gamma=0.15, m=24)
        self.assertEqual(len(fvg_naive), 1)

        # Múi giờ UTC-aware
        df_tz_utc = df_naive.copy()
        df_tz_utc["time_utc"] = df_tz_utc["time_utc"].dt.tz_localize("UTC")
        fvg_tz_utc, lc_tz_utc = detect_fvg(df_tz_utc, gamma=0.15, m=24)
        pd.testing.assert_frame_equal(fvg_naive, fvg_tz_utc)
        pd.testing.assert_frame_equal(lc_naive, lc_tz_utc)

        # Múi giờ địa phương (Asia/Ho_Chi_Minh: UTC+7)
        df_tz_vn = df_naive.copy()
        df_tz_vn["time_utc"] = df_tz_vn["time_utc"].dt.tz_localize("UTC").dt.tz_convert("Asia/Ho_Chi_Minh")
        fvg_tz_vn, lc_tz_vn = detect_fvg(df_tz_vn, gamma=0.15, m=24)
        pd.testing.assert_frame_equal(fvg_naive, fvg_tz_vn)
        pd.testing.assert_frame_equal(lc_naive, lc_tz_vn)

    def test_18_annual_fvg_distribution_regression_lock(self):
        """18. THỐNG KÊ PHÂN BỐ FVG THEO NĂM TRÊN TẬP DEVELOPMENT (2018–2025):

        GHI CHÚ QUAN TRỌNG:
        Các số lượng FVG dưới đây là KHÓA HỒI QUY (Regression Locks) nhằm bảo đảm
        không làm thay đổi thuật toán trong tương lai. TUYỆT ĐỐI KHÔNG dùng các con số này
        để tinh chỉnh ngược lại tham số gamma.
        """
        fvg_dev, lc_dev = load_fvg("2018-01-01", "2025-06-30", warmup_months=None, gamma=DEFAULT_GAMMA, m=DEFAULT_M)
        self.assertEqual(len(fvg_dev), 5778, "Tổng số FVG Development bảo toàn warm-up phải là 5.778")

        fvg_dev["year"] = fvg_dev["time_candle"].dt.year
        dist = fvg_dev.groupby(["year", "direction"]).size().unstack(fill_value=0)
        dist["TOTAL"] = dist["BULLISH"] + dist["BEARISH"]

        gap_counts = fvg_dev.groupby(["year", "spans_gap"]).size().unstack(fill_value=0)

        lc_dev["year"] = lc_dev["time_candle"].dt.year
        lc_dist = lc_dev.groupby(["year", "event_type"]).size().unstack(fill_value=0)

        print("\n=== ANNUAL FVG DISTRIBUTION (2018-2025 WITH WARMUP ACTIVE PRESERVED) ===")
        print(dist)
        print("\n=== FVG SPANS GAP DISTRIBUTION ===")
        print(gap_counts)
        print("\n=== FVG LIFECYCLE EVENTS DISTRIBUTION ===")
        print(lc_dist)

    def test_19_exact_equality_and_uninitialized_atr_boundary(self):
        """19. Ca biên nghiêm ngặt:

        - Ca bằng nhau: Low[i] == High[i-2] (hoặc High[i] == Low[i-2]) với gamma=0.0 không được sinh FVG.
        - Biên 14 nến: Cùng một gap, đặt ở nến 13 (không có FVG do ATR[12]=NaN), đặt ở nến 14 (có FVG do ATR[13] hợp lệ).
        - Nến chưa đủ 14 nến khởi động ATR: Không sinh FVG.
        - atr_ref <= 0 (dữ liệu phẳng dị thường): Không sinh FVG.
        """
        base_df = _make_steady_candles(14, high=108.0, low=100.0, close=104.0)

        # 1. Ca bằng nhau nghiêm ngặt phía Bullish: Low[14] == High[12] = 108.0 với gamma=0.0
        # Nếu code dùng >= thay vì > thì với gamma=0.0 (min_gap=0.0), gap=0.0 >= 0.0 sẽ sinh FVG sai
        bar_eq_bull = pd.DataFrame([{
            "time_utc": pd.Timestamp("2024-01-02 14:00"),
            "open": 110.0,
            "high": 115.0,
            "low": 108.0,  # Low[14] == High[12] == 108.0
            "close": 112.0,
        }])
        df_eq_bull = pd.concat([base_df, bar_eq_bull], ignore_index=True)
        assert_candles_valid(self, df_eq_bull)
        fvg_eq_b, _ = detect_fvg(df_eq_bull, gamma=0.0, atr_period=14)
        self.assertTrue(fvg_eq_b.empty, "Low[i] == High[i-2] với gamma=0.0 tuyệt đối KHÔNG phải là Bullish FVG (bắt buộc > nghiêm ngặt)")

        # 2. Ca bằng nhau nghiêm ngặt phía Bearish: High[14] == Low[12] = 100.0 với gamma=0.0
        bar_eq_bear = pd.DataFrame([{
            "time_utc": pd.Timestamp("2024-01-02 14:00"),
            "open": 98.0,
            "high": 100.0,  # High[14] == Low[12] == 100.0
            "low": 95.0,
            "close": 96.0,
        }])
        df_eq_bear = pd.concat([base_df, bar_eq_bear], ignore_index=True)
        assert_candles_valid(self, df_eq_bear)
        fvg_eq_bear, _ = detect_fvg(df_eq_bear, gamma=0.0, atr_period=14)
        self.assertTrue(fvg_eq_bear.empty, "High[i] == Low[i-2] với gamma=0.0 tuyệt đối KHÔNG phải là Bearish FVG (bắt buộc < nghiêm ngặt)")

        # 3. Biên 14 nến khởi động ATR: Cùng một gap, một lần đặt ở nến 13 (không có FVG do ATR[12]=NaN), một lần ở nến 14 (có FVG do ATR[13] hợp lệ)
        # Lần 1: Chuỗi 14 nến (chỉ số 0..13), đặt gap tại nến 13 (Low[13] = 115.0 > High[11] = 108.0)
        # Nến 13 có atr_ref = ATR[12] = NaN (chưa đủ 14 nến khởi động) -> Bắt buộc KHÔNG sinh FVG
        df_14_bars = _make_steady_candles(14, high=108.0, low=100.0, close=104.0)
        df_14_bars.loc[13, ["open", "high", "low", "close"]] = [118.0, 125.0, 115.0, 120.0]
        assert_candles_valid(self, df_14_bars)
        fvg_13, _ = detect_fvg(df_14_bars, gamma=0.15, atr_period=14)
        self.assertTrue(fvg_13.empty, "Nến 13 có atr_ref=ATR[12]=NaN (chưa đủ 14 nến), bắt buộc KHÔNG sinh FVG")

        # Lần 2: Chuỗi 15 nến (chỉ số 0..14), đặt đúng gap đó tại nến 14 (Low[14] = 115.0 > High[12] = 108.0)
        # Nến 14 có atr_ref = ATR[13] = 8.0 (đủ 14 nến đầu) -> Bắt buộc sinh đúng 1 FVG tại nến 14
        df_15_bars = _make_steady_candles(15, high=108.0, low=100.0, close=104.0)
        df_15_bars.loc[14, ["open", "high", "low", "close"]] = [118.0, 125.0, 115.0, 120.0]
        assert_candles_valid(self, df_15_bars)
        fvg_14, _ = detect_fvg(df_15_bars, gamma=0.15, atr_period=14)
        self.assertEqual(len(fvg_14), 1, "Nến 14 có atr_ref=ATR[13] đã khởi động, bắt buộc PHẢI sinh FVG")
        self.assertEqual(fvg_14.iloc[0]["bar_index"], 14)
        self.assertAlmostEqual(fvg_14.iloc[0]["gap_size"], 7.0)

        # 4. Nến chưa đủ 14 nến khởi động ATR tổng quát (chuỗi ngắn 10 nến)
        short_times = pd.date_range("2024-01-02 00:00", periods=10, freq="1h")
        short_df = pd.DataFrame({
            "time_utc": short_times,
            "open": [100.0] * 10,
            "high": [105.0] * 10,
            "low": [95.0] * 10,
            "close": [100.0] * 10,
        })
        short_df.loc[5, ["open", "high", "low", "close"]] = [200.0, 220.0, 190.0, 210.0]
        assert_candles_valid(self, short_df)
        fvg_uninit, _ = detect_fvg(short_df, gamma=0.15, atr_period=14)
        self.assertTrue(fvg_uninit.empty, "Nến chưa có giá trị ATR hợp lệ không được sinh FVG")

        # 5. atr_ref <= 0 (dữ liệu phẳng hoàn toàn, TR = 0)
        flat_times = pd.date_range("2024-01-02 00:00", periods=15, freq="1h")
        flat_df = pd.DataFrame({
            "time_utc": flat_times,
            "open": [100.0] * 15,
            "high": [100.0] * 15,
            "low": [100.0] * 15,
            "close": [100.0] * 15,
        })
        flat_df.loc[14, ["open", "high", "low", "close"]] = [110.0, 120.0, 105.0, 115.0]
        assert_candles_valid(self, flat_df)
        fvg_flat, _ = detect_fvg(flat_df, gamma=0.15, atr_period=14)
        self.assertTrue(fvg_flat.empty, "atr_ref <= 0 không được sinh FVG")

    def test_20_brute_force_exhaustive_randomized_synthetic_comparison(self):
        """20. ĐỐI CHIẾU VÉT CẠN (BRUTE-FORCE EXHAUSTIVE COMPARISON) TRÊN DỮ LIỆU GIẢ NGẪU NHIÊN:

        Chạy qua nhiều seed ngẫu nhiên [42, 2024, 7777], chèn khoảng trống thời gian (>1h và 48h cuối tuần),
        kiểm tra đa tham số m (m=5 và m=24), và khẳng định:
        - Bắt buộc có FVG cùng đủ cả 4 loại sự kiện (CREATED, TESTED, INVALIDATED, EXPIRED) để test không qua trống rỗng.
        - Bắt buộc kích hoạt nhánh biên 'TESTED đúng ở nến thứ M' (bar_index - bar_index_FVG == m).
        - Đối chiếu 100% khớp tuyệt đối các cột phân loại/thời gian và khớp trong dung sai số thực (np.allclose)
          cho các cột giá/ATR với hàm tham chiếu ngây thơ độc lập (naive ground-truth).
        """
        def _naive_reference_fvg(df, gamma=0.15, m=24, atr_period=14):
            n = len(df)
            highs = df["high"].to_numpy(dtype=float)
            lows = df["low"].to_numpy(dtype=float)
            closes = df["close"].to_numpy(dtype=float)
            times = df["time_utc"].to_numpy()
            dur = pd.Timedelta("1h")
            dur_ns = np.timedelta64(1, "h")

            # Tính ATR thuần túy từng bước
            tr = np.zeros(n, dtype=float)
            tr[0] = highs[0] - lows[0]
            for k in range(1, n):
                tr[k] = max(highs[k] - lows[k], abs(highs[k] - closes[k - 1]), abs(lows[k] - closes[k - 1]))
            atr = np.full(n, np.nan, dtype=float)
            if n >= atr_period:
                atr[atr_period - 1] = np.mean(tr[:atr_period])
                for k in range(atr_period, n):
                    atr[k] = (atr[k - 1] * (atr_period - 1) + tr[k]) / atr_period

            fvgs = []
            for i in range(2, n):
                atr_ref = atr[i - 1]
                if np.isnan(atr_ref) or atr_ref <= 0:
                    continue
                min_gap = gamma * atr_ref
                dt1 = times[i - 1] - times[i - 2]
                dt2 = times[i] - times[i - 1]
                spans_gap = bool((dt1 > dur_ns) or (dt2 > dur_ns))

                if lows[i] > highs[i - 2]:
                    gap = lows[i] - highs[i - 2]
                    if gap >= min_gap:
                        fvgs.append({
                            "fvg_id": len(fvgs), "direction": "BULLISH", "bar_index": i,
                            "time_candle": pd.Timestamp(times[i]), "time_available": pd.Timestamp(times[i]) + dur,
                            "top": float(lows[i]), "bottom": float(highs[i - 2]),
                            "near_edge": float(lows[i]), "far_edge": float(highs[i - 2]),
                            "gap_size": float(gap), "atr_ref": float(atr_ref), "spans_gap": spans_gap,
                        })
                elif highs[i] < lows[i - 2]:
                    gap = lows[i - 2] - highs[i]
                    if gap >= min_gap:
                        fvgs.append({
                            "fvg_id": len(fvgs), "direction": "BEARISH", "bar_index": i,
                            "time_candle": pd.Timestamp(times[i]), "time_available": pd.Timestamp(times[i]) + dur,
                            "top": float(lows[i - 2]), "bottom": float(highs[i]),
                            "near_edge": float(highs[i]), "far_edge": float(lows[i - 2]),
                            "gap_size": float(gap), "atr_ref": float(atr_ref), "spans_gap": spans_gap,
                        })

            lc = []
            for f in fvgs:
                fid = f["fvg_id"]
                direction = f["direction"]
                i = f["bar_index"]
                top = f["top"]
                bottom = f["bottom"]
                near = f["near_edge"]
                lc.append({
                    "fvg_id": fid, "event_type": "CREATED", "bar_index": i,
                    "time_candle": f["time_candle"], "time_available": f["time_available"],
                    "price_trigger": near,
                })
                tested = False
                just_tested_at_m = False
                for j in range(i + 1, n):
                    tj = pd.Timestamp(times[j])
                    t_av_j = tj + dur
                    cj, hj, lj = closes[j], highs[j], lows[j]
                    if just_tested_at_m:
                        inv = (cj < bottom) if direction == "BULLISH" else (cj > top)
                        if inv:
                            lc.append({"fvg_id": fid, "event_type": "INVALIDATED", "bar_index": j,
                                       "time_candle": tj, "time_available": t_av_j, "price_trigger": cj})
                        else:
                            lc.append({"fvg_id": fid, "event_type": "EXPIRED", "bar_index": j,
                                       "time_candle": tj, "time_available": t_av_j, "price_trigger": np.nan})
                        break
                    inv = (cj < bottom) if direction == "BULLISH" else (cj > top)
                    if inv:
                        lc.append({"fvg_id": fid, "event_type": "INVALIDATED", "bar_index": j,
                                   "time_candle": tj, "time_available": t_av_j, "price_trigger": cj})
                        break
                    if not tested:
                        touches = (lj <= top) if direction == "BULLISH" else (hj >= bottom)
                        if touches:
                            lc.append({"fvg_id": fid, "event_type": "TESTED", "bar_index": j,
                                       "time_candle": tj, "time_available": t_av_j, "price_trigger": near})
                            tested = True
                            if (j - i) == m:
                                just_tested_at_m = True
                                continue
                    if (j - i) >= m:
                        lc.append({"fvg_id": fid, "event_type": "EXPIRED", "bar_index": j,
                                   "time_candle": tj, "time_available": t_av_j, "price_trigger": np.nan})
                        break

            ref_fvg_df = pd.DataFrame(fvgs)
            ref_lc_df = pd.DataFrame(lc)
            if not ref_lc_df.empty:
                ord_map = {"CREATED": 0, "TESTED": 1, "INVALIDATED": 2, "EXPIRED": 3}
                ref_lc_df["_ord"] = ref_lc_df["event_type"].map(ord_map)
                ref_lc_df = ref_lc_df.sort_values(by=["time_available", "fvg_id", "_ord"], kind="stable").reset_index(drop=True)
                ref_lc_df["lifecycle_id"] = np.arange(len(ref_lc_df), dtype=int)
                del ref_lc_df["_ord"]
            return ref_fvg_df, ref_lc_df

        seeds = [42, 2024, 7777]
        m_values = [5, 24]
        total_fvgs_all = 0
        all_event_types_found = set()
        print("\n=== TEST 20: BRUTE-FORCE RANDOMIZED MULTI-SEED & MULTI-M SUMMARY ===")

        for m_test in m_values:
            tested_at_m_total_for_m = 0
            for seed in seeds:
                rng = np.random.default_rng(seed)
                n_bars = 200
                p = 100.0
                records = []
                t = pd.Timestamp("2024-01-01 00:00")
                for i in range(n_bars):
                    jump = rng.normal(0, 2.5)
                    # Chèn các khoảng trống thời gian và bước nhảy giá để sinh cả spans_gap=True và spans_gap=False
                    if i == 50:
                        t += pd.Timedelta("2h")  # bước nhảy giờ nghỉ bảo trì (>1h)
                        jump += 12.0             # nhảy giá tạo Bullish FVG qua khoảng trống
                    elif i == 120:
                        t += pd.Timedelta("48h") # bước nhảy 48h cuối tuần
                        jump -= 12.0             # sụt giá tạo Bearish FVG qua cuối tuần
                    o = p
                    c = p + jump
                    h = max(o, c) + abs(rng.normal(0, 1.2))
                    l = min(o, c) - abs(rng.normal(0, 1.2))
                    records.append({"time_utc": t, "open": o, "high": h, "low": l, "close": c})
                    p = c
                    t += pd.Timedelta("1h")
                syn_df = pd.DataFrame(records)
                assert_candles_valid(self, syn_df)

                act_fvg, act_lc = detect_fvg(syn_df, gamma=0.15, m=m_test, atr_period=14)
                ref_fvg, ref_lc = _naive_reference_fvg(syn_df, gamma=0.15, m=m_test, atr_period=14)

                # Khẳng định không thể qua trống rỗng (Non-vacuous assertions)
                self.assertGreater(len(act_fvg), 0, f"m={m_test}, Seed {seed}: Phải phát hiện ít nhất một FVG")
                self.assertTrue((act_fvg["spans_gap"] == True).any(), f"m={m_test}, Seed {seed}: Phải có ít nhất một FVG spans_gap=True")
                self.assertTrue((act_fvg["spans_gap"] == False).any(), f"m={m_test}, Seed {seed}: Phải có ít nhất một FVG spans_gap=False")

                # Khẳng định đủ cả 4 loại sự kiện vòng đời
                recorded_events = set(act_lc["event_type"].unique())
                expected_events = {"CREATED", "TESTED", "INVALIDATED", "EXPIRED"}
                self.assertEqual(recorded_events, expected_events, f"m={m_test}, Seed {seed}: Thiếu loại sự kiện: {expected_events - recorded_events}")
                for ev in expected_events:
                    self.assertGreater((act_lc["event_type"] == ev).sum(), 0, f"m={m_test}, Seed {seed}: Số lượng sự kiện {ev} phải > 0")

                # Đếm sự kiện TESTED xảy ra đúng ở nến thứ M (bar_index - bar_index_FVG == m_test)
                fvg_origin_map = act_fvg.set_index("fvg_id")["bar_index"].to_dict()
                tested_at_m_count = 0
                for _, row in act_lc[act_lc["event_type"] == "TESTED"].iterrows():
                    f_id = int(row["fvg_id"])
                    orig_bar = fvg_origin_map[f_id]
                    t_bar = int(row["bar_index"])
                    if (t_bar - orig_bar) == m_test:
                        tested_at_m_count += 1

                tested_at_m_total_for_m += tested_at_m_count
                total_fvgs_all += len(act_fvg)
                all_event_types_found.update(recorded_events)

                ev_counts = dict(act_lc["event_type"].value_counts())
                print(f"  [PASS] m={m_test:2d} | Seed={seed:4d} | FVGs={len(act_fvg):2d} (gap_true={(act_fvg['spans_gap']==True).sum()}, gap_false={(act_fvg['spans_gap']==False).sum()}) | Events: {ev_counts} | tested_at_m={tested_at_m_count}")

                self.assertEqual(len(act_fvg), len(ref_fvg), f"m={m_test}, Seed {seed}: Lệch số lượng FVG")
                self.assertEqual(len(act_lc), len(ref_lc), f"m={m_test}, Seed {seed}: Lệch số lượng sự kiện vòng đời")

                # Đối chiếu 100% cột phân loại/thời gian bảng FVG
                for col in ["direction", "bar_index", "time_candle", "time_available", "spans_gap"]:
                    self.assertTrue((act_fvg[col].values == ref_fvg[col].values).all(), f"Khác biệt cột FVG '{col}' tại m={m_test}, seed={seed}")
                # Đối chiếu khớp trong dung sai số thực np.allclose cho cột giá/ATR
                for col in ["top", "bottom", "near_edge", "far_edge", "gap_size", "atr_ref"]:
                    self.assertTrue(np.allclose(act_fvg[col].values, ref_fvg[col].values), f"Khác biệt cột số thực FVG '{col}' tại m={m_test}, seed={seed}")

                # Đối chiếu 100% cột phân loại/thời gian bảng Lifecycle
                for col in ["event_type", "bar_index", "time_candle", "time_available"]:
                    self.assertTrue((act_lc[col].values == ref_lc[col].values).all(), f"Khác biệt cột LC '{col}' tại m={m_test}, seed={seed}")
                self.assertTrue(np.allclose(act_lc["price_trigger"].fillna(-999), ref_lc["price_trigger"].fillna(-999)))

            # Khẳng định tại mỗi giá trị m, nhánh biên "TESTED đúng ở nến thứ M" (bar_index - bar_index_FVG == m) phải được kích hoạt ít nhất 1 lần
            self.assertGreater(
                tested_at_m_total_for_m,
                0,
                f"Tại m={m_test}: Phải có ít nhất 1 FVG có TESTED ở bar_index - bar_index_FVG == m",
            )

        self.assertGreater(total_fvgs_all, 100, "Tổng số FVG qua các cấu hình phải dồi dào (>100)")
        self.assertEqual(all_event_types_found, {"CREATED", "TESTED", "INVALIDATED", "EXPIRED"})


if __name__ == "__main__":
    unittest.main()
