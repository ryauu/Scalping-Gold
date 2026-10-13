"""Mô-đun signals.py - Nhận diện vùng Á và sinh tín hiệu phá vỡ (Breakout).

Theo PROTOCOL.md:
- Vùng Á: cửa sổ nửa mở [00:00, 07:00) UTC (nến 06:59 được tính, nến 07:00 thì không).
- Ngưỡng hợp lệ: n_bars >= 378 (tương đương 90% của 420 nến M1 lý thuyết, đăng ký tại PROTOCOL.md mục 9).
- Đầu ra vùng Á: mỗi ngày UTC một dòng gồm date, asia_high, asia_low, n_bars, valid.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data import M1Data

# Ngưỡng nến tối thiểu để phiên Á được coi là đủ dữ liệu hợp lệ (90% của 420 nến M1 lý thuyết)
# Đã được kiểm chứng thực nghiệm và đăng ký trước tại PROTOCOL.md mục 9
DEFAULT_MIN_ASIAN_BARS = 378


def compute_asian_range(
    m1: pd.DataFrame | M1Data,
    min_bars: int = DEFAULT_MIN_ASIAN_BARS,
    start_hour: int = 0,
    end_hour: int = 7,
    mask_invalid: bool = False,
) -> pd.DataFrame:
    """Xác định vùng giá cao/thấp phiên Á [start_hour:00, end_hour:00) UTC cho từng ngày.

    Cửa sổ thời gian:
    - Nửa mở [start_hour:00:00, end_hour:00:00):
      Ví dụ mặc định [00:00, 07:00): nến 06:59:00 được tính, nến 07:00:00 KHÔNG được tính.

    Chất lượng dữ liệu:
    - Tổng số nến lý thuyết tối đa là 420 nến M1 (7 giờ x 60 phút).
    - Ngưỡng mặc định min_bars = 378 (90% của 420 nến, đã đăng ký tại PROTOCOL.md mục 9).
    - Ngày có n_bars < min_bars sẽ có cờ valid = False.
    - Ngày cuối tuần hoặc ngày không có nến phiên Á sẽ có n_bars = 0 và valid = False.
    - Nếu mask_invalid=True, các ngày có valid=False sẽ có asia_high và asia_low đặt là NaN.

    Tham số:
        m1: Dữ liệu nến M1 (DataFrame hoặc M1Data).
        min_bars: Số nến tối thiểu để coi phiên Á là hợp lệ (mặc định 378).
        start_hour: Giờ bắt đầu tính vùng Á theo UTC (mặc định 0).
        end_hour: Giờ kết thúc tính vùng Á theo UTC (mặc định 7).
        mask_invalid: Nếu True, gán NaN cho asia_high/low khi valid=False (mặc định False).

    Trả về:
        DataFrame chứa mỗi ngày 1 dòng với các cột:
        - date: pd.Timestamp (chuẩn hóa về 00:00:00 UTC của ngày đó)
        - asia_high: float (giá cao nhất vùng Á)
        - asia_low: float (giá thấp nhất vùng Á)
        - n_bars: int (số lượng nến M1 thực tế trong cửa sổ)
        - valid: bool (True nếu đủ dữ liệu và hợp lệ)
    """
    m1_df = m1.to_dataframe() if isinstance(m1, M1Data) else m1.copy()
    columns_out = ["date", "asia_high", "asia_low", "n_bars", "valid"]

    if m1_df.empty:
        return pd.DataFrame(columns=columns_out)

    if "time_utc" not in m1_df.columns:
        raise KeyError("Dữ liệu M1 cần có cột 'time_utc'")

    time_ts = pd.to_datetime(m1_df["time_utc"])
    m1_df["time_utc"] = time_ts

    start_day = time_ts.min().floor("D")
    end_day = time_ts.max().floor("D")
    calendar_dates = pd.date_range(start_day, end_day, freq="D")

    # Lọc nến trong cửa sổ nửa mở [start_hour, end_hour)
    # Ví dụ: 0 <= hour < 7 -> nến 06:59 được tính, nến 07:00 KHÔNG được tính
    asia_mask = (time_ts.dt.hour >= start_hour) & (time_ts.dt.hour < end_hour)
    asia_bars = m1_df[asia_mask]

    if not asia_bars.empty:
        agg = (
            asia_bars.groupby(asia_bars["time_utc"].dt.floor("D"))
            .agg(
                asia_high=("high", "max"),
                asia_low=("low", "min"),
                n_bars=("high", "count"),
            )
        )
    else:
        agg = pd.DataFrame(
            {
                "asia_high": pd.Series(dtype=np.float64),
                "asia_low": pd.Series(dtype=np.float64),
                "n_bars": pd.Series(dtype=np.int64),
            }
        )

    # Đảm bảo toàn bộ các ngày từ start_day đến end_day đều có 1 dòng
    result = agg.reindex(calendar_dates)
    result.index.name = "date"
    result["n_bars"] = result["n_bars"].fillna(0).astype(np.int64)

    # Đánh giá tính hợp lệ của vùng Á
    is_valid = (
        (result["n_bars"] >= min_bars)
        & result["asia_high"].notna()
        & result["asia_low"].notna()
        & (result["asia_high"] >= result["asia_low"])
    )
    result["valid"] = is_valid

    if mask_invalid:
        result.loc[~is_valid, ["asia_high", "asia_low"]] = np.nan

    result = result.reset_index()
    return result[columns_out]

