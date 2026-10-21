"""Bộ kiểm thử toàn diện cho mô-đun src/backtest/smc/signals.py (SMC Sweep Confirmation v1.2).

Bao gồm trọn vẹn 5 nhóm test bắt buộc theo PROTOCOL.md:
1. Nhóm 1: Thác nước 6 bước kiểm tra hủy tín hiệu (Waterfall Priority Tests):
   - Test riêng từng mã:
     * DROP_CONFLICT_SIGNAL (c thỏa cả BUY và SELL).
     * DROP_SPLIT_END (close_time_c >= split_end).
     * DROP_MARKET_CLOSED (không có hàng H1 tại close_time_c).
     * DROP_DAILY_BREAK (close_time_c rơi vào 17:00-18:00 New York).
     * DROP_POSITION_BUSY (đang có vị thế mở chưa đóng).
     * DROP_INVALID_RDIST (vi phạm sàn chi phí hoặc trần khoảng cách SL).
   - Test đa vi phạm (Multi-violation): Một tín hiệu thỏa đồng thời nhiều điều kiện hủy
     (ví dụ vừa DROP_POSITION_BUSY vừa DROP_INVALID_RDIST) chỉ tăng đúng 1 bộ đếm duy nhất.
2. Nhóm 2: Sửa đổi nội dung C9 (Xung lực Displacement sau Sweep):
   - FVG hoàn tất tại i = s (nến xung lực i-1 = s-1 trước sweep) -> BỊ LOẠI.
   - FVG hoàn tất tại i = s+1 (nến xung lực i-1 = s tại sweep) -> ĐƯỢC NHẬN.
3. Nhóm 3: Sweep giữ mở khi thiếu FVG (C6.2):
   - Sự kiện cấu trúc đầu tiên thiếu FVG -> sweep vẫn giữ OPEN.
   - Sự kiện cấu trúc thứ hai có FVG -> tín hiệu được kích hoạt thành công.
4. Nhóm 4: Tiêu thụ sweep vô điều kiện khi tín hiệu bị hủy (C6.3):
   - Tín hiệu bị hủy bởi bất kỳ mã nào trong thác nước -> sweep bị CONSUMED ngay lập tức, không hoãn.
5. Nhóm 5: Ca biên cửa sổ 24 hàng & vắt qua cuối tuần:
   - c = s + 24 hàng H1 -> HỢP LỆ.
   - c = s + 25 hàng H1 -> EXPIRED_WINDOW.
   - 24 hàng vắt qua cuối tuần -> đếm chuẩn xác theo số hàng dữ liệu.
6. Nhóm 6: Quét nến M1 thiếu phút 20:59 trước giờ nghỉ NY:
   - Xử lý nến H1 chỉ có 59 phút M1 mà không gây lỗi runtime.
7. Nhóm 7: Bất biến tiền tố (Prefix Invariance Test):
   - Cắt tại nhiều điểm (trước/sau cấu trúc, sau sweep, sát biên 24 hàng) -> kết quả trước điểm cắt khớp 100%.
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

from src.backtest.smc.signals import (
    HIGH_POOL_TYPES,
    LOW_POOL_TYPES,
    SweepItem,
    find_and_group_sweeps,
    generate_smc_signals,
    get_friction_year,
    is_in_nominal_ny_break,
    scan_position_exit_m1,
)


def make_dummy_h1_bar(t: pd.Timestamp, op: float, hi: float, lo: float, cl: float, atr: float = 10.0) -> dict:
    return {
        "time_utc": t,
        "open": op,
        "high": hi,
        "low": lo,
        "close": cl,
        "tick_volume": 1000,
        "spread": 30.0,
        "n_m1": 60,
        "atr_14": atr,
    }


def make_mock_spreads_cfg():
    return {
        "spread_multiplier": 1.25,
        "slip_points": 5.0,
        "point_value_usd": 0.01,
        "yearly_median_spreads_points": {
            "2024": 31.0,
            "2025": 34.0,
        },
    }


def make_mock_splits_cfg():
    return {
        "development": ["2018-01-01", "2025-06-30"],
        "timezone": "UTC",
    }


class TestSMCSignals(unittest.TestCase):
    def setUp(self):
        self.spreads_cfg = make_mock_spreads_cfg()
        self.splits_cfg = make_mock_splits_cfg()

    def test_01_waterfall_drop_conflict_signal(self):
        """BƯỚC 1: DROP_CONFLICT_SIGNAL khi nến c thỏa cả BUY và SELL."""
        # Tạo 30 nến H1
        base_t = pd.Timestamp("2024-05-10 00:00:00")
        times = [base_t + pd.Timedelta(hours=i) for i in range(30)]
        h1_rows = [make_dummy_h1_bar(t, 2000.0, 2010.0, 1990.0, 2000.0, atr=10.0) for t in times]
        h1_df = pd.DataFrame(h1_rows)

        # Sweep High tại s=1 (SELL), Sweep Low tại s=2 (BUY)
        sweeps_lc = pd.DataFrame([
            {
                "pool_id": 1,
                "pool_type": "SWING_HIGH",
                "event_type": "SWEPT",
                "time_candle": times[1],
                "time_available": times[2],
                "price_trigger": 2015.0,
            },
            {
                "pool_id": 2,
                "pool_type": "SWING_LOW",
                "event_type": "SWEPT",
                "time_candle": times[2],
                "time_available": times[3],
                "price_trigger": 1985.0,
            },
        ])

        # Tại c=5 có cả BEARISH_CHOCH và BULLISH_CHOCH
        struct_df = pd.DataFrame([
            {
                "structure_id": 1,
                "event_type": "BEARISH_CHOCH",
                "time_candle": times[5],
                "time_available": times[6],
            },
            {
                "structure_id": 2,
                "event_type": "BULLISH_CHOCH",
                "time_candle": times[5],
                "time_available": times[6],
            },
        ])

        # Có cả Bearish FVG và Bullish FVG tại nến 4 (i >= s+1)
        fvg_df = pd.DataFrame([
            {
                "fvg_id": 1,
                "direction": "BEARISH",
                "bar_index": 4,
                "time_candle": times[4],
                "time_available": times[5],
                "top": 2005.0,
                "bottom": 1995.0,
                "near_edge": 1995.0,
                "far_edge": 2005.0,
                "spans_gap": False,
            },
            {
                "fvg_id": 2,
                "direction": "BULLISH",
                "bar_index": 4,
                "time_candle": times[4],
                "time_available": times[5],
                "top": 2005.0,
                "bottom": 1995.0,
                "near_edge": 2005.0,
                "far_edge": 1995.0,
                "spans_gap": False,
            },
        ])
        fvg_lc = pd.DataFrame([
            {"fvg_id": 1, "event_type": "CREATED", "time_available": times[5]},
            {"fvg_id": 2, "event_type": "CREATED", "time_available": times[5]},
        ])

        signals_df, counts = generate_smc_signals(
            h1_df, sweeps_lc, struct_df, fvg_df, fvg_lc,
            spreads_cfg=self.spreads_cfg, splits_cfg=self.splits_cfg
        )

        self.assertEqual(counts["DROP_CONFLICT_SIGNAL"], 1)
        self.assertEqual(counts["ENTERED"], 0)
        self.assertTrue(signals_df.empty)

    def test_02_waterfall_drop_split_end(self):
        """BƯỚC 2: DROP_SPLIT_END khi close_time_c >= split_end."""
        # Split end là 2025-06-30 23:59:00. Tạo nến c tại 2025-06-30 23:00:00 -> close_time_c = 2025-07-01 00:00:00
        times = [
            pd.Timestamp("2025-06-30 20:00:00"),
            pd.Timestamp("2025-06-30 22:00:00"),
            pd.Timestamp("2025-06-30 23:00:00"),
            pd.Timestamp("2025-07-01 00:00:00"),
        ]
        h1_df = pd.DataFrame([make_dummy_h1_bar(t, 2000.0, 2010.0, 1990.0, 2000.0) for t in times])

        # Sweep tại s=0 (20:00)
        sweeps_lc = pd.DataFrame([{
            "pool_id": 1, "pool_type": "SWING_HIGH", "event_type": "SWEPT",
            "time_candle": times[0], "time_available": times[1], "price_trigger": 2015.0
        }])
        # Structure tại c=2 (23:00) -> close_time_c = 2025-07-01 00:00:00 >= split_end
        struct_df = pd.DataFrame([{
            "structure_id": 1, "event_type": "BEARISH_BOS",
            "time_candle": times[2], "time_available": times[3]
        }])
        # FVG tại nến 1 (22:00, i >= s+1)
        fvg_df = pd.DataFrame([{
            "fvg_id": 1, "direction": "BEARISH", "bar_index": 1,
            "time_candle": times[1], "time_available": times[2],
            "top": 2005.0, "bottom": 1995.0, "near_edge": 1995.0, "far_edge": 2005.0,
            "spans_gap": False
        }])
        fvg_lc = pd.DataFrame([{"fvg_id": 1, "event_type": "CREATED", "time_available": times[2]}])

        signals_df, counts = generate_smc_signals(
            h1_df, sweeps_lc, struct_df, fvg_df, fvg_lc,
            spreads_cfg=self.spreads_cfg, splits_cfg=self.splits_cfg
        )
        self.assertEqual(counts["DROP_SPLIT_END"], 1)
        self.assertEqual(counts["ENTERED"], 0)

    def test_03_waterfall_drop_market_closed(self):
        """BƯỚC 3: DROP_MARKET_CLOSED khi không có hàng H1 nào tại close_time_c."""
        base_t = pd.Timestamp("2024-05-10 18:00:00")
        times = [
            base_t,
            base_t + pd.Timedelta(hours=1),  # 19:00
            base_t + pd.Timedelta(hours=2),  # 20:00 (nến c đóng lúc 21:00)
            # Khuyết 21:00 do thị trường đóng cửa
            base_t + pd.Timedelta(hours=4),  # 22:00
        ]
        h1_df = pd.DataFrame([make_dummy_h1_bar(t, 2000.0, 2010.0, 1990.0, 2000.0) for t in times])

        # Sweep tại s=0 (18:00)
        sweeps_lc = pd.DataFrame([{
            "pool_id": 1, "pool_type": "SWING_HIGH", "event_type": "SWEPT",
            "time_candle": times[0], "time_available": times[1], "price_trigger": 2015.0
        }])
        # Structure tại c=2 (20:00) -> close lúc 21:00 (không có hàng H1 21:00)
        struct_df = pd.DataFrame([{
            "structure_id": 1, "event_type": "BEARISH_BOS",
            "time_candle": times[2], "time_available": times[2] + pd.Timedelta(hours=1)
        }])
        # FVG tại nến 1 (19:00)
        fvg_df = pd.DataFrame([{
            "fvg_id": 1, "direction": "BEARISH", "bar_index": 1,
            "time_candle": times[1], "time_available": times[2],
            "top": 2005.0, "bottom": 1995.0, "near_edge": 1995.0, "far_edge": 2005.0,
            "spans_gap": False
        }])
        fvg_lc = pd.DataFrame([{"fvg_id": 1, "event_type": "CREATED", "time_available": times[2]}])

        signals_df, counts = generate_smc_signals(
            h1_df, sweeps_lc, struct_df, fvg_df, fvg_lc,
            spreads_cfg=self.spreads_cfg, splits_cfg=self.splits_cfg
        )
        self.assertEqual(counts["DROP_MARKET_CLOSED"], 1)
        self.assertEqual(counts["ENTERED"], 0)

    def test_04_waterfall_drop_daily_break(self):
        """BƯỚC 4: DROP_DAILY_BREAK khi close_time_c rơi vào khung bảo trì 17:00-18:00 New York."""
        # Giả lập lịch sử 2014 có nến H1 tồn tại lúc 21:00 UTC (17:00 EDT)
        # Nến c lúc 20:00 UTC -> close lúc 21:00 UTC
        base_t = pd.Timestamp("2024-05-10 18:00:00")
        times = [
            base_t,                          # 18:00
            base_t + pd.Timedelta(hours=1),  # 19:00
            base_t + pd.Timedelta(hours=2),  # 20:00
            base_t + pd.Timedelta(hours=3),  # 21:00 (rơi vào 17:00 EDT)
            base_t + pd.Timedelta(hours=4),  # 22:00
        ]
        h1_df = pd.DataFrame([make_dummy_h1_bar(t, 2000.0, 2010.0, 1990.0, 2000.0) for t in times])

        sweeps_lc = pd.DataFrame([{
            "pool_id": 1, "pool_type": "SWING_HIGH", "event_type": "SWEPT",
            "time_candle": times[0], "time_available": times[1], "price_trigger": 2015.0
        }])
        struct_df = pd.DataFrame([{
            "structure_id": 1, "event_type": "BEARISH_BOS",
            "time_candle": times[2], "time_available": times[3]
        }])
        fvg_df = pd.DataFrame([{
            "fvg_id": 1, "direction": "BEARISH", "bar_index": 1,
            "time_candle": times[1], "time_available": times[2],
            "top": 2005.0, "bottom": 1995.0, "near_edge": 1995.0, "far_edge": 2005.0,
            "spans_gap": False
        }])
        fvg_lc = pd.DataFrame([{"fvg_id": 1, "event_type": "CREATED", "time_available": times[2]}])

        signals_df, counts = generate_smc_signals(
            h1_df, sweeps_lc, struct_df, fvg_df, fvg_lc,
            spreads_cfg=self.spreads_cfg, splits_cfg=self.splits_cfg
        )
        self.assertEqual(counts["DROP_DAILY_BREAK"], 1)
        self.assertEqual(counts["ENTERED"], 0)

    def test_05_waterfall_drop_position_busy(self):
        """BƯỚC 5: DROP_POSITION_BUSY khi đang có 1 vị thế mở chưa đóng."""
        base_t = pd.Timestamp("2024-05-10 00:00:00")
        times = [base_t + pd.Timedelta(hours=i) for i in range(20)]
        h1_df = pd.DataFrame([make_dummy_h1_bar(t, 2000.0, 2010.0, 1990.0, 2000.0, atr=5.0) for t in times])

        # Sweep 1 tại s=1 (SELL), Structure 1 tại c=3 -> Vào lệnh lúc 04:00 (times[4])
        # Sweep 2 tại s=2 (SELL), Structure 2 tại c=5 -> Thử vào lệnh lúc 06:00 (times[6])
        sweeps_lc = pd.DataFrame([
            {"pool_id": 1, "pool_type": "SWING_HIGH", "event_type": "SWEPT", "time_candle": times[1], "time_available": times[2], "price_trigger": 2020.0},
            {"pool_id": 2, "pool_type": "SWING_HIGH", "event_type": "SWEPT", "time_candle": times[2], "time_available": times[3], "price_trigger": 2025.0},
        ])
        struct_df = pd.DataFrame([
            {"structure_id": 1, "event_type": "BEARISH_BOS", "time_candle": times[3], "time_available": times[4]},
            {"structure_id": 2, "event_type": "BEARISH_BOS", "time_candle": times[5], "time_available": times[6]},
        ])
        fvg_df = pd.DataFrame([
            {"fvg_id": 1, "direction": "BEARISH", "bar_index": 2, "time_candle": times[2], "time_available": times[3], "top": 2005.0, "bottom": 1995.0, "near_edge": 1995.0, "far_edge": 2005.0, "spans_gap": False},
            {"fvg_id": 2, "direction": "BEARISH", "bar_index": 4, "time_candle": times[4], "time_available": times[5], "top": 2005.0, "bottom": 1995.0, "near_edge": 1995.0, "far_edge": 2005.0, "spans_gap": False},
        ])
        fvg_lc = pd.DataFrame([
            {"fvg_id": 1, "event_type": "CREATED", "time_available": times[3]},
            {"fvg_id": 2, "event_type": "CREATED", "time_available": times[5]},
        ])

        # Tạo M1 không chạm SL/TP để lệnh 1 giữ lâu
        m1_times = pd.date_range(times[4], periods=600, freq="1min")
        m1_df = pd.DataFrame({
            "time_utc": m1_times,
            "open": 2000.0,
            "high": 2002.0,
            "low": 1998.0,
            "close": 2000.0,
        })

        signals_df, counts = generate_smc_signals(
            h1_df, sweeps_lc, struct_df, fvg_df, fvg_lc, m1_df=m1_df,
            spreads_cfg=self.spreads_cfg, splits_cfg=self.splits_cfg
        )
        self.assertEqual(counts["ENTERED"], 1)
        self.assertEqual(counts["DROP_POSITION_BUSY"], 1)

    def test_06_waterfall_drop_invalid_rdist_floor_and_ceiling(self):
        """BƯỚC 6: DROP_INVALID_RDIST vi phạm sàn hoặc trần chi phí."""
        base_t = pd.Timestamp("2024-05-10 00:00:00")
        times = [base_t + pd.Timedelta(hours=i) for i in range(10)]

        # Sàn: R_dist < max(0.5*ATR, 12.5*friction). Giả sử ATR=50 -> cost_floor = 25.0
        # Nhưng High cực đại đoạn [s, c] chỉ 2002.0, entry 2000.0 -> R_dist ~ 2.0 < 25.0 -> vi phạm sàn
        h1_df = pd.DataFrame([make_dummy_h1_bar(t, 2000.0, 2002.0, 1998.0, 2000.0, atr=50.0) for t in times])

        sweeps_lc = pd.DataFrame([{
            "pool_id": 1, "pool_type": "SWING_HIGH", "event_type": "SWEPT",
            "time_candle": times[1], "time_available": times[2], "price_trigger": 2005.0
        }])
        struct_df = pd.DataFrame([{
            "structure_id": 1, "event_type": "BEARISH_BOS",
            "time_candle": times[3], "time_available": times[4]
        }])
        fvg_df = pd.DataFrame([{
            "fvg_id": 1, "direction": "BEARISH", "bar_index": 2,
            "time_candle": times[2], "time_available": times[3],
            "top": 2001.0, "bottom": 1999.0, "near_edge": 1999.0, "far_edge": 2001.0,
            "spans_gap": False
        }])
        fvg_lc = pd.DataFrame([{"fvg_id": 1, "event_type": "CREATED", "time_available": times[3]}])

        signals_df, counts = generate_smc_signals(
            h1_df, sweeps_lc, struct_df, fvg_df, fvg_lc,
            spreads_cfg=self.spreads_cfg, splits_cfg=self.splits_cfg
        )
        self.assertEqual(counts["DROP_INVALID_RDIST"], 1)
        self.assertEqual(counts["ENTERED"], 0)

    def test_07_waterfall_multi_violation_single_counter(self):
        """ĐA VI PHẠM: Tín hiệu vừa vi phạm POSITION_BUSY vừa vi phạm INVALID_RDIST chỉ tăng đúng POSITION_BUSY."""
        base_t = pd.Timestamp("2024-05-10 00:00:00")
        times = [base_t + pd.Timedelta(hours=i) for i in range(15)]
        h1_df = pd.DataFrame([make_dummy_h1_bar(t, 2000.0, 2002.0, 1998.0, 2000.0, atr=5.0) for t in times])

        # Lệnh 1 hợp lệ R_dist tốt (tại s=1, c=3)
        # Nến [s, c] của lệnh 1 có High=2020.0 để R_dist ~ 20 > sàn
        h1_df.loc[1:3, "high"] = 2020.0

        # Lệnh 2 (tại s=2, c=5) xuất hiện khi lệnh 1 đang mở (POSITION_BUSY) VÀ có R_dist vi phạm sàn (High=2001 -> R_dist=1 < sàn)
        sweeps_lc = pd.DataFrame([
            {"pool_id": 1, "pool_type": "SWING_HIGH", "event_type": "SWEPT", "time_candle": times[1], "time_available": times[2], "price_trigger": 2020.0},
            {"pool_id": 2, "pool_type": "SWING_HIGH", "event_type": "SWEPT", "time_candle": times[2], "time_available": times[3], "price_trigger": 2025.0},
        ])
        struct_df = pd.DataFrame([
            {"structure_id": 1, "event_type": "BEARISH_BOS", "time_candle": times[3], "time_available": times[4]},
            {"structure_id": 2, "event_type": "BEARISH_BOS", "time_candle": times[5], "time_available": times[6]},
        ])
        fvg_df = pd.DataFrame([
            {"fvg_id": 1, "direction": "BEARISH", "bar_index": 2, "time_candle": times[2], "time_available": times[3], "top": 2005.0, "bottom": 1995.0, "near_edge": 1995.0, "far_edge": 2005.0, "spans_gap": False},
            {"fvg_id": 2, "direction": "BEARISH", "bar_index": 4, "time_candle": times[4], "time_available": times[5], "top": 2005.0, "bottom": 1995.0, "near_edge": 1995.0, "far_edge": 2005.0, "spans_gap": False},
        ])
        fvg_lc = pd.DataFrame([
            {"fvg_id": 1, "event_type": "CREATED", "time_available": times[3]},
            {"fvg_id": 2, "event_type": "CREATED", "time_available": times[5]},
        ])

        m1_times = pd.date_range(times[4], periods=600, freq="1min")
        m1_df = pd.DataFrame({
            "time_utc": m1_times, "open": 2000.0, "high": 2002.0, "low": 1998.0, "close": 2000.0,
        })

        signals_df, counts = generate_smc_signals(
            h1_df, sweeps_lc, struct_df, fvg_df, fvg_lc, m1_df=m1_df,
            spreads_cfg=self.spreads_cfg, splits_cfg=self.splits_cfg
        )
        # Khẳng định: Chỉ tăng DROP_POSITION_BUSY, DROP_INVALID_RDIST không tăng!
        self.assertEqual(counts["ENTERED"], 1)
        self.assertEqual(counts["DROP_POSITION_BUSY"], 1)
        self.assertEqual(counts["DROP_INVALID_RDIST"], 0)

    def test_08_c9_amendment_fvg_timing_rule(self):
        """C9 AMENDMENT: FVG hoàn tất tại i = s bị loại; FVG hoàn tất tại i = s+1 được nhận."""
        base_t = pd.Timestamp("2024-05-10 00:00:00")
        times = [base_t + pd.Timedelta(hours=i) for i in range(10)]
        h1_df = pd.DataFrame([make_dummy_h1_bar(t, 2000.0, 2025.0, 1995.0, 2000.0, atr=5.0) for t in times])

        # Sweep tại s=2 (times[2])
        sweeps_lc = pd.DataFrame([{
            "pool_id": 1, "pool_type": "SWING_HIGH", "event_type": "SWEPT",
            "time_candle": times[2], "time_available": times[3], "price_trigger": 2025.0
        }])
        # Structure tại c=4 (times[4])
        struct_df = pd.DataFrame([{
            "structure_id": 1, "event_type": "BEARISH_BOS",
            "time_candle": times[4], "time_available": times[5]
        }])

        # Ca 1: FVG hoàn tất tại đúng i = s = 2 (nến giữa i-1 = 1 trước sweep) -> BỊ LOẠI
        fvg_df_bad = pd.DataFrame([{
            "fvg_id": 1, "direction": "BEARISH", "bar_index": 2,
            "time_candle": times[2], "time_available": times[3],
            "top": 2010.0, "bottom": 2000.0, "near_edge": 2000.0, "far_edge": 2010.0,
            "spans_gap": False
        }])
        fvg_lc_bad = pd.DataFrame([{"fvg_id": 1, "event_type": "CREATED", "time_available": times[3]}])

        sig_bad, counts_bad = generate_smc_signals(
            h1_df, sweeps_lc, struct_df, fvg_df_bad, fvg_lc_bad,
            spreads_cfg=self.spreads_cfg, splits_cfg=self.splits_cfg
        )
        self.assertEqual(counts_bad["CANDIDATES_RAW"], 0, "FVG tại i=s phải bị loại hoàn toàn theo C9.2")
        self.assertEqual(counts_bad["ENTERED"], 0)

        # Ca 2: FVG hoàn tất tại i = s+1 = 3 (nến giữa i-1 = s = 2 tại sweep) -> ĐƯỢC NHẬN
        fvg_df_good = pd.DataFrame([{
            "fvg_id": 1, "direction": "BEARISH", "bar_index": 3,
            "time_candle": times[3], "time_available": times[4],
            "top": 2010.0, "bottom": 2000.0, "near_edge": 2000.0, "far_edge": 2010.0,
            "spans_gap": False
        }])
        fvg_lc_good = pd.DataFrame([{"fvg_id": 1, "event_type": "CREATED", "time_available": times[4]}])

        sig_good, counts_good = generate_smc_signals(
            h1_df, sweeps_lc, struct_df, fvg_df_good, fvg_lc_good,
            spreads_cfg=self.spreads_cfg, splits_cfg=self.splits_cfg
        )
        self.assertEqual(counts_good["ENTERED"], 1, "FVG tại i=s+1 phải được kích hoạt thành công")

    def test_09_c6_2_sweep_remains_open_when_first_structure_lacks_fvg(self):
        """C6.2: Sweep giữ mở khi sự kiện cấu trúc đầu tiên thiếu FVG, kích hoạt ở cấu trúc thứ hai."""
        base_t = pd.Timestamp("2024-05-10 00:00:00")
        times = [base_t + pd.Timedelta(hours=i) for i in range(15)]
        h1_df = pd.DataFrame([make_dummy_h1_bar(t, 2000.0, 2025.0, 1995.0, 2000.0, atr=5.0) for t in times])

        # Sweep tại s=1
        sweeps_lc = pd.DataFrame([{
            "pool_id": 1, "pool_type": "SWING_HIGH", "event_type": "SWEPT",
            "time_candle": times[1], "time_available": times[2], "price_trigger": 2025.0
        }])

        # Có 2 sự kiện cấu trúc: c1 = 3 (lúc này chưa có FVG), c2 = 6 (đã có FVG tạo tại nến 4)
        struct_df = pd.DataFrame([
            {"structure_id": 1, "event_type": "BEARISH_BOS", "time_candle": times[3], "time_available": times[4]},
            {"structure_id": 2, "event_type": "BEARISH_CHOCH", "time_candle": times[6], "time_available": times[7]},
        ])

        # FVG xuất hiện tại nến 4 (khả dụng lúc đóng nến 4 = times[5], sau c1 nhưng trước c2)
        fvg_df = pd.DataFrame([{
            "fvg_id": 1, "direction": "BEARISH", "bar_index": 4,
            "time_candle": times[4], "time_available": times[5],
            "top": 2010.0, "bottom": 2000.0, "near_edge": 2000.0, "far_edge": 2010.0,
            "spans_gap": False
        }])
        fvg_lc = pd.DataFrame([{"fvg_id": 1, "event_type": "CREATED", "time_available": times[5]}])

        sig_df, counts = generate_smc_signals(
            h1_df, sweeps_lc, struct_df, fvg_df, fvg_lc,
            spreads_cfg=self.spreads_cfg, splits_cfg=self.splits_cfg
        )
        # Khẳng định lệnh được vào thành công tại c2=6
        self.assertEqual(counts["ENTERED"], 1)
        self.assertEqual(sig_df["signal_row_idx"].iloc[0], 6)
        self.assertEqual(sig_df["sweep_row_idx"].iloc[0], 1)

    def test_10_c6_3_unconditional_sweep_consumption_on_drop(self):
        """C6.3: Tiêu thụ sweep vô điều kiện khi tín hiệu bị hủy (không mở lại ở nến sau)."""
        base_t = pd.Timestamp("2024-05-10 00:00:00")
        times = [base_t + pd.Timedelta(hours=i) for i in range(15)]
        h1_df = pd.DataFrame([make_dummy_h1_bar(t, 2000.0, 2002.0, 1998.0, 2000.0, atr=50.0) for t in times])

        # Sweep tại s=1
        sweeps_lc = pd.DataFrame([{
            "pool_id": 1, "pool_type": "SWING_HIGH", "event_type": "SWEPT",
            "time_candle": times[1], "time_available": times[2], "price_trigger": 2025.0
        }])

        # Có 2 cấu trúc liên tiếp tại c1=3 và c2=4
        struct_df = pd.DataFrame([
            {"structure_id": 1, "event_type": "BEARISH_BOS", "time_candle": times[3], "time_available": times[4]},
            {"structure_id": 2, "event_type": "BEARISH_CHOCH", "time_candle": times[4], "time_available": times[5]},
        ])
        fvg_df = pd.DataFrame([{
            "fvg_id": 1, "direction": "BEARISH", "bar_index": 2,
            "time_candle": times[2], "time_available": times[3],
            "top": 2001.0, "bottom": 1999.0, "near_edge": 1999.0, "far_edge": 2001.0,
            "spans_gap": False
        }])
        fvg_lc = pd.DataFrame([{"fvg_id": 1, "event_type": "CREATED", "time_available": times[3]}])

        # Tại c1=3: tín hiệu bị hủy do DROP_INVALID_RDIST (ATR=50, High chỉ 2002 -> r_dist nhỏ)
        # Theo C6.3: sweep s=1 bị tiêu thụ ngay lập tức -> tại c2=4 không được sinh tín hiệu thứ hai!
        sig_df, counts = generate_smc_signals(
            h1_df, sweeps_lc, struct_df, fvg_df, fvg_lc,
            spreads_cfg=self.spreads_cfg, splits_cfg=self.splits_cfg
        )
        self.assertEqual(counts["DROP_INVALID_RDIST"], 1)
        self.assertEqual(counts["CANDIDATES_RAW"], 1, "Chỉ được sinh đúng 1 candidate tại c1, không sinh thêm tại c2")
        self.assertEqual(counts["ENTERED"], 0)

    def test_11_window_boundary_edge_cases_and_weekend_gap(self):
        """Ca biên cửa sổ 24 hàng H1: c = s + 24 hợp lệ, c = s + 25 hết hạn, và 24 hàng vắt qua cuối tuần."""
        # Tạo 35 nến H1 vắt qua cuối tuần (Thứ Sáu 20:00 -> Thứ Hai 00:00 cách nhau 52h nhưng là 2 hàng liên tiếp)
        friday_bars = [pd.Timestamp("2024-05-10 00:00:00") + pd.Timedelta(hours=i) for i in range(21)]
        monday_bars = [pd.Timestamp("2024-05-13 00:00:00") + pd.Timedelta(hours=i) for i in range(15)]
        times = friday_bars + monday_bars  # Tổng 36 hàng
        h1_df = pd.DataFrame([make_dummy_h1_bar(t, 2000.0, 2025.0, 1995.0, 2000.0, atr=5.0) for t in times])

        # Sweep s = 2 (Thứ Sáu 02:00)
        sweeps_lc = pd.DataFrame([{
            "pool_id": 1, "pool_type": "SWING_HIGH", "event_type": "SWEPT",
            "time_candle": times[2], "time_available": times[3], "price_trigger": 2025.0
        }])

        # Ca A: c = s + 24 = 26 (rơi vào Thứ Hai nến thứ 6) -> VẪN CÒN TRONG CỬA SỔ 24 HÀNG
        struct_df_24 = pd.DataFrame([{
            "structure_id": 1, "event_type": "BEARISH_BOS",
            "time_candle": times[26], "time_available": times[27]
        }])
        fvg_df_24 = pd.DataFrame([{
            "fvg_id": 1, "direction": "BEARISH", "bar_index": 3,
            "time_candle": times[3], "time_available": times[4],
            "top": 2010.0, "bottom": 2000.0, "near_edge": 2000.0, "far_edge": 2010.0,
            "spans_gap": False
        }])
        fvg_lc_24 = pd.DataFrame([{"fvg_id": 1, "event_type": "CREATED", "time_available": times[4]}])

        sig_24, counts_24 = generate_smc_signals(
            h1_df, sweeps_lc, struct_df_24, fvg_df_24, fvg_lc_24,
            spreads_cfg=self.spreads_cfg, splits_cfg=self.splits_cfg
        )
        self.assertEqual(counts_24["ENTERED"], 1, "c = s + 24 hàng H1 phải được nhận hợp lệ kể cả khi vắt qua cuối tuần")

        # Ca B: c = s + 25 = 27 -> ĐÃ QUÁ 24 HÀNG H1 -> EXPIRED
        struct_df_25 = pd.DataFrame([{
            "structure_id": 1, "event_type": "BEARISH_BOS",
            "time_candle": times[27], "time_available": times[28]
        }])
        sig_25, counts_25 = generate_smc_signals(
            h1_df, sweeps_lc, struct_df_25, fvg_df_24, fvg_lc_24,
            spreads_cfg=self.spreads_cfg, splits_cfg=self.splits_cfg
        )
        self.assertEqual(counts_25["ENTERED"], 0, "c = s + 25 hàng H1 phải bị hết hạn cửa sổ")

    def test_12_m1_missing_minute_59_before_break(self):
        """Nến H1 20:00 chỉ có 59 nến M1 (thiếu phút :59 trước giờ nghỉ NY): quét SL/TP chạy an toàn không lỗi."""
        t_entry = pd.Timestamp("2024-05-10 18:00:00")
        t_max_exit = pd.Timestamp("2024-05-10 21:00:00")
        split_end = pd.Timestamp("2025-06-30 23:59:00")

        # Tạo chuỗi M1 từ 18:00 đến 20:58 (khuyết phút 20:59)
        m1_times = pd.date_range("2024-05-10 18:00:00", "2024-05-10 20:58:00", freq="1min")
        m1_df = pd.DataFrame({
            "time_utc": m1_times,
            "open": 2000.0,
            "high": 2002.0,
            "low": 1998.0,
            "close": 2000.0,
        })

        # Quét thoát lệnh BUY không chạm SL/TP
        exit_time, exit_reason, exit_price = scan_position_exit_m1(
            direction="BUY",
            entry_time=t_entry,
            entry_price=2000.5,
            sl=1980.0,
            tp=2040.0,
            spread=0.30,
            slip=0.05,
            m1_df=m1_df,
            max_exit_time=t_max_exit,
            split_end=split_end,
        )
        # Khẳng định hàm thoát mượt mà ở phút cuối cùng hoặc TIME_EXIT mà không văng exception
        self.assertIn(exit_reason, ["TIME_EXIT", "DATA_END"])

    def test_13_prefix_invariance_multi_cutpoints(self):
        """BẤT BIẾN TIỀN TỐ (Prefix Invariance Test): Cắt tại nhiều điểm khác nhau không làm đổi kết quả quá khứ."""
        base_t = pd.Timestamp("2024-05-10 00:00:00")
        times = [base_t + pd.Timedelta(hours=i) for i in range(70)]
        h1_df_full = pd.DataFrame([make_dummy_h1_bar(t, 2000.0, 2025.0, 1995.0, 2000.0, atr=5.0) for t in times])

        # Sweep 1 tại s=3 (c1=6 -> vị thế giữ tối đa 24 hàng [7..30], đóng tại 31)
        # Sweep 2 tại s=32 (c2=35 -> sau khi vị thế 1 đã đóng)
        sweeps_lc = pd.DataFrame([
            {"pool_id": 1, "pool_type": "SWING_HIGH", "event_type": "SWEPT", "time_candle": times[3], "time_available": times[4], "price_trigger": 2025.0},
            {"pool_id": 2, "pool_type": "SWING_HIGH", "event_type": "SWEPT", "time_candle": times[32], "time_available": times[33], "price_trigger": 2025.0},
        ])
        # Structure tại c1=6 và c2=35
        struct_df = pd.DataFrame([
            {"structure_id": 1, "event_type": "BEARISH_BOS", "time_candle": times[6], "time_available": times[7]},
            {"structure_id": 2, "event_type": "BEARISH_BOS", "time_candle": times[35], "time_available": times[36]},
        ])
        fvg_df = pd.DataFrame([
            {"fvg_id": 1, "direction": "BEARISH", "bar_index": 4, "time_candle": times[4], "time_available": times[5], "top": 2010.0, "bottom": 2000.0, "near_edge": 2000.0, "far_edge": 2010.0, "spans_gap": False},
            {"fvg_id": 2, "direction": "BEARISH", "bar_index": 33, "time_candle": times[33], "time_available": times[34], "top": 2010.0, "bottom": 2000.0, "near_edge": 2000.0, "far_edge": 2010.0, "spans_gap": False},
        ])
        fvg_lc = pd.DataFrame([
            {"fvg_id": 1, "event_type": "CREATED", "time_available": times[5]},
            {"fvg_id": 2, "event_type": "CREATED", "time_available": times[34]},
        ])

        # Chạy full
        sig_full, _ = generate_smc_signals(
            h1_df_full, sweeps_lc, struct_df, fvg_df, fvg_lc,
            spreads_cfg=self.spreads_cfg, splits_cfg=self.splits_cfg
        )
        self.assertGreaterEqual(len(sig_full), 2)

        # Cắt tại các điểm khác nhau:
        # 1. Ngay sau sweep 1 (cắt tại hàng 5) -> chưa có tín hiệu nào
        # 2. Ngay trước cấu trúc 1 (cắt tại hàng 6) -> chưa có cấu trúc
        # 3. Tại nến cấu trúc 1 (cắt tại hàng 7: c=6 là nến cuối, chưa có nến c+1=7 để vào lệnh -> DROP_MARKET_CLOSED)
        # 4. Ngay sau cấu trúc 1 có nến vào lệnh (cắt tại hàng 8: nến 7 đã mở) -> khớp đúng tín hiệu 1
        # 5. Giữa vị thế 1 (cắt tại hàng 10) -> khớp đúng tín hiệu 1
        # 6. Sát biên 24 hàng của sweep 1 (cắt tại hàng 27) -> khớp đúng tín hiệu 1
        # 7. Ngay sau sweep 2 (cắt tại hàng 33) -> vẫn chỉ có tín hiệu 1
        # 8. Ngay trước cấu trúc 2 (cắt tại hàng 35) -> vẫn chỉ có tín hiệu 1
        # 9. Ngay sau cấu trúc 2 có nến vào lệnh (cắt tại hàng 37: nến 36 đã mở) -> có cả tín hiệu 1 và 2
        # 10. Thoải mái về sau (cắt tại hàng 50) -> có cả tín hiệu 1 và 2
        cut_points = [5, 6, 7, 8, 10, 27, 33, 35, 37, 50]
        for cut in cut_points:
            sub_h1 = h1_df_full.iloc[:cut].copy()
            sub_sweeps = sweeps_lc[sweeps_lc["time_candle"] <= sub_h1["time_utc"].iloc[-1]].copy()
            sub_struct = struct_df[struct_df["time_candle"] <= sub_h1["time_utc"].iloc[-1]].copy()
            sub_fvg = fvg_df[fvg_df["time_candle"] <= sub_h1["time_utc"].iloc[-1]].copy()
            sub_lc = fvg_lc[fvg_lc["time_available"] <= sub_h1["time_utc"].iloc[-1] + pd.Timedelta(hours=1)].copy()

            sig_sub, _ = generate_smc_signals(
                sub_h1, sub_sweeps, sub_struct, sub_fvg, sub_lc,
                spreads_cfg=self.spreads_cfg, splits_cfg=self.splits_cfg
            )

            # Lấy các tín hiệu trong sig_full mà nến vào lệnh (signal_row_idx + 1) nằm trong tập cắt (< cut)
            expected = sig_full[sig_full["signal_row_idx"] + 1 < cut].reset_index(drop=True)
            self.assertEqual(len(sig_sub), len(expected), f"Lệch số lượng tín hiệu tại cut_point={cut}")

            if not expected.empty:
                for col in ["direction", "candle_c_time", "close_time_c", "sweep_s_time", "entry_open", "sl", "tp", "r_dist"]:
                    if col in ["entry_open", "sl", "tp", "r_dist"]:
                        self.assertTrue(np.allclose(sig_sub[col].values, expected[col].values))
                    else:
                        self.assertTrue((sig_sub[col].values == expected[col].values).all())
    def test_14_exit_reasons_hand_calculated(self):
        """VÁ LỖ HỔNG TEST THOÁT LỆNH: Kiểm tra tính tay chuẩn xác từng lý do thoát lệnh, công thức giá và ưu tiên SL > TP."""
        t_entry = pd.Timestamp("2024-05-10 10:00:00")
        t_split_end = pd.Timestamp("2024-05-10 23:59:00")
        t_max_exit = pd.Timestamp("2024-05-11 10:00:00")
        spread = 0.30
        slip = 0.05

        # 1. Lệnh BUY: Entry = 2000.35, SL = 1990.0, TP = 2020.0
        # a) Chạm SL thông thường trong nến
        m1_buy_sl = pd.DataFrame([{
            "time_utc": t_entry, "open": 2000.0, "high": 2005.0, "low": 1989.0, "close": 1992.0
        }])
        t_ex, reason, p_ex = scan_position_exit_m1(
            "BUY", t_entry, 2000.35, 1990.0, 2020.0, spread, slip, m1_buy_sl, t_max_exit, t_split_end
        )
        self.assertEqual(reason, "SL")
        self.assertAlmostEqual(p_ex, 1990.0 - 0.05)  # 1989.95

        # b) Chạm SL_GAP (Open nhảy gap vượt SL)
        m1_buy_sl_gap = pd.DataFrame([{
            "time_utc": t_entry, "open": 1988.0, "high": 1995.0, "low": 1987.0, "close": 1992.0
        }])
        t_ex, reason, p_ex = scan_position_exit_m1(
            "BUY", t_entry, 2000.35, 1990.0, 2020.0, spread, slip, m1_buy_sl_gap, t_max_exit, t_split_end
        )
        self.assertEqual(reason, "SL_GAP")
        self.assertAlmostEqual(p_ex, 1988.0 - 0.05)  # 1987.95

        # c) Chạm TP thông thường trong nến
        m1_buy_tp = pd.DataFrame([{
            "time_utc": t_entry, "open": 2000.0, "high": 2025.0, "low": 1998.0, "close": 2022.0
        }])
        t_ex, reason, p_ex = scan_position_exit_m1(
            "BUY", t_entry, 2000.35, 1990.0, 2020.0, spread, slip, m1_buy_tp, t_max_exit, t_split_end
        )
        self.assertEqual(reason, "TP")
        self.assertAlmostEqual(p_ex, 2020.0 - 0.05)  # 2019.95 (TP - slip)

        # d) Chạm cả SL và TP trong cùng một nến M1 -> Bắt buộc ưu tiên SL (C5.2)
        m1_buy_both = pd.DataFrame([{
            "time_utc": t_entry, "open": 2000.0, "high": 2025.0, "low": 1985.0, "close": 2000.0
        }])
        t_ex, reason, p_ex = scan_position_exit_m1(
            "BUY", t_entry, 2000.35, 1990.0, 2020.0, spread, slip, m1_buy_both, t_max_exit, t_split_end
        )
        self.assertEqual(reason, "SL")
        self.assertAlmostEqual(p_ex, 1990.0 - 0.05)  # 1989.95

        # 2. Lệnh SELL: Entry = 1999.95, SL = 2010.0, TP = 1980.0
        # a) Chạm SL thông thường (High + spread >= SL)
        m1_sell_sl = pd.DataFrame([{
            "time_utc": t_entry, "open": 2000.0, "high": 2009.8, "low": 1995.0, "close": 1998.0
        }])
        # High = 2009.8 + spread 0.30 = 2010.1 >= 2010.0 -> SL hit!
        t_ex, reason, p_ex = scan_position_exit_m1(
            "SELL", t_entry, 1999.95, 2010.0, 1980.0, spread, slip, m1_sell_sl, t_max_exit, t_split_end
        )
        self.assertEqual(reason, "SL")
        self.assertAlmostEqual(p_ex, 2010.0 + 0.05)  # 2010.05 (SL + slip)

        # b) Chạm SL_GAP (Open + spread >= SL)
        m1_sell_sl_gap = pd.DataFrame([{
            "time_utc": t_entry, "open": 2009.8, "high": 2015.0, "low": 2008.0, "close": 2012.0
        }])
        # Open = 2009.8 + 0.30 = 2010.1 >= 2010.0 -> SL_GAP
        t_ex, reason, p_ex = scan_position_exit_m1(
            "SELL", t_entry, 1999.95, 2010.0, 1980.0, spread, slip, m1_sell_sl_gap, t_max_exit, t_split_end
        )
        self.assertEqual(reason, "SL_GAP")
        self.assertAlmostEqual(p_ex, 2009.8 + 0.30 + 0.05)  # 2010.15

        # c) Chạm TP thông thường (Low + spread <= TP)
        m1_sell_tp = pd.DataFrame([{
            "time_utc": t_entry, "open": 2000.0, "high": 2002.0, "low": 1979.5, "close": 1981.0
        }])
        # Low = 1979.5 + 0.30 = 1979.8 <= 1980.0 -> TP hit!
        t_ex, reason, p_ex = scan_position_exit_m1(
            "SELL", t_entry, 1999.95, 2010.0, 1980.0, spread, slip, m1_sell_tp, t_max_exit, t_split_end
        )
        self.assertEqual(reason, "TP")
        self.assertAlmostEqual(p_ex, 1980.0 + 0.05)  # 1980.05 (TP + slip)

        # d) Chạm cả SL và TP cho SELL -> Bắt buộc ưu tiên SL
        m1_sell_both = pd.DataFrame([{
            "time_utc": t_entry, "open": 2000.0, "high": 2015.0, "low": 1975.0, "close": 2000.0
        }])
        t_ex, reason, p_ex = scan_position_exit_m1(
            "SELL", t_entry, 1999.95, 2010.0, 1980.0, spread, slip, m1_sell_both, t_max_exit, t_split_end
        )
        self.assertEqual(reason, "SL")
        self.assertAlmostEqual(p_ex, 2010.0 + 0.05)  # 2010.05

        # 3. DATA_END tại split_end
        # a) BUY: thoát tại Close - slip
        m1_buy_data_end = pd.DataFrame([{
            "time_utc": t_split_end, "open": 2000.0, "high": 2005.0, "low": 1995.0, "close": 2002.0
        }])
        t_ex, reason, p_ex = scan_position_exit_m1(
            "BUY", t_split_end, 2000.0, 1980.0, 2030.0, spread, slip, m1_buy_data_end, t_max_exit, t_split_end
        )
        self.assertEqual(reason, "DATA_END")
        self.assertAlmostEqual(p_ex, 2002.0 - 0.05)  # 2001.95

        # b) SELL: thoát tại Close + spread + slip
        m1_sell_data_end = pd.DataFrame([{
            "time_utc": t_split_end, "open": 2000.0, "high": 2005.0, "low": 1995.0, "close": 2002.0
        }])
        t_ex, reason, p_ex = scan_position_exit_m1(
            "SELL", t_split_end, 2000.0, 2020.0, 1970.0, spread, slip, m1_sell_data_end, t_max_exit, t_split_end
        )
        self.assertEqual(reason, "DATA_END")
        self.assertAlmostEqual(p_ex, 2002.0 + 0.30 + 0.05)  # 2002.35

    def test_15_exit_sl_gap_priority_over_time_exit(self):
        """KIỂM TRA CHỐT CHẶN: SL_GAP không bị nhãn TIME_EXIT che mất tại mốc max_exit_time."""
        t_entry = pd.Timestamp("2024-05-10 10:00:00")
        t_max_exit = pd.Timestamp("2024-05-11 10:00:00")
        t_split_end = pd.Timestamp("2025-06-30 23:59:00")
        spread = 0.30
        slip = 0.05

        # Nến M1 tại đúng max_exit_time
        # Ca 1: Open bình thường (op > SL) -> TIME_EXIT
        m1_normal = pd.DataFrame([{
            "time_utc": t_max_exit, "open": 2000.0, "high": 2005.0, "low": 1995.0, "close": 2000.0
        }])
        _, reason_norm, p_norm = scan_position_exit_m1(
            "BUY", t_entry, 2000.0, 1990.0, 2020.0, spread, slip, m1_normal, t_max_exit, t_split_end
        )
        self.assertEqual(reason_norm, "TIME_EXIT")
        self.assertAlmostEqual(p_norm, 2000.0 - 0.05)

        # Ca 2: Open nhảy gap dưới SL (op <= SL) -> BẮT BUỘC trả SL_GAP chứ không được trả TIME_EXIT
        m1_gap = pd.DataFrame([{
            "time_utc": t_max_exit, "open": 1985.0, "high": 1990.0, "low": 1980.0, "close": 1988.0
        }])
        _, reason_gap, p_gap = scan_position_exit_m1(
            "BUY", t_entry, 2000.0, 1990.0, 2020.0, spread, slip, m1_gap, t_max_exit, t_split_end
        )
        self.assertEqual(reason_gap, "SL_GAP")
        self.assertAlmostEqual(p_gap, 1985.0 - 0.05)

        # Ca 3: SELL với Open nhảy gap qua SL (op + spread >= SL) tại max_exit_time -> BẮT BUỘC SL_GAP
        m1_sell_gap = pd.DataFrame([{
            "time_utc": t_max_exit, "open": 2015.0, "high": 2020.0, "low": 2010.0, "close": 2012.0
        }])
        _, reason_sell_gap, p_sell_gap = scan_position_exit_m1(
            "SELL", t_entry, 2000.0, 2010.0, 1980.0, spread, slip, m1_sell_gap, t_max_exit, t_split_end
        )
        self.assertEqual(reason_sell_gap, "SL_GAP")
        self.assertAlmostEqual(p_sell_gap, 2015.0 + 0.30 + 0.05)


if __name__ == "__main__":
    unittest.main()
