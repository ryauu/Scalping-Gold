"""Mô-đun structure.py - Phát hiện Cấu trúc thị trường SMC (BOS & CHoCH).

Định nghĩa toán học và quy tắc vận hành (theo SMC_SPEC.md mục 3):
1. Trạng thái cấu trúc thị trường (Market Trend State):
   - 'NEUTRAL': Trạng thái khởi tạo ban đầu khi chưa ghi nhận bất kỳ sự kiện phá vỡ nào.
   - 'BULLISH': Cấu trúc tăng giá (sau khi có Bullish BOS hoặc Bullish CHoCH).
   - 'BEARISH': Cấu trúc giảm giá (sau khi có Bearish BOS hoặc Bearish CHoCH).

2. Mức đỉnh/đáy được bảo vệ (Protected Level - 0 bậc tự do):
   - Protected High: Swing High chưa bị phá gần nhất (last unbroken Swing High) có time_available <= open_time[t].
   - Protected Low: Swing Low chưa bị phá gần nhất (last unbroken Swing Low) có time_available <= open_time[t].

3. Phân định rạch ròi giữa BOS và CHoCH:
   - BOS: Phá vỡ CÙNG CHIỀU với xu hướng hiện tại (hoặc xác lập xu hướng đầu tiên từ NEUTRAL).
   - CHoCH: Phá vỡ NGƯỢC CHIỀU với xu hướng hiện tại (lật trạng thái xu hướng).

4. Quy tắc "Chỉ phá một lần" (First Break Only) & Xóa bỏ Swing bị che khuất (Shadowed Swings):
   - Khi một Swing High bị phá (Close[t] > P): Mức đó và MỌI Swing High chưa bị phá có giá <= max(P, Close[t])
     đều lập tức chuyển sang trạng thái BROKEN.
   - Khi một Swing Low bị phá (Close[t] < P): Mức đó và MỌI Swing Low chưa bị phá có giá >= min(P, Close[t])
     đều lập tức chuyển sang trạng thái BROKEN.

5. Thời điểm khả dụng (time_available):
   - time_available của sự kiện cấu trúc = close_time[t] = open_time[t] + bar_duration.
"""

from __future__ import annotations

import logging
from typing import Sequence

import numpy as np
import pandas as pd

from .swings import DEFAULT_H1_DURATION, DEFAULT_K, detect_swings

logger = logging.getLogger(__name__)


