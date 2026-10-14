"""Mô-đun swings.py - Phát hiện Swing High và Swing Low chuẩn SMC.

Định nghĩa toán học (theo SMC_SPEC.md mục 2):
- Với chuỗi nến và tham số k (PRIMARY: k=3, EXPLORATORY: k=5):
  * Swing High tại nến i: High[i] > High[i-j] và High[i] > High[i+j] với mọi j in [1, k] (lớn hơn nghiêm ngặt).
  * Swing Low tại nến i: Low[i] < Low[i-j] và Low[i] < Low[i+j] với mọi j in [1, k] (nhỏ hơn nghiêm ngặt).
- Các ca hòa (đỉnh/đáy bằng nhau trong cửa sổ k) bị loại bỏ hoàn toàn, không tạo swing đơn lẻ.
- Bảo vệ NaN: Dữ liệu đầu vào tuyệt đối không được chứa NaN để tránh lỗi so sánh <= NaN trả về False.
- Quy tắc time_available:
  * Nến i là đỉnh/đáy, nhưng chỉ được xác nhận khi nến (i+k) ĐÓNG CỬA.
  * Với nến H1 nhãn thời gian time_utc (giờ mở nến), thời điểm đóng cửa là time_utc[i+k] + bar_duration.
  * Mọi thuật toán tại thời điểm t chỉ được truy cập các swing có time_available <= t.
"""

from __future__ import annotations

import logging
from typing import Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_K: int = 3
DEFAULT_H1_DURATION: pd.Timedelta = pd.Timedelta(hours=1)


