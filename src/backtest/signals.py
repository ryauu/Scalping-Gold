"""Mô-đun signals.py - Nhận diện vùng Á và sinh tín hiệu phá vỡ (Breakout).

Theo PROTOCOL.md:
- Vùng Á: cửa sổ nửa mở [00:00, 07:00) UTC (nến 06:59 được tính, nến 07:00 thì không).
- Ngưỡng hợp lệ: n_bars >= 378 (tương đương 90% của 420 nến M1 lý thuyết, đăng ký tại PROTOCOL.md mục 9).
- Cửa sổ vào lệnh: [07:00, 13:00) UTC. Tối đa 1 lệnh/ngày (hướng phá vỡ đầu tiên).
- Tín hiệu: nến M1 ĐÓNG CỬA vượt biên ± 0,1 × ATR(H1, 14); vào lệnh ở giá MỞ nến M1 kế tiếp.
  ATR(H1, 14) được lấy động tại thời điểm nến M1 đang xét từ nến H1 đã đóng gần nhất.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .data import M1Data, attach_h1_atr

# Ngưỡng nến tối thiểu để phiên Á được coi là đủ dữ liệu hợp lệ (90% của 420 nến M1 lý thuyết)
# Đã được kiểm chứng thực nghiệm và đăng ký trước tại PROTOCOL.md mục 9
DEFAULT_MIN_ASIAN_BARS = 378


def compute_asian_range(
    m1: pd.DataFrame | M1Data,
    min_bars: int = DEFAULT_MIN_ASIAN_BARS,
    start_hour: int = 0,
    end_hour: int = 7,
    mask_invalid: bool = True,
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
    - Mặc định mask_invalid=True: các ngày có valid=False sẽ tự động có asia_high và asia_low
      được gán bằng NaN để đảm bảo an toàn, tránh tính toán nhầm ở các bước sau.

    Tham số:
        m1: Dữ liệu nến M1 (DataFrame hoặc M1Data).
        min_bars: Số nến tối thiểu để coi phiên Á là hợp lệ (mặc định 378).
        start_hour: Giờ bắt đầu tính vùng Á theo UTC (mặc định 0).
        end_hour: Giờ kết thúc tính vùng Á theo UTC (mặc định 7).
        mask_invalid: Nếu True, gán NaN cho asia_high/low khi valid=False (mặc định True).

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


def generate_breakout_signals(
    m1: pd.DataFrame | M1Data,
    asian_ranges: pd.DataFrame | None = None,
    atr_multiplier: float = 0.1,
    window_start_hour: int = 7,
    window_end_hour: int = 13,
) -> pd.DataFrame:
    """Sinh tín hiệu phá vỡ (Breakout) phiên Á theo PROTOCOL.md mục 4.

    Cửa sổ vào lệnh:
    - Nửa mở [window_start_hour:00, window_end_hour:00) UTC (mặc định [07:00, 13:00)).
      Nến 12:59:00 được xét, nến 13:00:00 nằm ngoài cửa sổ.

    Quy tắc kích hoạt tín hiệu (tại nến M1 đóng cửa):
    - Tín hiệu BUY: close > asia_high + atr_multiplier * atr (nghiêm ngặt >).
    - Tín hiệu SELL: close < asia_low - atr_multiplier * atr (nghiêm ngặt <).
    - Giá chạm râu (high/low) nhưng close nằm trong: KHÔNG sinh tín hiệu.
    - Giá đóng cửa đúng bằng ngưỡng: KHÔNG sinh tín hiệu.
    - ATR(H1, 14) được lấy động tại thời điểm nến M1 đang xét (từ nến H1 đã đóng gần nhất).
    - Tối đa 1 lệnh/ngày (hướng phá vỡ đầu tiên). Nếu cả BUY và SELL đều thỏa mãn trong ngày,
      chọn hướng xuất hiện sớm hơn.

    Vào lệnh:
    - Vào lệnh tại giá MỞ (Open) của nến M1 kế tiếp theo thứ tự thực tế trong chuỗi dữ liệu.
    - Nếu nến tín hiệu là nến cuối cùng trước 13:00 (hoặc nến kế tiếp có giờ >= 13:00 hoặc hết dữ liệu),
      bỏ qua tín hiệu vì không kịp vào lệnh trong cửa sổ [07:00, 13:00).
    - Bỏ qua các ngày có vùng Á không hợp lệ (valid=False) hoặc ATR bị NaN.

    Tham số:
        m1: Dữ liệu nến M1 (DataFrame hoặc M1Data).
        asian_ranges: Bảng vùng Á tính từ compute_asian_range (nếu None sẽ tự tính).
        atr_multiplier: Hệ số đệm ATR (mặc định 0.1).
        window_start_hour: Giờ bắt đầu cửa sổ vào lệnh theo UTC (mặc định 7).
        window_end_hour: Giờ kết thúc cửa sổ vào lệnh theo UTC (mặc định 13).

    Trả về:
        DataFrame chứa mỗi ngày có tín hiệu 1 dòng:
        - date: pd.Timestamp (chuẩn hóa về 00:00:00 UTC)
        - direction: str ("BUY" hoặc "SELL")
        - signal_time: pd.Timestamp (thời điểm nến M1 đóng cửa sinh tín hiệu)
        - entry_time: pd.Timestamp (thời điểm nến M1 mở cửa vào lệnh)
        - entry_price: float (giá Open tại entry_time)
        - threshold: float (ngưỡng giá kích hoạt tín hiệu)
        - atr: float (giá trị ATR tại nến tín hiệu)
        - asia_high: float (đỉnh vùng Á ngày đó)
        - asia_low: float (đáy vùng Á ngày đó)
    """
    m1_df = m1.to_dataframe() if isinstance(m1, M1Data) else m1.copy()
    columns_out = [
        "date",
        "direction",
        "signal_time",
        "entry_time",
        "entry_price",
        "threshold",
        "atr",
        "asia_high",
        "asia_low",
    ]

    if m1_df.empty:
        return pd.DataFrame(columns=columns_out)

    if "time_utc" not in m1_df.columns:
        raise KeyError("Dữ liệu M1 cần có cột 'time_utc'")

    m1_df["time_utc"] = pd.to_datetime(m1_df["time_utc"])
    m1_df = m1_df.sort_values("time_utc").reset_index(drop=True)

    # Đảm bảo có cột ATR(H1, 14) không look-ahead
    if "atr" not in m1_df.columns or m1_df["atr"].isna().all():
        m1_df = attach_h1_atr(m1_df)

    # Đảm bảo có bảng vùng Á
    if asian_ranges is None:
        asian_ranges = compute_asian_range(m1_df, mask_invalid=True)

    # Chỉ lọc các ngày hợp lệ
    valid_days = asian_ranges[
        asian_ranges["valid"] & asian_ranges["asia_high"].notna() & asian_ranges["asia_low"].notna()
    ]
    if valid_days.empty:
        return pd.DataFrame(columns=columns_out)

    # Map tra cứu nhanh vùng Á theo ngày
    ranges_by_date: dict[pd.Timestamp, tuple[float, float]] = {
        pd.Timestamp(row["date"]).floor("D"): (float(row["asia_high"]), float(row["asia_low"]))
        for _, row in valid_days.iterrows()
    }

    m1_df["trade_date"] = m1_df["time_utc"].dt.floor("D")
    records: list[dict[str, Any]] = []

    for trade_date, day_group in m1_df.groupby("trade_date", sort=True):
        if trade_date not in ranges_by_date:
            continue

        asia_high, asia_low = ranges_by_date[trade_date]
        day_bars = day_group.reset_index(drop=True)
        n_day_bars = len(day_bars)

        # Duyệt nến trong ngày tìm tín hiệu đầu tiên
        for i in range(n_day_bars):
            bar_time = day_bars["time_utc"].iloc[i]
            bar_hour = bar_time.hour

            # Cửa sổ vào lệnh: [window_start_hour, window_end_hour)
            if bar_hour < window_start_hour or bar_hour >= window_end_hour:
                continue

            bar_atr = day_bars["atr"].iloc[i]
            if pd.isna(bar_atr) or bar_atr <= 0:
                continue

            bar_close = day_bars["close"].iloc[i]
            buy_threshold = asia_high + atr_multiplier * bar_atr
            sell_threshold = asia_low - atr_multiplier * bar_atr

            is_buy = bar_close > buy_threshold
            is_sell = bar_close < sell_threshold

            if is_buy or is_sell:
                # Tìm nến kế tiếp thực tế để vào lệnh
                if i + 1 < n_day_bars:
                    next_bar = day_bars.iloc[i + 1]
                    next_time = next_bar["time_utc"]
                    # Nến kế tiếp phải vào lệnh trước khi cửa sổ đóng
                    if next_time.hour < window_end_hour and next_time.floor("D") == trade_date:
                        direction = "BUY" if is_buy else "SELL"
                        thresh = buy_threshold if is_buy else sell_threshold
                        records.append({
                            "date": trade_date,
                            "direction": direction,
                            "signal_time": bar_time,
                            "entry_time": next_time,
                            "entry_price": float(next_bar["open"]),
                            "threshold": thresh,
                            "atr": float(bar_atr),
                            "asia_high": asia_high,
                            "asia_low": asia_low,
                        })
                # Tối đa 1 lệnh/ngày: dù có khớp được nến kế tiếp hay không,
                # đây là hướng phá vỡ đầu tiên, dừng tìm kiếm trong ngày này.
                break

    if not records:
        return pd.DataFrame(columns=columns_out)

    return pd.DataFrame(records)[columns_out]