def detect_structure(
    df: pd.DataFrame,
    swings_df: pd.DataFrame | None = None,
    k: int = DEFAULT_K,
    bar_duration: pd.Timedelta = DEFAULT_H1_DURATION,
) -> pd.DataFrame:
    """Phát hiện toàn bộ các sự kiện BOS và CHoCH trên chuỗi nến H1.

    Tham số:
        df: DataFrame chứa các nến H1 với tối thiểu các cột 'time_utc', 'close', 'high', 'low'.
        swings_df: DataFrame đầu ra từ detect_swings (nếu None, tự động gọi detect_swings(df, k=k)).
        k: Tham số k dùng để phát hiện swing nếu swings_df chưa được cung cấp.
        bar_duration: Độ dài nến để tính time_available (mặc định 1 giờ đối với H1).

    Trả về:
        DataFrame chứa các sự kiện cấu trúc với các cột:
        - structure_id: int (tăng dần từ 0)
        - event_type: str ('BULLISH_BOS', 'BEARISH_BOS', 'BULLISH_CHOCH', 'BEARISH_CHOCH')
        - bar_index: int (chỉ số nến t phá vỡ cấu trúc)
        - time_candle: pd.Timestamp (giờ mở nến t)
        - time_available: pd.Timestamp (giờ đóng nến t: time_utc[t] + bar_duration)
        - price_broken: float (mức giá swing bị phá vỡ)
        - broken_swing_id: int (định danh swing_id của mức swing chính bị phá)
        - broken_swing_bar_index: int (chỉ số nến tạo ra swing bị phá)
        - close_price: float (giá đóng cửa của nến t)
        - prior_trend: str ('NEUTRAL', 'BULLISH', 'BEARISH')
        - new_trend: str ('BULLISH', 'BEARISH')
    """
    columns_out = [
        "structure_id",
        "event_type",
        "bar_index",
        "time_candle",
        "time_available",
        "price_broken",
        "broken_swing_id",
        "broken_swing_bar_index",
        "close_price",
        "prior_trend",
        "new_trend",
    ]

    if df.empty or len(df) == 0:
        return pd.DataFrame(columns=columns_out)

    required_cols = {"time_utc", "close", "high", "low"}
    if not required_cols.issubset(df.columns):
        raise KeyError(f"DataFrame cần có đủ các cột: {required_cols}")

    # Bảo vệ chống NaN
    if df["time_utc"].isna().any():
        raise ValueError("Cột 'time_utc' chứa giá trị NaN/NaT.")
    if df["close"].isna().any() or df["high"].isna().any() or df["low"].isna().any():
        raise ValueError("Cột giá ('close', 'high', hoặc 'low') chứa giá trị NaN.")

    sorted_df = df.sort_values("time_utc").reset_index(drop=True)
    n_bars = len(sorted_df)

    # Phát hiện swings nếu chưa có
    if swings_df is None or swings_df.empty:
        swings = detect_swings(sorted_df, k=k, bar_duration=bar_duration)
    else:
        swings = swings_df.copy()

    if swings.empty:
        return pd.DataFrame(columns=columns_out)

    # Đảm bảo sắp xếp swings theo time_available và bar_index
    swings = swings.sort_values(["time_available", "bar_index"]).reset_index(drop=True)

    times = pd.to_datetime(sorted_df["time_utc"]).to_numpy()
    closes = sorted_df["close"].to_numpy(dtype=np.float64)

    # Danh sách các swing chưa bị phá: lưu trữ dict
    # Mỗi phần tử: {'swing_id': int, 'price': float, 'bar_index': int, 'time_available': pd.Timestamp}
    unbroken_highs: list[dict[str, object]] = []
    unbroken_lows: list[dict[str, object]] = []

    trend_state = "NEUTRAL"
    records: list[dict[str, object]] = []

    # Con trỏ duyệt swings
    swing_idx = 0
    n_swings = len(swings)

    for t in range(n_bars):
        t_open = pd.Timestamp(times[t])
        t_close = t_open + bar_duration
        close_t = closes[t]

        # Nạp tất cả các swing có time_available <= t_open (không look-ahead)
        while swing_idx < n_swings:
            sw_avail = pd.Timestamp(swings.iloc[swing_idx]["time_available"])
            if sw_avail <= t_open:
                sw_row = swings.iloc[swing_idx]
                sw_item = {
                    "swing_id": int(sw_row["swing_id"]),
                    "price": float(sw_row["price"]),
                    "bar_index": int(sw_row["bar_index"]),
                    "time_available": sw_avail,
                }
                if sw_row["swing_type"] == "HIGH":
                    unbroken_highs.append(sw_item)
                elif sw_row["swing_type"] == "LOW":
                    unbroken_lows.append(sw_item)
                swing_idx += 1
            else:
                break

        # Xác định mức được bảo vệ (Protected Level - phần tử cuối cùng trong danh sách chưa bị phá)
        protected_high = unbroken_highs[-1] if unbroken_highs else None
        protected_low = unbroken_lows[-1] if unbroken_lows else None

        event_type: str | None = None
        broken_target: dict[str, object] | None = None
        prior_trend = trend_state
        new_trend = trend_state

        # Phân định sự kiện theo ma trận trạng thái xu hướng (SMC_SPEC.md mục 3.2)
        if trend_state == "BULLISH":
            # 1. Phá đỉnh được bảo vệ -> Tiếp diễn tăng (BULLISH_BOS)
            if protected_high is not None and close_t > float(protected_high["price"]):
                event_type = "BULLISH_BOS"
                broken_target = protected_high
                new_trend = "BULLISH"
            # 2. Phá đáy được bảo vệ -> Đảo chiều giảm (BEARISH_CHOCH)
            elif protected_low is not None and close_t < float(protected_low["price"]):
                event_type = "BEARISH_CHOCH"
                broken_target = protected_low
                new_trend = "BEARISH"
                trend_state = "BEARISH"

        elif trend_state == "BEARISH":
            # 1. Phá đáy được bảo vệ -> Tiếp diễn giảm (BEARISH_BOS)
            if protected_low is not None and close_t < float(protected_low["price"]):
                event_type = "BEARISH_BOS"
                broken_target = protected_low
                new_trend = "BEARISH"
            # 2. Phá đỉnh được bảo vệ -> Đảo chiều tăng (BULLISH_CHOCH)
            elif protected_high is not None and close_t > float(protected_high["price"]):
                event_type = "BULLISH_CHOCH"
                broken_target = protected_high
                new_trend = "BULLISH"
                trend_state = "BULLISH"

        elif trend_state == "NEUTRAL":
            # Khi NEUTRAL: Phá đỉnh -> Khởi tạo BULLISH_BOS; Phá đáy -> Khởi tạo BEARISH_BOS
            # Tuyệt đối không sinh CHoCH khi đang ở NEUTRAL
            if protected_high is not None and close_t > float(protected_high["price"]):
                event_type = "BULLISH_BOS"
                broken_target = protected_high
                new_trend = "BULLISH"
                trend_state = "BULLISH"
            elif protected_low is not None and close_t < float(protected_low["price"]):
                event_type = "BEARISH_BOS"
                broken_target = protected_low
                new_trend = "BEARISH"
                trend_state = "BEARISH"

        # Nếu có sự kiện kích hoạt
        if event_type is not None and broken_target is not None:
            p_broken = float(broken_target["price"])

            records.append({
                "structure_id": len(records),
                "event_type": event_type,
                "bar_index": int(t),
                "time_candle": t_open,
                "time_available": t_close,
                "price_broken": p_broken,
                "broken_swing_id": int(broken_target["swing_id"]),
                "broken_swing_bar_index": int(broken_target["bar_index"]),
                "close_price": float(close_t),
                "prior_trend": prior_trend,
                "new_trend": new_trend,
            })

            # Quy tắc First Break & Vô hiệu hóa Swing cũ bị che khuất (Shadowed Swings Elimination):
            # Nếu phá High: Toàn bộ Swing High chưa bị phá có giá <= max(P, close_t) chuyển sang BROKEN
            if "BULLISH" in event_type or event_type == "BULLISH_CHOCH":
                threshold = max(p_broken, close_t)
                unbroken_highs = [h for h in unbroken_highs if float(h["price"]) > threshold]

            # Nếu phá Low: Toàn bộ Swing Low chưa bị phá có giá >= min(P, close_t) chuyển sang BROKEN
            if "BEARISH" in event_type or event_type == "BEARISH_CHOCH":
                threshold = min(p_broken, close_t)
                unbroken_lows = [l for l in unbroken_lows if float(l["price"]) < threshold]

    if not records:
        return pd.DataFrame(columns=columns_out)

    structure_df = pd.DataFrame(records)
    structure_df = structure_df.sort_values(["time_available", "bar_index"]).reset_index(drop=True)
    structure_df["structure_id"] = structure_df.index
    return structure_df[columns_out]


def get_structure_available_at(
    structure_df: pd.DataFrame,
    current_time: pd.Timestamp | str,
    event_type: str | None = None,
) -> pd.DataFrame:
    """Truy xuất các sự kiện cấu trúc đã hoàn tất và khả dụng tại current_time (chống look-ahead).

    Tham số:
        structure_df: DataFrame đầu ra từ detect_structure.
        current_time: Thời điểm hiện tại cần lọc.
        event_type: Lọc theo loại sự kiện ('BULLISH_BOS', 'BEARISH_BOS', v.v.), None lấy tất cả.

    Trả về:
        DataFrame các sự kiện có time_available <= current_time.
    """
    if structure_df.empty:
        return structure_df.copy()

    ts = pd.Timestamp(current_time)
    if structure_df["time_available"].dt.tz is not None and ts.tzinfo is None:
        ts = ts.tz_localize(structure_df["time_available"].dt.tz)
    elif structure_df["time_available"].dt.tz is None and ts.tzinfo is not None:
        ts = ts.tz_localize(None)

    mask = structure_df["time_available"] <= ts
    if event_type is not None:
        mask = mask & (structure_df["event_type"] == event_type.strip().upper())

    return structure_df[mask].copy().reset_index(drop=True)
