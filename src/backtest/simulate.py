"""Mô-đun simulate.py - Mô phỏng khớp lệnh và quản lý rủi ro (R-multiple).

Quy ước giá (dữ liệu MT5 là giá Bid, point GOLD = 0.01):
- BUY:
  * Vào lệnh tại open_kế + spread + slip (mua ở Ask).
  * SL kích hoạt khi bid low <= SL.
  * TP kích hoạt khi bid high >= TP.
  * Thoát ở mức - slip.
- SELL:
  * Vào lệnh tại open_kế - slip (bán ở Bid).
  * SL kích hoạt theo Ask: bid high + spread >= SL.
  * TP kích hoạt theo Ask: bid low + spread <= TP.
  * Thoát ở mức + slip.
- Spread: Trung vị spread của năm đó x 1,25 (tính 1 lần cho mỗi năm).
- Trượt giá (slip): 5 points (0.05) mỗi lần khớp.
- R_dist = |giá vào đã gồm chi phí - SL|.
- TP = giá vào +- 2 x R_dist.
- pnl_R = (giá thoát - giá vào) / R_dist (đảo dấu cho SELL).

Thứ tự kiểm tra từng nến, từ chính nến vào lệnh đến 16:00 UTC:
1. Nếu giá mở nến đã vượt SL (gap): thoát ở giá mở, lý do SL_GAP.
2. Nếu nến chạm cả SL lẫn TP: tính SL (bảo thủ).
3. Chạm SL hoặc TP: thoát ở mức kích hoạt trừ/cộng slip.
4. Nến đầu tiên có giờ >= 16:00 UTC: thoát ở giá mở, lý do TIME.
5. Hết dữ liệu trước 16:00 UTC: thoát ở giá đóng cuối, lý do DATA_END, và đếm các lệnh này.

Quy ước bất đối xứng: Gap qua TP KHÔNG thưởng thêm, chỉ lấy đúng mức TP (bảo thủ, đã đăng ký tại PROTOCOL.md mục 9).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from .data import DEFAULT_DB_PATH, M1Data

logger = logging.getLogger(__name__)

# Thông số hợp đồng MT5 cho GOLD (DATA.md & PROTOCOL.md)
DEFAULT_POINT: float = 0.01
DEFAULT_SLIP_POINTS: float = 5.0
DEFAULT_SPREAD_MULTIPLIER: float = 1.25
STRESS_SPREAD_POINTS: float = 51.0

# Bảng trung vị spread theo năm (DATA.md mục 5, kiểm chứng từ meta.duckdb)
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


def compute_yearly_spread_medians(db_path: Path | str | None = None) -> dict[int, float]:
    """Tính trung vị spread theo năm từ DuckDB view m1_utc.

    Nếu không tìm thấy file DuckDB hoặc có lỗi kết nối, dự phòng sang bảng YEARLY_MEDIAN_SPREADS.
    """
    resolved_db = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if not resolved_db.exists():
        if Path("data/meta.duckdb").exists():
            resolved_db = Path("data/meta.duckdb")
        else:
            return dict(YEARLY_MEDIAN_SPREADS)

    try:
        import duckdb

        with duckdb.connect(str(resolved_db), read_only=True) as con:
            rows = con.execute(
                "SELECT year, median(spread) as med FROM m1_utc GROUP BY year ORDER BY year"
            ).fetchall()
            return {int(r[0]): float(r[1]) for r in rows}
    except Exception as exc:
        logger.warning("Không thể đọc trung vị spread từ DuckDB (%s), dùng bảng dự phòng.", exc)
        return dict(YEARLY_MEDIAN_SPREADS)


def get_effective_spread(
    year: int,
    multiplier: float = DEFAULT_SPREAD_MULTIPLIER,
    point: float = DEFAULT_POINT,
    yearly_medians: dict[int, float] | None = None,
    stress_points: float | None = None,
) -> float:
    """Tính mức spread hiệu dụng quy đổi ra giá cho một năm.

    Công thức:
    - Mặc định: spread_price = trung vị spread năm đó * multiplier * point.
    - Kịch bản stress (nếu stress_points được truyền): spread_price = stress_points * point.
    """
    if stress_points is not None:
        return float(stress_points * point)

    medians = yearly_medians if yearly_medians is not None else YEARLY_MEDIAN_SPREADS
    med = medians.get(year, 35.0)
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
) -> dict[str, Any]:
    """Mô phỏng khớp lệnh chi tiết cho 1 giao dịch đơn lẻ từ entry_time đến 16:00 UTC.

    Tham số:
        direction: "BUY" hoặc "SELL".
        entry_time: Thời điểm nến M1 vào lệnh (Open).
        entry_open: Giá Open của nến M1 tại entry_time (giá Bid MT5).
        sl: Mức dừng lỗ Stop Loss ban đầu.
        m1_bars: Dữ liệu nến M1 chứa các nến từ entry_time đến 16:00 (DataFrame hoặc M1Data).
        spread: Spread tính theo giá (ví dụ: 31 * 1.25 * 0.01 = 0.3875).
        slip: Trượt giá mỗi lần khớp tính theo giá (ví dụ: 5 * 0.01 = 0.05).
        point: Kích thước point của GOLD (mặc định 0.01).
        tp: Mức chốt lời Take Profit (nếu None, tự tính entry_price +- 2 * R_dist).
        trade_date: Ngày giao dịch (Timestamp hoặc chuỗi).
        year: Năm giao dịch (int).

    Trả về:
        dict chứa:
        - date, direction, entry_time, entry_price, exit_time, exit_price,
          sl, tp, r_dist, exit_reason, pnl_r, spread, year
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

    # 2. Chuẩn bị mảng nến M1
    if isinstance(m1_bars, M1Data):
        time_series = pd.to_datetime(m1_bars.time)
        opens = np.asarray(m1_bars.open, dtype=np.float64)
        highs = np.asarray(m1_bars.high, dtype=np.float64)
        lows = np.asarray(m1_bars.low, dtype=np.float64)
        closes = np.asarray(m1_bars.close, dtype=np.float64)
    elif isinstance(m1_bars, pd.DataFrame):
        if "time_utc" not in m1_bars.columns:
            raise KeyError("Dữ liệu nến M1 cần có cột 'time_utc'")
        time_series = pd.to_datetime(m1_bars["time_utc"])
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
                if o <= sl:
                    # Mở nến đã vượt SL (gap)
                    exit_price = float(o - slip)
                    exit_reason = "SL_GAP"
                else:
                    exit_price = float(o - slip)
                    exit_reason = "TIME"
                exit_time = cur_t
                break
            else:  # SELL
                if o + spread >= sl:
                    # Mở nến theo Ask đã vượt SL (gap)
                    exit_price = float(o + spread + slip)
                    exit_reason = "SL_GAP"
                else:
                    exit_price = float(o + spread + slip)
                    exit_reason = "TIME"
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

    return {
        "date": trade_date_ts,
        "direction": dir_clean,
        "entry_time": entry_time_ts,
        "entry_price": float(entry_price),
        "exit_time": exit_time,
        "exit_price": float(exit_price),
        "sl": float(sl),
        "tp": float(calc_tp),
        "r_dist": float(r_dist),
        "exit_reason": exit_reason,
        "pnl_r": float(pnl_r),
        "spread": float(spread),
        "year": int(year_int),
    }


