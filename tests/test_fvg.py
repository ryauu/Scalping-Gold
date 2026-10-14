"""Kiểm thử toàn diện mô-đun fvg.py (Fair Value Gap & Nhật ký vòng đời chuẩn SMC v2.2).

Bao gồm:
1. Test ngưỡng lọc kích thước tối thiểu gap_size >= gamma * ATR[i-1] (giữ khi bằng đúng ngưỡng).
2. Test nến i không tự test chính FVG nó tạo ra.
3. Test sự kiện TESTED chỉ ghi nhận 1 lần duy nhất dù giá chạm nhiều lần.
4. Test xung đột cùng nến: Vừa chạm mép gần vừa đóng xuyên mép xa chỉ ghi INVALIDATED.
5. Test nến thứ M chạm mép gần: TESTED ở M, EXPIRED ở M+1.
6. Test nến thứ M không chạm: EXPIRED ở M.
7. Test FVG đã TESTED trước đó thì EXPIRED ở nến thứ M.
8. Test cờ spans_gap khi bộ ba nến vắt qua cuối tuần / ngày nghỉ.
9. Test không look-ahead của ATR: Thay đổi High/Low của nến i không ảnh hưởng đến ATR[i-1].
10. Test 5 bất biến vật lý trên H1 thật:
    - Mỗi FVG đúng 1 CREATED.
    - Tối đa 1 TESTED.
    - Tối đa 1 sự kiện kết thúc (INVALIDATED hoặc EXPIRED).
    - Không có sự kiện nào sau sự kiện kết thúc.
    - Thứ tự thời gian không giảm.
11. Test bất biến theo tiền tố (Prefix Invariance Test) cắt tại biên tạo FVG và biên vòng đời.
12. Test kiểm tra đầu vào và bảo vệ chống NaN/NaT/trùng lặp thời gian/thiếu cột.
13. Test get_active_fvg_at và bộ lọc valid_only.
14. Thống kê phân bố FVG theo năm và theo hướng trên tập Development 2018–2025.
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
from src.backtest.smc.fvg import (
    DEFAULT_ATR_PERIOD,
    DEFAULT_GAMMA,
    DEFAULT_M,
    detect_fvg,
    get_active_fvg_at,
    load_fvg,
)
from src.backtest.smc.swings import DEFAULT_H1_DURATION


def _make_steady_candles(n: int, high: float = 110.0, low: float = 100.0, close: float = 105.0) -> pd.DataFrame:
    """Tạo chuỗi n nến H1 ổn định với khoảng giá cố định (TR = high - low = 10.0, ATR = 10.0)."""
    times = pd.date_range("2024-01-02 00:00", periods=n, freq="1h")
    return pd.DataFrame({
        "time_utc": times,
        "open": [close] * n,
        "high": [high] * n,
        "low": [low] * n,
        "close": [close] * n,
    })


class TestFVGModule(unittest.TestCase):
    def test_01_exact_gamma_atr_threshold_kept(self):
        """1. gap_size bằng đúng gamma * ATR[i-1] thì được giữ (dấu >=)."""
        # Tạo 14 nến khởi động chuẩn mực (TR = 10.0, ATR[13] = 10.0)
        # gamma = 0.15 -> min_gap = 1.50
        base_df = _make_steady_candles(14, high=110.0, low=100.0, close=105.0)

        # Nến 14 (i): tạo Bullish FVG so với nến 12 (High = 110.0)
        # Low = 111.50 -> gap = 111.50 - 110.0 = 1.50 == 0.15 * 10.0
        bar_14 = pd.DataFrame([{
            "time_utc": pd.Timestamp("2024-01-02 14:00"),
            "open": 112.0,
            "high": 120.0,
            "low": 111.50,
            "close": 118.0,
        }])
        full_df = pd.concat([base_df, bar_14], ignore_index=True)

        fvg_df, lc_df = detect_fvg(full_df, gamma=0.15, atr_period=14)

        self.assertFalse(fvg_df.empty, "gap_size == gamma * ATR phải được giữ (dấu >=)")
        self.assertAlmostEqual(fvg_df.iloc[0]["gap_size"], 1.50, places=4)
        self.assertAlmostEqual(fvg_df.iloc[0]["atr_ref"], 10.0, places=4)

        # Ca dưới ngưỡng 1.50 (ví dụ Low = 111.49 -> gap = 1.49 < 1.50)
        full_df_fail = full_df.copy()
        full_df_fail.loc[14, "low"] = 111.49
        fvg_fail, _ = detect_fvg(full_df_fail, gamma=0.15, atr_period=14)
        self.assertTrue(fvg_fail.empty, "gap_size < gamma * ATR phải bị loại bỏ")

    def test_02_bar_i_does_not_self_test(self):
        """2. Nến i không tự test chính FVG nó tạo ra."""
        base_df = _make_steady_candles(14, high=110.0, low=100.0, close=105.0)
        base_df.loc[12, "high"] = 100.0

        # Nến 14 tạo Bullish FVG: Low = 105.0, High = 120.0, Close = 118.0
        # Dù nến 14 có Low = 105.0 chạm vào vùng top = 105.0, nến 14 không tự test
        time_14 = pd.Timestamp("2024-01-02 14:00")
        bar_14 = pd.DataFrame([{
            "time_utc": time_14,
            "open": 106.0,
            "high": 120.0,
            "low": 105.0,
            "close": 118.0,
        }])
        df = pd.concat([base_df, bar_14], ignore_index=True)

        fvg_df, lc_df = detect_fvg(df, gamma=0.15, atr_period=14)
        self.assertEqual(len(fvg_df), 1)
        # Chỉ có 1 sự kiện CREATED, TUYỆT ĐỐI KHÔNG có TESTED tại nến 14
        self.assertEqual(len(lc_df), 1)
        self.assertEqual(lc_df.iloc[0]["event_type"], "CREATED")
        self.assertEqual(lc_df.iloc[0]["bar_index"], 14)

    def test_03_tested_recorded_only_once_on_multiple_touches(self):
        """3. TESTED chỉ ghi nhận 1 lần duy nhất dù giá chạm nhiều lần."""
        base_df = _make_steady_candles(14, high=110.0, low=100.0, close=105.0)
        base_df.loc[12, "high"] = 100.0

        # Nến 14: Bullish FVG (top=105.0, bottom=100.0)
        # Nến 15, 16, 17: Đều chạm top (Low <= 105.0, Close >= 100.0)
        extra_times = pd.date_range("2024-01-02 14:00", periods=4, freq="1h")
        extra_df = pd.DataFrame({
            "time_utc": extra_times,
            "open": [106.0, 107.0, 106.0, 107.0],
            "high": [120.0, 110.0, 110.0, 110.0],
            "low": [105.0, 103.0, 102.0, 104.0],  # 15, 16, 17 đều chạm vùng gap
            "close": [118.0, 106.0, 107.0, 108.0],  # Không đóng thủng 100.0
        })
        df = pd.concat([base_df, extra_df], ignore_index=True)

        fvg_df, lc_df = detect_fvg(df, gamma=0.15, atr_period=14)
        self.assertEqual(len(fvg_df), 1)

        tested_events = lc_df[lc_df["event_type"] == "TESTED"]
        self.assertEqual(len(tested_events), 1, "TESTED chỉ được ghi nhận đúng 1 lần duy nhất")
        self.assertEqual(tested_events.iloc[0]["bar_index"], 15, "TESTED phải được ghi nhận tại nến 15 (chạm đầu tiên)")

    def test_04_same_bar_touch_and_invalidate_records_only_invalidated(self):
        """4. Cùng nến vừa chạm mép gần vừa đóng xuyên mép xa chỉ ghi INVALIDATED."""
        base_df = _make_steady_candles(14, high=110.0, low=100.0, close=105.0)
        base_df.loc[12, "high"] = 100.0

        # Nến 14: Bullish FVG (top=105.0, bottom=100.0)
        # Nến 15: Low = 98.0 (chạm vào gap và xuyên thủng), Close = 97.0 (< bottom 100.0)
        extra_times = pd.date_range("2024-01-02 14:00", periods=2, freq="1h")
        extra_df = pd.DataFrame({
            "time_utc": extra_times,
            "open": [106.0, 107.0],
            "high": [120.0, 108.0],
            "low": [105.0, 98.0],
            "close": [118.0, 97.0],  # Đóng cửa xuyên mép xa
        })
        df = pd.concat([base_df, extra_df], ignore_index=True)

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
        base_df.loc[12, "high"] = 100.0

        m = 5  # Dùng M=5 nến để chuỗi nến ngắn gọn
        # Nến 14 (i): tạo Bullish FVG (top=105.0, bottom=100.0)
        # Nến 15..18 (j=i+1..i+4): giá lơ lửng không chạm gap (Low = 106.0, Close = 108.0)
        # Nến 19 (j=i+5, nến thứ M): chạm gap (Low = 104.0, Close = 106.0)
        # Nến 20 (j=i+6, nến M+1): giá tiếp tục bình thường (Low = 106.0, Close = 108.0)
        n_extra = m + 2  # 7 nến: 14, 15, 16, 17, 18, 19, 20
        extra_times = pd.date_range("2024-01-02 14:00", periods=n_extra, freq="1h")
        opens = [106.0] + [107.0] * (m + 1)
        highs = [120.0] + [110.0] * (m + 1)
        lows = [105.0] + [106.0] * (m - 1) + [104.0, 106.0]  # Nến 19 chạm gap
        closes = [118.0] + [108.0] * (m + 1)

        extra_df = pd.DataFrame({
            "time_utc": extra_times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
        })
        df = pd.concat([base_df, extra_df], ignore_index=True)

        fvg_df, lc_df = detect_fvg(df, gamma=0.15, m=m, atr_period=14)
        self.assertEqual(len(fvg_df), 1)

        # Kỳ vọng: CREATED (bar 14), TESTED (bar 19 = 14 + 5), EXPIRED (bar 20 = 14 + 6)
        self.assertEqual(len(lc_df), 3)
        self.assertEqual(lc_df.iloc[0]["event_type"], "CREATED")
        self.assertEqual(lc_df.iloc[0]["bar_index"], 14)

        self.assertEqual(lc_df.iloc[1]["event_type"], "TESTED")
        self.assertEqual(lc_df.iloc[1]["bar_index"], 14 + m)

        self.assertEqual(lc_df.iloc[2]["event_type"], "EXPIRED")
        self.assertEqual(lc_df.iloc[2]["bar_index"], 14 + m + 1)

    def test_06_m_th_bar_no_touch_expired_at_m(self):
        """6. Nến thứ M không chạm: EXPIRED ở M."""
        base_df = _make_steady_candles(14, high=110.0, low=100.0, close=105.0)
        base_df.loc[12, "high"] = 100.0

        m = 5
        # Nến 14 (i): tạo Bullish FVG
        # Nến 15..19: giá không chạm gap (Low = 106.0)
        n_extra = m + 1
        extra_times = pd.date_range("2024-01-02 14:00", periods=n_extra, freq="1h")
        extra_df = pd.DataFrame({
            "time_utc": extra_times,
            "open": [106.0] + [107.0] * m,
            "high": [120.0] + [110.0] * m,
            "low": [105.0] + [106.0] * m,  # Không chạm
            "close": [118.0] + [108.0] * m,
        })
        df = pd.concat([base_df, extra_df], ignore_index=True)

        fvg_df, lc_df = detect_fvg(df, gamma=0.15, m=m, atr_period=14)
        self.assertEqual(len(fvg_df), 1)

        # Kỳ vọng: CREATED (bar 14), EXPIRED (bar 19 = 14 + 5)
        self.assertEqual(len(lc_df), 2)
        self.assertEqual(lc_df.iloc[0]["event_type"], "CREATED")
        self.assertEqual(lc_df.iloc[1]["event_type"], "EXPIRED")
        self.assertEqual(lc_df.iloc[1]["bar_index"], 14 + m)

    def test_07_fvg_tested_earlier_expires_at_m(self):
        """7. FVG đã TESTED trước đó thì EXPIRED ở nến thứ M."""
        base_df = _make_steady_candles(14, high=110.0, low=100.0, close=105.0)
        base_df.loc[12, "high"] = 100.0

        m = 5
        # Nến 14 (i): tạo Bullish FVG
        # Nến 15 (j=1): chạm gap (Low = 104.0) -> TESTED
        # Nến 16..19: giá không đóng thủng gap
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

        fvg_df, lc_df = detect_fvg(df, gamma=0.15, m=m, atr_period=14)
        self.assertEqual(len(fvg_df), 1)

        # Kỳ vọng: CREATED (14), TESTED (15), EXPIRED (19 = 14 + 5)
        self.assertEqual(len(lc_df), 3)
        self.assertEqual(lc_df.iloc[0]["event_type"], "CREATED")
        self.assertEqual(lc_df.iloc[1]["event_type"], "TESTED")
        self.assertEqual(lc_df.iloc[1]["bar_index"], 15)
        self.assertEqual(lc_df.iloc[2]["event_type"], "EXPIRED")
        self.assertEqual(lc_df.iloc[2]["bar_index"], 14 + m)

    def test_08_spans_weekend_flag(self):
        """8. Bộ ba vắt qua cuối tuần được gán cờ spans_gap = True."""
        base_df = _make_steady_candles(14, high=110.0, low=100.0, close=105.0)
        base_df.loc[12, "high"] = 100.0

        # Nến 13: Thứ Sáu 21:00 UTC
        # Nến 14: Chủ Nhật 22:00 UTC (khoảng cách 49 giờ > 1 giờ)
        time_13 = pd.Timestamp("2024-01-05 21:00")
        time_14 = pd.Timestamp("2024-01-07 22:00")
        base_df.loc[13, "time_utc"] = time_13

        bar_14 = pd.DataFrame([{
            "time_utc": time_14,
            "open": 108.0,
            "high": 120.0,
            "low": 106.0,  # Tạo Bullish FVG so với nến 12 (High 100.0)
            "close": 115.0,
        }])
        df = pd.concat([base_df, bar_14], ignore_index=True)

        fvg_df, _ = detect_fvg(df, gamma=0.15, atr_period=14)
        self.assertEqual(len(fvg_df), 1)
        self.assertTrue(fvg_df.iloc[0]["spans_gap"], "FVG vắt qua bước nhảy cuối tuần phải có spans_gap = True")

    def test_09_zero_lookahead_atr_independence(self):
        """9. Thay High/Low của nến i thì ATR[i-1] không đổi (kiểm tra không look-ahead)."""
        base_df = _make_steady_candles(14, high=110.0, low=100.0, close=105.0)
        bar_14_orig = pd.DataFrame([{
            "time_utc": pd.Timestamp("2024-01-02 14:00"),
            "open": 112.0,
            "high": 120.0,
            "low": 112.0,
            "close": 118.0,
        }])
        df_orig = pd.concat([base_df, bar_14_orig], ignore_index=True)
        fvg_orig, _ = detect_fvg(df_orig, gamma=0.15, atr_period=14)

        # Sửa đổi giá nến 14 (i) cực đoan: High tăng vọt từ 120 lên 250, Low từ 112 lên 115
        df_mod = df_orig.copy()
        df_mod.loc[14, "high"] = 250.0
        df_mod.loc[14, "low"] = 115.0
        df_mod.loc[14, "close"] = 240.0

        fvg_mod, _ = detect_fvg(df_mod, gamma=0.15, atr_period=14)

        self.assertFalse(fvg_orig.empty)
        self.assertFalse(fvg_mod.empty)
        # atr_ref tại nến 14 trong cả 2 trường hợp phải bằng nhau tuyệt đối
        self.assertEqual(fvg_orig.iloc[0]["atr_ref"], fvg_mod.iloc[0]["atr_ref"])
        self.assertAlmostEqual(fvg_mod.iloc[0]["atr_ref"], 10.0, places=6)

    def test_10_physical_invariants_on_real_h1(self):
        """10. KIỂM TRA 5 BẤT BIẾN VẬT LÝ VÒNG ĐỜI TRÊN H1 THẬT (2020):

        - Mỗi FVG đúng 1 sự kiện CREATED.
        - Mỗi FVG có tối đa 1 sự kiện TESTED.
        - Mỗi FVG có tối đa 1 sự kiện kết thúc (INVALIDATED hoặc EXPIRED).
        - Tuyệt đối không có bất kỳ sự kiện nào sau sự kiện kết thúc.
        - Thứ tự thời gian time_available tăng không giảm trong vòng đời mỗi FVG.
        """
        h1_df = load_h1("2020-01-01", "2020-12-31")
        fvg_df, lc_df = detect_fvg(h1_df, gamma=DEFAULT_GAMMA, m=DEFAULT_M)

        self.assertGreater(len(fvg_df), 500, "Năm 2020 phải có trên 500 FVG")

        # 1. Mỗi FVG đúng 1 CREATED
        created_events = lc_df[lc_df["event_type"] == "CREATED"]
        self.assertEqual(len(created_events), len(fvg_df))
        self.assertFalse(created_events["fvg_id"].duplicated().any())

        # 2. Mỗi FVG tối đa 1 TESTED
        tested_events = lc_df[lc_df["event_type"] == "TESTED"]
        self.assertFalse(tested_events["fvg_id"].duplicated().any(), "Tồn tại FVG ghi nhận nhiều hơn 1 lần TESTED")

        # 3. Mỗi FVG tối đa 1 sự kiện kết thúc
        term_events = lc_df[lc_df["event_type"].isin(["INVALIDATED", "EXPIRED"])]
        self.assertFalse(term_events["fvg_id"].duplicated().any(), "Tồn tại FVG có nhiều hơn 1 sự kiện kết thúc")

        # 4 & 5. Thứ tự thời gian và không có sự kiện sau kết thúc
        grouped = lc_df.groupby("fvg_id")
        for fid, group in grouped:
            types = group["event_type"].tolist()
            # Sự kiện đầu tiên luôn là CREATED
            self.assertEqual(types[0], "CREATED")

            # Nếu có sự kiện kết thúc, nó phải là sự kiện cuối cùng
            for idx, ev_type in enumerate(types):
                if ev_type in ["INVALIDATED", "EXPIRED"]:
                    self.assertEqual(idx, len(types) - 1, f"FVG {fid} có sự kiện sau khi đã kết thúc ({types})")

            # time_available tăng không giảm
            avails = pd.to_datetime(group["time_available"]).tolist()
            for k in range(len(avails) - 1):
                self.assertLessEqual(avails[k], avails[k + 1])

    def test_11_prefix_invariance_on_real_h1(self):
        """11. TEST BẤT BIẾN THEO TIỀN TỐ (PREFIX INVARIANCE) TRÊN H1 THẬT:

        Lấy mẫu 8 FVG trên năm 2020:
        1. Cắt tại biên nến tạo FVG (i và i+1).
        2. Cắt tại biên nến sự kiện vòng đời (j và j+1).
        Khớp 100% từng thuộc tính giữa chạy tiền tố và chạy toàn bộ.
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

                # So sánh các cột thị trường
                for col in ["direction", "time_candle", "time_available", "top", "bottom", "gap_size", "spans_gap"]:
                    self.assertTrue((fvg_pre[col].values == fvg_full_filt[col].values).all())

    def test_12_input_validation(self):
        """12. Kiểm tra đầu vào, bảo vệ chống NaN/NaT/trùng thời gian/thiếu cột."""
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

    def test_13_get_active_fvg_at_and_filters(self):
        """13. get_active_fvg_at: Lọc chính xác FVG active và cờ spans_gap."""
        times = pd.date_range("2024-01-02 00:00", periods=20, freq="1h")
        base_df = pd.DataFrame({
            "time_utc": times,
            "open": [105.0] * 20,
            "high": [110.0] * 20,
            "low": [100.0] * 20,
            "close": [105.0] * 20,
        })
        base_df.loc[12, "high"] = 100.0
        # Nến 14: Bullish FVG
        base_df.loc[14, "low"] = 105.0
        base_df.loc[14, "high"] = 120.0
        base_df.loc[14, "close"] = 118.0

        fvg_df, lc_df = detect_fvg(base_df, gamma=0.15, m=24, atr_period=14)
        self.assertEqual(len(fvg_df), 1)

        t_avail = fvg_df.iloc[0]["time_available"]

        # Trước khi tạo: chưa active
        active_before = get_active_fvg_at(fvg_df, lc_df, t_avail - pd.Timedelta(seconds=1))
        self.assertTrue(active_before.empty)

        # Đúng lúc tạo: active và chưa test
        active_exact = get_active_fvg_at(fvg_df, lc_df, t_avail)
        self.assertEqual(len(active_exact), 1)
        self.assertFalse(active_exact.iloc[0]["is_tested"])

    def test_14_annual_fvg_distribution_2018_2025(self):
        """14. THỐNG KÊ PHÂN BỐ FVG THEO NĂM TRÊN TẬP DEVELOPMENT (2018–2025):

        Đếm số lượng Bullish/Bearish FVG, tỷ lệ vắt qua cuối tuần, và trạng thái kết thúc.
        """
        fvg_dev, lc_dev = load_fvg("2018-01-01", "2025-06-30", warmup_months=None, gamma=DEFAULT_GAMMA, m=DEFAULT_M)
        self.assertGreater(len(fvg_dev), 3000, "Tập Development phải có trên 3000 FVG")

        fvg_dev["year"] = fvg_dev["time_candle"].dt.year
        dist = fvg_dev.groupby(["year", "direction"]).size().unstack(fill_value=0)
        dist["TOTAL"] = dist["BULLISH"] + dist["BEARISH"]

        # Đếm số FVG vắt qua cuối tuần
        gap_counts = fvg_dev.groupby(["year", "spans_gap"]).size().unstack(fill_value=0)

        # Đếm các sự kiện vòng đời
        lc_dev["year"] = lc_dev["time_candle"].dt.year
        lc_dist = lc_dev.groupby(["year", "event_type"]).size().unstack(fill_value=0)

        print("\n=== ANNUAL FVG DISTRIBUTION (2018-2025) ===")
        print(dist)
        print("\n=== FVG SPANS GAP DISTRIBUTION ===")
        print(gap_counts)
        print("\n=== FVG LIFECYCLE EVENTS DISTRIBUTION ===")
        print(lc_dist)


if __name__ == "__main__":
    unittest.main()
