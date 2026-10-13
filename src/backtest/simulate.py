"""Mô-đun simulate.py - Mô phỏng khớp lệnh và quản lý rủi ro (R-multiple).

Quy ước mô phỏng C3 (đã đăng ký tại PROTOCOL.md mục 9):
- Dữ liệu M1 là giá bid; 1 point = 0,01.
- Spread = trung vị spread năm đó x 1,25; slip = 5 points mỗi lần khớp (vào và ra); cột spread từng nến không dùng.
- BUY vào tại Open nến kế + spread + slip; SELL vào tại Open nến kế - slip.
- R_dist = |giá vào đã gồm chi phí - SL|; TP = giá vào +- 2 x R_dist.
- Kích hoạt:
  * BUY: SL khi bid low <= SL, TP khi bid high >= TP.
  * SELL: SL khi bid high + spread >= SL, TP khi bid low + spread <= TP.
- Thoát tại mức kích hoạt: BUY trừ slip, SELL cộng slip.
- Kiểm tra từ chính nến vào lệnh.
- Gap qua SL: thoát tại Open (BUY: Open - slip; SELL: Open + spread + slip), lý do SL_GAP.
- Gap qua TP: chỉ lấy đúng mức TP (bảo thủ, bất đối xứng có chủ đích).
- Chạm cả hai trong một nến: tính SL.
- Thoát cứng: nến đầu tiên có giờ >= 16:00 UTC, tại Open (BUY: - slip; SELL: + spread + slip), lý do TIME.
- Hết dữ liệu trước 16:00: thoát tại Close nến cuối (BUY: - slip; SELL: + spread + slip), ghi DATA_END và đếm.
- entry_delay được ghi lại, không dùng để lọc (nếu thiếu tính từ entry_time - signal_time, nếu không có để NaN).
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from .data import M1Data

logger = logging.getLogger(__name__)

# Thông số hợp đồng MT5 cho GOLD (DATA.md & PROTOCOL.md)
DEFAULT_POINT: float = 0.01
DEFAULT_SLIP_POINTS: float = 5.0
DEFAULT_SPREAD_MULTIPLIER: float = 1.25
STRESS_SPREAD_POINTS: float = 51.0

# Bảng trung vị spread theo năm (DATA.md mục 5) — NGUỒN DUY NHẤT CỐ ĐỊNH, KHÔNG TRUY VẤN RUNTIME
YEARLY_MEDIAN_SPREADS: dict[int, float] = {
    2014: 45.0,
    2015: 39.0,
    2016: 43.0,
    2017: 41.0,
    2018: 32.0,
    2019: 32.0,
    2020: 33.0,
    2021: 35.0,
    2022: 26.0,
    2023: 30.0,
    2024: 31.0,
    2025: 34.0,
    2026: 51.0,
}


def get_effective_spread(
    year: int,
    multiplier: float = DEFAULT_SPREAD_MULTIPLIER,
    point: float = DEFAULT_POINT,
    stress_points: float | None = None,
) -> float:
    """Tính mức spread hiệu dụng quy đổi ra giá cho một năm.

    Công thức theo C3 & PROTOCOL.md:
    - Kịch bản stress (nếu stress_points được truyền): spread_price = stress_points * point.
    - Mặc định: spread_price = trung vị spread năm đó * multiplier * point.
      Sử dụng YEARLY_MEDIAN_SPREADS (khớp DATA.md) làm nguồn duy nhất.
      Nếu thiếu năm, báo lỗi KeyError rõ ràng, không âm thầm điền giá trị giả định.
    """
    if stress_points is not None:
        return float(stress_points * point)

    if year not in YEARLY_MEDIAN_SPREADS:
        raise KeyError(
            f"Năm {year} không có trong bảng trung vị spread YEARLY_MEDIAN_SPREADS (xem DATA.md). "
            f"Các năm hợp lệ: {sorted(YEARLY_MEDIAN_SPREADS.keys())}."
        )

    med = YEARLY_MEDIAN_SPREADS[year]
    return float(med * multiplier * point)


def simulate_single_trade(
    direction: str,
    entry_time: pd.Timestamp | str,
    entry_open: float,
    sl: float,
    m1_bars: pd.DataFrame | M1Data,
    spread: float = 0.0,
    slip: float = 0.0,
    point: float = DEFAULT_POINT,
    tp: float | None = None,
    trade_date: pd.Timestamp | str | None = None,
    year: int | None = None,
    signal_time: pd.Timestamp | str | None = None,
    entry_delay: float | None = None,
    atr: float | None = None,
    asia_high: float | None = None,
    asia_low: float | None = None,
) -> dict[str, Any]:
    """Mô phỏng khớp lệnh chi tiết cho 1 giao dịch đơn lẻ từ entry_time đến 16:00 UTC.

    Hỗ trợ cả DataFrame và M1Data.

    Tham số:
        direction: "BUY" hoặc "SELL".
        entry_time: Thời điểm nến M1 vào lệnh (Open).
        entry_open: Giá Open của nến M1 tại entry_time (giá Bid MT5).
        sl: Mức dừng lỗ Stop Loss ban đầu.
        m1_bars: Dữ liệu nến M1 (DataFrame hoặc M1Data).
        spread: Spread tính theo giá.
        slip: Trượt giá mỗi lần khớp tính theo giá.
        point: Kích thước point của GOLD (mặc định 0.01).
        tp: Mức chốt lời Take Profit (nếu None, tự tính entry_price +- 2 * R_dist).
        trade_date: Ngày giao dịch (Timestamp hoặc chuỗi).
        year: Năm giao dịch (int).
        signal_time: Thời điểm sinh tín hiệu (tùy chọn để đối chiếu trực quan).
        entry_delay: Độ trễ vào lệnh tính bằng phút (tùy chọn; nếu thiếu tự tính từ entry_time - signal_time).
        atr: Giá trị ATR tại nến tín hiệu (tùy chọn).
        asia_high: Đỉnh vùng Á (tùy chọn).
        asia_low: Đáy vùng Á (tùy chọn).

    Trả về:
        dict chứa đầy đủ thông tin giao dịch phục vụ thống kê và kiểm tra trực quan.
    """
    dir_clean = str(direction).strip().upper()
    if dir_clean not in ("BUY", "SELL"):
        raise ValueError(f"Hướng lệnh không hợp lệ: {direction} (chỉ chấp nhận 'BUY' hoặc 'SELL')")

    entry_time_ts = pd.to_datetime(entry_time)

    # Chuẩn hóa trade_date và year
    if trade_date is None:
        trade_date_ts = entry_time_ts.floor("D")
    else:
        trade_date_ts = pd.to_datetime(trade_date).floor("D")

    year_int = int(year) if year is not None else int(trade_date_ts.year)

    # 1. Tính giá vào lệnh thực tế đã gồm chi phí và R_dist
    if dir_clean == "BUY":
        entry_price = float(entry_open + spread + slip)
        r_dist = float(entry_price - sl)
        if r_dist <= 0:
            raise ValueError(f"R_dist <= 0 cho BUY: entry_price={entry_price}, sl={sl}")
        calc_tp = float(entry_price + 2.0 * r_dist) if tp is None else float(tp)
    else:  # SELL
        entry_price = float(entry_open - slip)
        r_dist = float(sl - entry_price)
        if r_dist <= 0:
            raise ValueError(f"R_dist <= 0 cho SELL: entry_price={entry_price}, sl={sl}")
        calc_tp = float(entry_price - 2.0 * r_dist) if tp is None else float(tp)

    # 2. Chuẩn bị mảng nến M1 (bọc bằng pd.Series để đảm bảo có .dt.tz kể cả khi nhận M1Data)
    if isinstance(m1_bars, M1Data):
        time_series = pd.Series(pd.to_datetime(m1_bars.time))
        opens = np.asarray(m1_bars.open, dtype=np.float64)
        highs = np.asarray(m1_bars.high, dtype=np.float64)
        lows = np.asarray(m1_bars.low, dtype=np.float64)
        closes = np.asarray(m1_bars.close, dtype=np.float64)
    elif isinstance(m1_bars, pd.DataFrame):
        if "time_utc" not in m1_bars.columns:
            raise KeyError("Dữ liệu nến M1 cần có cột 'time_utc'")
        time_series = pd.Series(pd.to_datetime(m1_bars["time_utc"]))
        opens = m1_bars["open"].to_numpy(dtype=np.float64)
        highs = m1_bars["high"].to_numpy(dtype=np.float64)
        lows = m1_bars["low"].to_numpy(dtype=np.float64)
        closes = m1_bars["close"].to_numpy(dtype=np.float64)
    else:
        raise TypeError(f"m1_bars phải là DataFrame hoặc M1Data, nhận được: {type(m1_bars)}")

    # Đồng bộ múi giờ nếu có
    if time_series.dt.tz is not None:
        if entry_time_ts.tzinfo is None:
            entry_time_ts = entry_time_ts.tz_localize("UTC")
        else:
            entry_time_ts = entry_time_ts.tz_convert(time_series.dt.tz)
        if trade_date_ts.tzinfo is None:
            trade_date_ts = trade_date_ts.tz_localize(time_series.dt.tz)
    elif entry_time_ts.tzinfo is not None:
        entry_time_ts = entry_time_ts.tz_localize(None)
        if trade_date_ts.tzinfo is not None:
            trade_date_ts = trade_date_ts.tz_localize(None)

    # Lọc các nến cùng ngày giao dịch từ entry_time_ts trở đi
    mask = (time_series >= entry_time_ts) & (time_series.dt.floor("D") == trade_date_ts)
    valid_indices = np.where(mask)[0]

    if len(valid_indices) == 0:
        raise ValueError(
            f"Không tìm thấy nến M1 nào từ {entry_time_ts} trong ngày {trade_date_ts.date()}"
        )

    times_sub = time_series.iloc[valid_indices].reset_index(drop=True)
    opens_sub = opens[valid_indices]
    highs_sub = highs[valid_indices]
    lows_sub = lows[valid_indices]
    closes_sub = closes[valid_indices]

    hours_sub = times_sub.dt.hour.to_numpy()
    n_bars = len(valid_indices)

    exit_price: float = 0.0
    exit_time: pd.Timestamp = times_sub.iloc[-1]
    exit_reason: str = "DATA_END"

    # 3. Duyệt từng nến từ chính nến vào lệnh (i=0) đến 16:00 UTC
    for i in range(n_bars):
        cur_t = times_sub.iloc[i]
        cur_h = hours_sub[i]
        o = opens_sub[i]
        hi = highs_sub[i]
        lo = lows_sub[i]

        # a) Nến đầu tiên có giờ >= 16:00: thoát ở giá mở
        if cur_h >= 16:
            if dir_clean == "BUY":
                exit_price = float(o - slip)
                exit_reason = "SL_GAP" if o <= sl else "TIME"
            else:  # SELL
                exit_price = float(o + spread + slip)
                exit_reason = "SL_GAP" if (o + spread >= sl) else "TIME"
            exit_time = cur_t
            break

        # b) Với nến trước 16:00, nếu i > 0 (nến sau nến vào lệnh):
        # Kiểm tra xem giá mở nến có gap qua SL không
        if i > 0:
            if dir_clean == "BUY" and o <= sl:
                exit_price = float(o - slip)
                exit_reason = "SL_GAP"
                exit_time = cur_t
                break
            elif dir_clean == "SELL" and o + spread >= sl:
                exit_price = float(o + spread + slip)
                exit_reason = "SL_GAP"
                exit_time = cur_t
                break

        # c) Kiểm tra chạm SL / TP trong nến (từ chính nến vào lệnh i=0):
        if dir_clean == "BUY":
            sl_hit = lo <= sl
            tp_hit = hi >= calc_tp
            if sl_hit and tp_hit:
                # Nếu nến chạm cả SL lẫn TP: tính SL (bảo thủ)
                exit_price = float(sl - slip)
                exit_reason = "SL"
                exit_time = cur_t
                break
            elif sl_hit:
                exit_price = float(sl - slip)
                exit_reason = "SL"
                exit_time = cur_t
                break
            elif tp_hit:
                # Gap qua TP không thưởng thêm, chỉ lấy đúng mức TP trừ slip (bảo thủ)
                exit_price = float(calc_tp - slip)
                exit_reason = "TP"
                exit_time = cur_t
                break
        else:  # SELL
            # SELL kích hoạt theo Ask: SL khi bid high + spread >= SL; TP khi bid low + spread <= TP
            sl_hit = hi + spread >= sl
            tp_hit = lo + spread <= calc_tp
            if sl_hit and tp_hit:
                # Nếu nến chạm cả SL lẫn TP: tính SL (bảo thủ)
                exit_price = float(sl + slip)
                exit_reason = "SL"
                exit_time = cur_t
                break
            elif sl_hit:
                exit_price = float(sl + slip)
                exit_reason = "SL"
                exit_time = cur_t
                break
            elif tp_hit:
                # Gap qua TP không thưởng thêm, chỉ lấy đúng mức TP cộng slip (bảo thủ)
                exit_price = float(calc_tp + slip)
                exit_reason = "TP"
                exit_time = cur_t
                break
    else:
        # d) Hết dữ liệu trước 16:00: thoát ở giá đóng cuối, lý do DATA_END
        exit_time = times_sub.iloc[-1]
        exit_reason = "DATA_END"
        if dir_clean == "BUY":
            exit_price = float(closes_sub[-1] - slip)
        else:
            exit_price = float(closes_sub[-1] + spread + slip)

    # 4. Tính PnL theo R-multiple
    if dir_clean == "BUY":
        pnl_r = float((exit_price - entry_price) / r_dist)
    else:
        pnl_r = float((entry_price - exit_price) / r_dist)

    # Chuẩn bị các cột hỗ trợ đối chiếu hình ảnh / kiểm tra trực quan
    sig_time_val = pd.to_datetime(signal_time) if signal_time is not None and pd.notna(signal_time) else None
    if entry_delay is not None and pd.notna(entry_delay):
        delay_val = float(entry_delay)
    elif sig_time_val is not None:
        delay_val = float((entry_time_ts - sig_time_val).total_seconds() / 60.0)
    else:
        delay_val = np.nan

    atr_val = float(atr) if (atr is not None and pd.notna(atr)) else np.nan
    r_atr_ratio = float(r_dist / atr_val) if (not np.isnan(atr_val) and atr_val > 0) else np.nan
    asia_high_val = float(asia_high) if (asia_high is not None and pd.notna(asia_high)) else np.nan
    asia_low_val = float(asia_low) if (asia_low is not None and pd.notna(asia_low)) else np.nan

    return {
        "date": trade_date_ts,
        "direction": dir_clean,
        "signal_time": sig_time_val,
        "entry_time": entry_time_ts,
        "entry_delay": delay_val,
        "entry_open": float(entry_open),
        "entry_price": float(entry_price),
        "exit_time": exit_time,
        "exit_price": float(exit_price),
        "sl": float(sl),
        "tp": float(calc_tp),
        "r_dist": float(r_dist),
        "atr": atr_val,
        "r_atr_ratio": r_atr_ratio,
        "asia_high": asia_high_val,
        "asia_low": asia_low_val,
        "exit_reason": exit_reason,
        "pnl_r": float(pnl_r),
        "spread": float(spread),
        "year": int(year_int),
    }


def simulate_trades(
    signals: pd.DataFrame,
    m1: pd.DataFrame | M1Data,
    point: float = DEFAULT_POINT,
    spread_multiplier: float = DEFAULT_SPREAD_MULTIPLIER,
    slip_points: float = DEFAULT_SLIP_POINTS,
    fixed_spread: float | None = None,
    fixed_slip: float | None = None,
    stress_spread: bool = False,
    return_summary: bool = False,
) -> pd.DataFrame | tuple[pd.DataFrame, dict[str, Any]]:
    """Mô phỏng toàn bộ các lệnh từ tín hiệu phá vỡ (Breakout signals).

    Giữ lại đầy đủ các cột tín hiệu (signal_time, entry_delay, atr, asia_high, asia_low, r_atr_ratio)
    để hỗ trợ dựng lại biểu đồ kiểm tra bằng mắt (30-50 lệnh) theo PROTOCOL.md mục 7.

    Tham số:
        signals: DataFrame chứa các tín hiệu phá vỡ sinh từ generate_breakout_signals.
        m1: Dữ liệu nến M1 (DataFrame hoặc M1Data).
        point: Kích thước point của GOLD (mặc định 0.01).
        spread_multiplier: Hệ số nhân spread theo năm (mặc định 1.25).
        slip_points: Trượt giá mỗi lần khớp tính theo points (mặc định 5.0 points = 0.05).
        fixed_spread: Nếu truyền, ghi đè spread thành giá trị cố định (tính theo giá).
        fixed_slip: Nếu truyền, ghi đè slip thành giá trị cố định (tính theo giá).
        stress_spread: Nếu True, dùng kịch bản stress 51 points cho mọi năm.
        return_summary: Nếu True, trả về tuple (trades_df, summary_dict).

    Trả về:
        trades_df: DataFrame với mỗi dòng là 1 lệnh đã mô phỏng, kèm summary trong .attrs["summary"].
    """
    columns_out = [
        "date",
        "direction",
        "signal_time",
        "entry_time",
        "entry_delay",
        "entry_open",
        "entry_price",
        "exit_time",
        "exit_price",
        "sl",
        "tp",
        "r_dist",
        "atr",
        "r_atr_ratio",
        "asia_high",
        "asia_low",
        "exit_reason",
        "pnl_r",
        "spread",
        "year",
    ]

    empty_summary: dict[str, Any] = {
        "total_trades": 0,
        "n_tp": 0,
        "n_sl": 0,
        "n_sl_gap": 0,
        "n_time": 0,
        "n_data_end": 0,
        "n_skipped": 0,
        "win_rate": 0.0,
        "sum_pnl_r": 0.0,
        "mean_pnl_r": 0.0,
    }

    if signals.empty:
        empty_df = pd.DataFrame(columns=columns_out)
        empty_df.attrs["summary"] = empty_summary
        return (empty_df, empty_summary) if return_summary else empty_df

    m1_df = m1.to_dataframe() if isinstance(m1, M1Data) else m1.copy()
    if m1_df.empty:
        empty_df = pd.DataFrame(columns=columns_out)
        empty_df.attrs["summary"] = empty_summary
        return (empty_df, empty_summary) if return_summary else empty_df

    if "time_utc" not in m1_df.columns:
        raise KeyError("Dữ liệu M1 cần có cột 'time_utc'")

    m1_df["time_utc"] = pd.to_datetime(m1_df["time_utc"])
    m1_df["trade_date"] = m1_df["time_utc"].dt.floor("D")

    # Lọc M1 chỉ giữ các ngày có tín hiệu để tăng tốc độ groupby
    signal_dates = set(pd.to_datetime(signals["date"]).dt.floor("D"))
    m1_filtered = m1_df[m1_df["trade_date"].isin(signal_dates)]

    # Nhóm trước M1 theo ngày giao dịch
    grouped_m1 = dict(tuple(m1_filtered.groupby("trade_date")))

    slip_val = fixed_slip if fixed_slip is not None else float(slip_points * point)

    records: list[dict[str, Any]] = []
    n_skipped: int = 0

    for _, row in signals.iterrows():
        trade_date = pd.Timestamp(row["date"]).floor("D")
        direction = str(row["direction"]).strip().upper()
        entry_time = pd.Timestamp(row["entry_time"])
        entry_open = float(row["entry_price"])

        # SL lấy từ asia_low (BUY) hoặc asia_high (SELL) nếu không có cột sl riêng
        if "sl" in row and pd.notna(row["sl"]):
            sl = float(row["sl"])
        elif direction == "BUY":
            sl = float(row["asia_low"])
        else:
            sl = float(row["asia_high"])

        tp = float(row["tp"]) if ("tp" in row and pd.notna(row["tp"])) else None
        year = trade_date.year

        # Tính spread cho lệnh
        if fixed_spread is not None:
            spread_val = float(fixed_spread)
        elif stress_spread:
            spread_val = float(STRESS_SPREAD_POINTS * point)
        else:
            spread_val = get_effective_spread(
                year,
                multiplier=spread_multiplier,
                point=point,
            )

        day_bars = grouped_m1.get(trade_date)
        if day_bars is None or day_bars.empty:
            logger.warning("Không có nến M1 cho ngày %s, bỏ qua lệnh.", trade_date.date())
            n_skipped += 1
            continue

        # Tính toán entry_delay nếu chưa có hoặc NaN
        sig_time_raw = row.get("signal_time")
        entry_delay_raw = row.get("entry_delay")
        if entry_delay_raw is not None and pd.notna(entry_delay_raw):
            delay_to_pass: float | None = float(entry_delay_raw)
        elif sig_time_raw is not None and pd.notna(sig_time_raw):
            delay_to_pass = float((entry_time - pd.to_datetime(sig_time_raw)).total_seconds() / 60.0)
        else:
            delay_to_pass = None

        trade_res = simulate_single_trade(
            direction=direction,
            entry_time=entry_time,
            entry_open=entry_open,
            sl=sl,
            m1_bars=day_bars,
            spread=spread_val,
            slip=slip_val,
            point=point,
            tp=tp,
            trade_date=trade_date,
            year=year,
            signal_time=sig_time_raw,
            entry_delay=delay_to_pass,
            atr=row.get("atr"),
            asia_high=row.get("asia_high"),
            asia_low=row.get("asia_low"),
        )
        records.append(trade_res)

    if records:
        trades_df = pd.DataFrame(records)[columns_out]
    else:
        trades_df = pd.DataFrame(columns=columns_out)

    # Thống kê tổng kết
    total_trades = len(trades_df)
    n_tp = int((trades_df["exit_reason"] == "TP").sum()) if total_trades > 0 else 0
    n_sl = int((trades_df["exit_reason"] == "SL").sum()) if total_trades > 0 else 0
    n_sl_gap = int((trades_df["exit_reason"] == "SL_GAP").sum()) if total_trades > 0 else 0
    n_time = int((trades_df["exit_reason"] == "TIME").sum()) if total_trades > 0 else 0
    n_data_end = int((trades_df["exit_reason"] == "DATA_END").sum()) if total_trades > 0 else 0

    win_count = int((trades_df["pnl_r"] > 0).sum()) if total_trades > 0 else 0
    win_rate = float(win_count / total_trades) if total_trades > 0 else 0.0
    sum_pnl_r = float(trades_df["pnl_r"].sum()) if total_trades > 0 else 0.0
    mean_pnl_r = float(trades_df["pnl_r"].mean()) if total_trades > 0 else 0.0

    summary = {
        "total_trades": total_trades,
        "n_tp": n_tp,
        "n_sl": n_sl,
        "n_sl_gap": n_sl_gap,
        "n_time": n_time,
        "n_data_end": n_data_end,
        "n_skipped": n_skipped,
        "win_rate": win_rate,
        "sum_pnl_r": sum_pnl_r,
        "mean_pnl_r": mean_pnl_r,
    }

    trades_df.attrs["summary"] = summary

    return (trades_df, summary) if return_summary else trades_df
