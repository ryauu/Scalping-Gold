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


import logging

logger = logging.getLogger(__name__)


def generate_breakout_signals(
    m1: pd.DataFrame | M1Data,
    asian_ranges: pd.DataFrame | None = None,
    atr_multiplier: float = 0.1,
    window_start_hour: int = 7,
    window_end_hour: int = 13,
    return_summary: bool = False,
) -> pd.DataFrame | tuple[pd.DataFrame, dict[str, int]]:
    """Sinh tín hiệu phá vỡ (Breakout) phiên Á theo PROTOCOL.md mục 4.

    Tối ưu hóa:
    - Lọc trước cửa sổ [window_start_hour, window_end_hour) UTC.
    - Vector hóa bằng NumPy (np.argmax) cho từng ngày, tốc độ thực thi C.

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

    Vào lệnh & Độ trễ:
    - Vào lệnh tại giá MỞ (Open) của nến M1 kế tiếp theo thứ tự thực tế trong chuỗi dữ liệu.
    - Độ trễ vào lệnh: entry_delay = (entry_time - signal_time) tính bằng phút.
    - Nếu nến tín hiệu là nến cuối cùng trước 13:00 (hoặc nến kế tiếp có giờ >= 13:00 hoặc hết dữ liệu),
      bỏ qua tín hiệu vì không kịp vào lệnh trong cửa sổ [07:00, 13:00).
    - Bỏ qua các ngày có vùng Á không hợp lệ (valid=False) hoặc ATR bị NaN.

    Kiểm đếm & Tóm tắt (Summary):
    - Tự động thống kê: total_days, invalid_asian_days, valid_asian_days, atr_nan_days,
      no_signal_days, signal_without_entry_days, valid_signal_days.
    - Cảnh báo rõ ràng nếu dữ liệu M1 bị thiếu phiên Á dẫn đến invalid_asian_days chiếm 100%.
    - Luôn lưu bảng tóm tắt vào thuộc tính DataFrame: `signals_df.attrs["summary"]`.

    Tham số:
        m1: Dữ liệu nến M1 (DataFrame hoặc M1Data).
        asian_ranges: Bảng vùng Á tính từ compute_asian_range (nếu None sẽ tự tính).
        atr_multiplier: Hệ số đệm ATR (mặc định 0.1).
        window_start_hour: Giờ bắt đầu cửa sổ vào lệnh theo UTC (mặc định 7).
        window_end_hour: Giờ kết thúc cửa sổ vào lệnh theo UTC (mặc định 13).
        return_summary: Nếu True, trả về tuple (signals_df, summary_dict).
                        Nếu False (mặc định), trả về signals_df (summary có trong .attrs["summary"]).

    Trả về:
        DataFrame chứa mỗi ngày có tín hiệu 1 dòng:
        - date: pd.Timestamp (chuẩn hóa về 00:00:00 UTC)
        - direction: str ("BUY" hoặc "SELL")
        - signal_time: pd.Timestamp (thời điểm nến M1 đóng cửa sinh tín hiệu)
        - entry_time: pd.Timestamp (thời điểm nến M1 mở cửa vào lệnh)
        - entry_delay: int (khoảng cách phút giữa entry_time và signal_time)
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
        "entry_delay",
        "entry_price",
        "threshold",
        "atr",
        "asia_high",
        "asia_low",
    ]

    empty_summary: dict[str, int] = {
        "total_days": 0,
        "invalid_asian_days": 0,
        "valid_asian_days": 0,
        "atr_nan_days": 0,
        "no_signal_days": 0,
        "signal_without_entry_days": 0,
        "valid_signal_days": 0,
    }

    if m1_df.empty:
        empty_df = pd.DataFrame(columns=columns_out)
        empty_df.attrs["summary"] = empty_summary
        return (empty_df, empty_summary) if return_summary else empty_df

    if "time_utc" not in m1_df.columns:
        raise KeyError("Dữ liệu M1 cần có cột 'time_utc'")

    time_ts = pd.to_datetime(m1_df["time_utc"])
    m1_df["time_utc"] = time_ts

    # Đảm bảo có cột ATR(H1, 14) không look-ahead
    if "atr" not in m1_df.columns or m1_df["atr"].isna().all():
        m1_df = attach_h1_atr(m1_df)

    # Đảm bảo có bảng vùng Á
    if asian_ranges is None:
        asian_ranges = compute_asian_range(m1_df, mask_invalid=True)

    total_days = len(asian_ranges)
    valid_mask = (
        asian_ranges["valid"]
        & asian_ranges["asia_high"].notna()
        & asian_ranges["asia_low"].notna()
    )
    valid_days_df = asian_ranges[valid_mask]
    invalid_asian_days = total_days - len(valid_days_df)
    valid_asian_days = len(valid_days_df)

    # Cảnh báo nếu toàn bộ ngày đều có vùng Á không hợp lệ (ví dụ M1 chỉ chứa cửa sổ 07-13h)
    if invalid_asian_days == total_days and total_days > 0:
        logger.warning(
            "Cảnh báo: Toàn bộ %d ngày đều có vùng Á không hợp lệ (n_bars < %d hoặc thiếu dữ liệu). "
            "Hàm trả về DataFrame rỗng. Hãy kiểm tra xem dữ liệu M1 truyền vào có bị cắt mất cửa sổ "
            "phiên Á [00:00, 07:00) UTC hay không!",
            total_days,
            DEFAULT_MIN_ASIAN_BARS,
        )

    if valid_days_df.empty:
        summary = {
            "total_days": total_days,
            "invalid_asian_days": invalid_asian_days,
            "valid_asian_days": 0,
            "atr_nan_days": 0,
            "no_signal_days": 0,
            "signal_without_entry_days": 0,
            "valid_signal_days": 0,
        }
        res_empty = pd.DataFrame(columns=columns_out)
        res_empty.attrs["summary"] = summary
        return (res_empty, summary) if return_summary else res_empty

    # Map tra cứu nhanh vùng Á theo ngày
    ranges_by_date: dict[pd.Timestamp, tuple[float, float]] = {
        pd.Timestamp(row["date"]).floor("D"): (float(row["asia_high"]), float(row["asia_low"]))
        for _, row in valid_days_df.iterrows()
    }

    # Lọc trước nến M1 trong cửa sổ vào lệnh [window_start_hour, window_end_hour)
    hours = m1_df["time_utc"].dt.hour
    in_window_mask = (hours >= window_start_hour) & (hours < window_end_hour)
    win_m1 = m1_df[in_window_mask].sort_values("time_utc").reset_index(drop=True)
    win_m1["trade_date"] = win_m1["time_utc"].dt.floor("D")

    records: list[dict[str, Any]] = []
    atr_nan_days = 0
    no_signal_days = 0
    signal_without_entry_days = 0
    valid_signal_days = 0

    grouped = win_m1.groupby("trade_date", sort=True)
    grouped_dates = set(grouped.groups.keys())

    # Duyệt qua từng ngày có vùng Á hợp lệ
    for trade_date_raw in valid_days_df["date"]:
        trade_date = pd.Timestamp(trade_date_raw).floor("D")
        if trade_date not in grouped_dates:
            # Ngày có vùng Á hợp lệ nhưng không có nến nào trong cửa sổ 07-13h
            no_signal_days += 1
            continue

        asia_high, asia_low = ranges_by_date[trade_date]
        group = grouped.get_group(trade_date)

        close = group["close"].to_numpy()
        atr = group["atr"].to_numpy()
        open_prices = group["open"].to_numpy()
        times = group["time_utc"].to_numpy()

        valid_atr = ~np.isnan(atr) & (atr > 0)
        if not np.any(valid_atr):
            atr_nan_days += 1
            continue

        buy_thresh = asia_high + atr_multiplier * atr
        sell_thresh = asia_low - atr_multiplier * atr

        buy_cond = valid_atr & (close > buy_thresh)
        sell_cond = valid_atr & (close < sell_thresh)

        has_buy = np.any(buy_cond)
        has_sell = np.any(sell_cond)

        if not has_buy and not has_sell:
            no_signal_days += 1
            continue

        if has_buy and not has_sell:
            sig_idx = int(np.argmax(buy_cond))
            direction = "BUY"
            thresh = buy_thresh[sig_idx]
        elif has_sell and not has_buy:
            sig_idx = int(np.argmax(sell_cond))
            direction = "SELL"
            thresh = sell_thresh[sig_idx]
        else:
            b_idx = int(np.argmax(buy_cond))
            s_idx = int(np.argmax(sell_cond))
            if b_idx < s_idx:
                sig_idx = b_idx
                direction = "BUY"
                thresh = buy_thresh[sig_idx]
            else:
                sig_idx = s_idx
                direction = "SELL"
                thresh = sell_thresh[sig_idx]

        # Kiểm tra nến kế tiếp để vào lệnh
        # Vì toàn bộ mảng group đã nằm trong [window_start_hour, window_end_hour),
        # nếu sig_idx + 1 < len(close), nến kế tiếp đảm bảo cùng ngày và giờ < window_end_hour
        if sig_idx + 1 < len(close):
            entry_idx = sig_idx + 1
            entry_time = times[entry_idx]
            sig_time = times[sig_idx]
            entry_delay_min = int((entry_time - sig_time) / np.timedelta64(1, "m"))
            valid_signal_days += 1

            records.append({
                "date": trade_date,
                "direction": direction,
                "signal_time": pd.Timestamp(sig_time),
                "entry_time": pd.Timestamp(entry_time),
                "entry_delay": entry_delay_min,
                "entry_price": float(open_prices[entry_idx]),
                "threshold": float(thresh),
                "atr": float(atr[sig_idx]),
                "asia_high": float(asia_high),
                "asia_low": float(asia_low),
            })
        else:
            # Tín hiệu rơi vào đúng nến cuối cùng của cửa sổ hôm đó (không có nến tiếp theo < 13:00)
            signal_without_entry_days += 1

    summary = {
        "total_days": total_days,
        "invalid_asian_days": invalid_asian_days,
        "valid_asian_days": valid_asian_days,
        "atr_nan_days": atr_nan_days,
        "no_signal_days": no_signal_days,
        "signal_without_entry_days": signal_without_entry_days,
        "valid_signal_days": valid_signal_days,
    }

    if records:
        res_df = pd.DataFrame(records)[columns_out]
    else:
        res_df = pd.DataFrame(columns=columns_out)

    res_df.attrs["summary"] = summary

    return (res_df, summary) if return_summary else res_df