def simulate_trades(
    signals: pd.DataFrame,
    m1: pd.DataFrame | M1Data,
    db_path: Path | str | None = None,
    point: float = DEFAULT_POINT,
    spread_multiplier: float = DEFAULT_SPREAD_MULTIPLIER,
    slip_points: float = DEFAULT_SLIP_POINTS,
    fixed_spread: float | None = None,
    fixed_slip: float | None = None,
    stress_spread: bool = False,
    return_summary: bool = False,
) -> pd.DataFrame | tuple[pd.DataFrame, dict[str, Any]]:
    """Mô phỏng toàn bộ các lệnh từ tín hiệu phá vỡ (Breakout signals).

    Tham số:
        signals: DataFrame chứa các tín hiệu phá vỡ sinh từ generate_breakout_signals.
        m1: Dữ liệu nến M1 (DataFrame hoặc M1Data).
        db_path: Đường dẫn tới meta.duckdb (tùy chọn).
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
        "entry_time",
        "entry_price",
        "exit_time",
        "exit_price",
        "sl",
        "tp",
        "r_dist",
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

    # Tải bảng trung vị spread nếu không dùng fixed_spread
    yearly_medians = compute_yearly_spread_medians(db_path) if fixed_spread is None else None

    # Lọc M1 chỉ giữ các ngày có tín hiệu để tăng tốc độ groupby
    signal_dates = set(pd.to_datetime(signals["date"]).dt.floor("D"))
    m1_filtered = m1_df[m1_df["trade_date"].isin(signal_dates)]

    # Nhóm trước M1 theo ngày giao dịch
    grouped_m1 = dict(tuple(m1_filtered.groupby("trade_date")))

    slip_val = fixed_slip if fixed_slip is not None else float(slip_points * point)

    records: list[dict[str, Any]] = []

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
                yearly_medians=yearly_medians,
            )

        day_bars = grouped_m1.get(trade_date)
        if day_bars is None or day_bars.empty:
            logger.warning("Không có nến M1 cho ngày %s, bỏ qua lệnh.", trade_date.date())
            continue

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
        "win_rate": win_rate,
        "sum_pnl_r": sum_pnl_r,
        "mean_pnl_r": mean_pnl_r,
    }

    trades_df.attrs["summary"] = summary

    return (trades_df, summary) if return_summary else trades_df