def detect_swings(
    df: pd.DataFrame,
    k: int = DEFAULT_K,
    bar_duration: pd.Timedelta = DEFAULT_H1_DURATION,
) -> pd.DataFrame:
    """Phát hiện toàn bộ các điểm Swing High và Swing Low trên chuỗi nến.

    Tham số:
        df: DataFrame chứa các cột 'time_utc', 'high', 'low' (nến đã sắp xếp theo thời gian).
        k: Số nến lân cận mỗi bên cần vượt qua nghiêm ngặt (PRIMARY: k=3, EXPLORATORY: k=5).
        bar_duration: Độ dài nến để tính thời điểm đóng nến i+k (mặc định 1 giờ đối với H1).

    Trả về:
        DataFrame chứa danh sách các swing với các cột:
        - swing_id: int (định danh tăng dần từ 0)
        - swing_type: str ('HIGH' hoặc 'LOW')
        - bar_index: int (vị trí nến tạo đỉnh/đáy)
        - time_candle: pd.Timestamp (giờ mở nến tạo đỉnh/đáy)
        - time_available: pd.Timestamp (giờ nến i+k đóng cửa - thời điểm khả dụng)
        - price: float (mức giá đỉnh/đáy)
        - k: int (tham số k đã dùng)
    """
    columns_out = [
        "swing_id",
        "swing_type",
        "bar_index",
        "time_candle",
        "time_available",
        "price",
        "k",
    ]

    # 1. Kiểm tra đủ cột trước tiên (kể cả với DataFrame rỗng)
    if not {"time_utc", "high", "low"}.issubset(df.columns):
        raise KeyError("DataFrame đầu vào cần có đủ các cột: 'time_utc', 'high', 'low'")

    if df.empty or len(df) < (2 * k + 1) or k <= 0:
        return pd.DataFrame(columns=columns_out)

    # 2. Bảo vệ chống NaN: Tuyệt đối không chấp nhận NaN để tránh so sánh <= NaN ra False
    if df["time_utc"].isna().any():
        raise ValueError("Cột 'time_utc' chứa giá trị NaN/NaT.")
    if df["high"].isna().any() or df["low"].isna().any():
        raise ValueError("Cột 'high' hoặc 'low' chứa giá trị NaN.")

    # 3. Chuẩn hóa múi giờ sang UTC naive nhất quán (nếu có tz thì convert sang UTC rồi bỏ tz)
    time_series = pd.to_datetime(df["time_utc"])
    if time_series.dt.tz is not None:
        time_series = time_series.dt.tz_convert("UTC").dt.tz_localize(None)

    sorted_df = df.copy()
    sorted_df["time_utc"] = time_series
    sorted_df = sorted_df.sort_values("time_utc", kind="stable").reset_index(drop=True)

    # 4. Kiểm tra thời gian tăng nghiêm ngặt (không trùng lặp)
    if sorted_df["time_utc"].duplicated().any():
        raise ValueError("Dữ liệu 'time_utc' chứa các mốc thời gian bị trùng lặp.")
    if not sorted_df["time_utc"].is_monotonic_increasing:
        raise ValueError("Dữ liệu 'time_utc' phải được sắp xếp tăng dần nghiêm ngặt.")

    times = sorted_df["time_utc"].tolist()
    highs = sorted_df["high"].to_numpy(dtype=np.float64)
    lows = sorted_df["low"].to_numpy(dtype=np.float64)

    assert not np.isnan(highs).any(), "Mảng highs không được chứa NaN"
    assert not np.isnan(lows).any(), "Mảng lows không được chứa NaN"

    n = len(sorted_df)
    records: list[dict[str, object]] = []

    # Quét từ chỉ số k đến n - 1 - k
    for i in range(k, n - k):
        h_i = highs[i]
        l_i = lows[i]

        # Kiểm tra Swing High: High[i] > High[i-j] và High[i] > High[i+j]
        is_high = True
        for j in range(1, k + 1):
            if h_i <= highs[i - j] or h_i <= highs[i + j]:
                is_high = False
                break

        # Kiểm tra Swing Low: Low[i] < Low[i-j] và Low[i] < Low[i+j]
        is_low = True
        for j in range(1, k + 1):
            if l_i >= lows[i - j] or l_i >= lows[i + j]:
                is_low = False
                break

        t_candle = times[i]
        t_available = times[i + k] + bar_duration

        if is_high:
            records.append({
                "swing_type": "HIGH",
                "bar_index": int(i),
                "time_candle": t_candle,
                "time_available": t_available,
                "price": float(h_i),
                "k": int(k),
            })

        if is_low:
            records.append({
                "swing_type": "LOW",
                "bar_index": int(i),
                "time_candle": t_candle,
                "time_available": t_available,
                "price": float(l_i),
                "k": int(k),
            })

    if not records:
        return pd.DataFrame(columns=columns_out)

    swings_df = pd.DataFrame(records)
    # Sắp xếp ổn định (kind='stable') với 3 khóa: time_available, bar_index, swing_type
    swings_df = swings_df.sort_values(
        ["time_available", "bar_index", "swing_type"], kind="stable"
    ).reset_index(drop=True)
    swings_df["swing_id"] = swings_df.index
    swings_df = swings_df[columns_out]

    return swings_df


def get_swings_available_at(
    swings_df: pd.DataFrame,
    current_time: pd.Timestamp | str,
    swing_type: str | None = None,
) -> pd.DataFrame:
    """Truy xuất các swing đã được xác nhận và khả dụng tại thời điểm current_time (chống look-ahead).

    Tham số:
        swings_df: DataFrame đầu ra từ detect_swings.
        current_time: Thời điểm hiện tại cần truy vấn.
        swing_type: Lọc theo 'HIGH', 'LOW', hoặc None (lấy cả hai).

    Trả về:
        DataFrame chứa các swing có time_available <= current_time.
    """
    if swings_df.empty:
        return swings_df.copy()

    # Chuẩn hóa current_time sang UTC naive nhất quán
    ts = pd.to_datetime(current_time)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)

    # Chuẩn hóa time_available trong DataFrame sang UTC naive
    avail_series = pd.to_datetime(swings_df["time_available"])
    if avail_series.dt.tz is not None:
        avail_series = avail_series.dt.tz_convert("UTC").dt.tz_localize(None)

    mask = avail_series <= ts
    if swing_type is not None:
        mask = mask & (swings_df["swing_type"] == swing_type.strip().upper())

    return swings_df[mask].copy().reset_index(drop=True)

