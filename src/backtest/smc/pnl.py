"""Mô-đun pnl.py - Tính toán P&L theo R, Drawdown, Thống kê 8 năm & Monte Carlo C4(c) cho SMC v1.

Quy ước chuẩn hóa đóng băng theo PROTOCOL.md (Mục 4.3 & 4.4, tag prereg-smc-v1.5):
1. Công thức R của từng lệnh:
   - BUY:  R = (exit_price - entry_exec) / r_dist
   - SELL: R = (entry_exec - exit_price) / r_dist
   Trong đó:
   - entry_exec: Giá khớp lệnh thực tế đã gồm chi phí (BUY: Open + spread + slip; SELL: Open - slip).
   - exit_price: Giá thoát lệnh thực tế đã gồm chi phí theo C5.1, C5.2, C5.3.
   - r_dist: Khoảng cách dừng lỗ danh định đã kiểm duyệt (|entry_exec - sl|).

2. Quy ước gán lệnh vào năm lịch (Year Assignment):
   - Mặc định chính thức: Gán theo năm vào lệnh (entry_time.year, tức close_time_c.year).
   - Tham chiếu minh bạch: Đồng thời xuất kèm bảng theo năm thoát lệnh (exit_time.year).

3. Max Drawdown theo R (MDD in R):
   - Sắp xếp chuỗi lệnh tuần tự theo thời điểm thoát lệnh (exit_time).
   - Vốn tích lũy: E_i = sum_{j=1}^i R_j.
   - Đỉnh vốn lũy kế: H_i = max_{0 <= j <= i} E_j (E_0 = 0, H_0 = 0).
   - Drawdown: DD_i = H_i - E_i >= 0.
   - MDD = max_{1 <= i <= N} DD_i (đơn vị: R).

4. Kiểm soát ngẫu nhiên Monte Carlo theo C4(c):
   - Với mỗi lệnh thật, tạo cặp đối xứng BUY / SELL cùng entry_time, entry_open, khoảng cách SL d.
   - Mô phỏng thoát nến M1 theo quy tắc 24 hàng H1 (scan_position_exit_m1).
   - 1.000 lần mô phỏng hoán vị ngẫu nhiên (seed = 42, vector hóa NumPy).
   - p = (1 + sum(E_sim >= E_actual)) / 1001. Ngưỡng FWER (N_trials = 2): p <= 0.025.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

import numpy as np
import pandas as pd

from ..data import compute_wilder_atr, load_h1, load_m1
from ..metrics import DEFAULT_DEVELOPMENT_YEARS
from .signals import (
    get_friction_year,
    load_spreads_config,
    scan_position_exit_m1,
)

logger = logging.getLogger(__name__)


def compute_trade_r(
    direction: str,
    entry_exec: float,
    exit_price: float,
    r_dist: float,
) -> float:
    """Tính PnL theo đơn vị R cho một lệnh đơn lẻ.

    Công thức:
        BUY:  R = (exit_price - entry_exec) / r_dist
        SELL: R = (entry_exec - exit_price) / r_dist
    """
    if r_dist <= 0:
        raise ValueError(f"r_dist phải > 0, nhận được: {r_dist}")

    dir_upper = str(direction).strip().upper()
    if dir_upper == "BUY":
        return float((exit_price - entry_exec) / r_dist)
    elif dir_upper == "SELL":
        return float((entry_exec - exit_price) / r_dist)
    else:
        raise ValueError(f"Hướng không hợp lệ: {direction}. Chỉ chấp nhận 'BUY' hoặc 'SELL'.")


def attach_pnl_r(trades_df: pd.DataFrame) -> pd.DataFrame:
    """Gắn cột 'pnl_r' vào bảng lệnh nếu chưa có."""
    df = trades_df.copy()
    if "pnl_r" in df.columns and not df["pnl_r"].isna().any():
        return df

    required_cols = {"direction", "entry_exec", "exit_price", "r_dist"}
    missing = required_cols - set(df.columns)
    if missing:
        raise KeyError(f"Thiếu các cột bắt buộc để tính pnl_r: {missing}")

    directions = df["direction"].astype(str).str.upper().to_numpy()
    entry_execs = df["entry_exec"].to_numpy(dtype=np.float64)
    exit_prices = df["exit_price"].to_numpy(dtype=np.float64)
    r_dists = df["r_dist"].to_numpy(dtype=np.float64)

    if (r_dists <= 0).any():
        raise ValueError("Phát hiện r_dist <= 0 trong bảng lệnh.")

    pnl_r = np.where(
        directions == "BUY",
        (exit_prices - entry_execs) / r_dists,
        (entry_execs - exit_prices) / r_dists,
    )
    df["pnl_r"] = pnl_r
    return df


def compute_drawdown_series(
    trades_df: pd.DataFrame,
    sort_by_exit: bool = True,
) -> dict[str, Any]:
    """Tính Max Drawdown (MDD in R) sắp xếp theo thời điểm thoát lệnh (exit_time).

    Quy ước chuẩn hóa:
    - Sắp xếp bảng lệnh tăng dần theo 'exit_time'.
    - Vốn tích lũy: E_i = sum_{j=1}^i R_j.
    - Đỉnh vốn lũy kế: H_i = max_{0 <= j <= i} E_j (E_0 = 0, H_0 = 0).
    - Drawdown: DD_i = H_i - E_i >= 0.
    - MDD = max_{1 <= i <= N} DD_i.
    """
    df = attach_pnl_r(trades_df)
    if df.empty:
        return {
            "mdd_r": 0.0,
            "peak_r": 0.0,
            "max_drawdown_pct": 0.0,
            "equity_series": np.array([], dtype=np.float64),
            "drawdown_series": np.array([], dtype=np.float64),
            "peak_index": -1,
            "trough_index": -1,
        }

    if sort_by_exit:
        if "exit_time" in df.columns:
            df = df.sort_values("exit_time", kind="mergesort").reset_index(drop=True)
        else:
            logger.warning("Không tìm thấy cột 'exit_time', giữ nguyên thứ tự lệnh hiện tại.")

    pnl = df["pnl_r"].to_numpy(dtype=np.float64)
    equity = np.cumsum(pnl)
    # Thêm mốc 0 ban đầu để đỉnh vốn khởi điểm là 0
    equity_with_zero = np.insert(equity, 0, 0.0)
    peaks = np.maximum.accumulate(equity_with_zero)
    drawdowns = peaks - equity_with_zero

    # Bỏ mốc 0 ban đầu cho chuỗi trả về
    dd_trades = drawdowns[1:]
    mdd = float(np.max(dd_trades)) if len(dd_trades) > 0 else 0.0

    # Tìm vị trí đỉnh và đáy của giai đoạn MDD
    trough_idx = int(np.argmax(dd_trades)) if len(dd_trades) > 0 else -1
    if trough_idx >= 0:
        peak_idx = int(np.argmax(equity_with_zero[: trough_idx + 1])) - 1
    else:
        peak_idx = -1

    return {
        "mdd_r": mdd,
        "peak_r": float(np.max(peaks)),
        "equity_series": equity,
        "drawdown_series": dd_trades,
        "peak_index": peak_idx,
        "trough_index": trough_idx,
    }


def summarize_trades(trades_df: pd.DataFrame) -> dict[str, Any]:
    """Tổng kết các chỉ số hiệu năng cốt lõi (Expectancy, PF, Win Rate, Cơ cấu thoát)."""
    df = attach_pnl_r(trades_df)
    total_trades = len(df)
    if total_trades == 0:
        return {
            "total_trades": 0,
            "expectancy": 0.0,
            "profit_factor": 0.0,
            "win_rate": 0.0,
            "loss_rate": 0.0,
            "sum_pnl_r": 0.0,
            "n_win": 0,
            "n_loss": 0,
            "n_breakeven": 0,
            "exit_counts": {},
        }

    pnl = df["pnl_r"].to_numpy(dtype=np.float64)
    pos_mask = pnl > 0.0
    neg_mask = pnl < 0.0
    zero_mask = pnl == 0.0

    n_win = int(np.sum(pos_mask))
    n_loss = int(np.sum(neg_mask))
    n_breakeven = int(np.sum(zero_mask))

    sum_pnl_r = float(np.sum(pnl))
    expectancy = float(np.mean(pnl))

    pos_sum = float(np.sum(pnl[pos_mask]))
    neg_sum = float(np.sum(np.abs(pnl[neg_mask])))

    if neg_sum > 0:
        profit_factor = float(pos_sum / neg_sum)
    elif pos_sum > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    win_rate = float(n_win / total_trades)
    loss_rate = float(n_loss / total_trades)

    exit_counts = {}
    if "exit_reason" in df.columns:
        exit_counts = df["exit_reason"].value_counts().to_dict()

    return {
        "total_trades": total_trades,
        "expectancy": expectancy,
        "profit_factor": profit_factor,
        "win_rate": win_rate,
        "loss_rate": loss_rate,
        "sum_pnl_r": sum_pnl_r,
        "n_win": n_win,
        "n_loss": n_loss,
        "n_breakeven": n_breakeven,
        "exit_counts": exit_counts,
    }


def breakdown_by_year(
    trades_df: pd.DataFrame,
    years: Sequence[int] = DEFAULT_DEVELOPMENT_YEARS,
    by_column: str = "entry",
) -> pd.DataFrame:
    """Phân rã chi tiết 8 năm lịch (2018–2025).

    Tham số:
        trades_df: Bảng lệnh có cột pnl_r và cột thời gian.
        years: Danh sách 8 năm lịch cần đánh giá.
        by_column: 'entry' (theo năm vào lệnh, mặc định chuẩn C4) hoặc 'exit' (theo năm thoát).
    """
    df = attach_pnl_r(trades_df)
    if by_column == "entry":
        if "close_time_c" in df.columns:
            year_col = pd.to_datetime(df["close_time_c"]).dt.year
        elif "entry_time" in df.columns:
            year_col = pd.to_datetime(df["entry_time"]).dt.year
        elif "year" in df.columns:
            year_col = df["year"]
        else:
            raise KeyError("Cần có cột close_time_c hoặc entry_time để gán năm vào lệnh.")
    elif by_column == "exit":
        if "exit_time" in df.columns:
            year_col = pd.to_datetime(df["exit_time"]).dt.year
        else:
            raise KeyError("Cần có cột exit_time để gán năm thoát lệnh.")
    else:
        raise ValueError(f"by_column không hợp lệ: {by_column}")

    df_year = df.copy()
    df_year["_calc_year"] = year_col

    rows = []
    for y in years:
        sub = df_year[df_year["_calc_year"] == y]
        if sub.empty:
            rows.append({
                "year": int(y),
                "n_trades": 0,
                "expectancy": 0.0,
                "profit_factor": 0.0,
                "win_rate": 0.0,
                "sum_pnl_r": 0.0,
                "is_positive": False,
            })
        else:
            s = summarize_trades(sub)
            rows.append({
                "year": int(y),
                "n_trades": int(s["total_trades"]),
                "expectancy": float(s["expectancy"]),
                "profit_factor": float(s["profit_factor"]),
                "win_rate": float(s["win_rate"]),
                "sum_pnl_r": float(s["sum_pnl_r"]),
                "is_positive": bool(s["expectancy"] > 0.0 and s["total_trades"] > 0),
            })

    res_df = pd.DataFrame(rows)
    res_df.attrs["n_positive_years"] = int(res_df["is_positive"].sum())
    res_df.attrs["total_years"] = len(years)
    res_df.attrs["by_column"] = by_column
    return res_df


def simulate_c4c_pairs(
    trades_df: pd.DataFrame,
    m1_df: pd.DataFrame,
    h1_df: pd.DataFrame,
    spreads_cfg: dict[str, Any] | None = None,
    split_end_str: str = "2025-06-30 23:59:00",
) -> tuple[np.ndarray, np.ndarray]:
    """Mô phỏng hai nhánh BUY và SELL cho mỗi lệnh theo đúng C4(c) trên M1.

    Quy trình chuẩn C4(c):
    - entry_time = close_time_c.
    - entry_open = entry_open gốc.
    - d = |entry_open - sl_orig|.
    - Nhánh BUY:  entry_exec = entry_open + spread + slip, SL = entry_open - d, TP = entry_exec + 2*R_dist.
    - Nhánh SELL: entry_exec = entry_open - slip,          SL = entry_open + d, TP = entry_exec - 2*R_dist.
    - Quét M1 bằng scan_position_exit_m1 với cùng cấu hình 24 hàng H1.
    """
    spreads_cfg = spreads_cfg or load_spreads_config()
    split_end = pd.to_datetime(split_end_str)

    h1_times = pd.to_datetime(h1_df["time_utc"])
    time_to_row = {t: idx for idx, t in enumerate(h1_times)}
    duration = pd.Timedelta(hours=1)

    pnl_buys: list[float] = []
    pnl_sells: list[float] = []

    for _, row in trades_df.iterrows():
        t_entry = pd.Timestamp(row["close_time_c"])
        entry_open = float(row["entry_open"])
        sl_orig = float(row["sl"])
        d = abs(entry_open - sl_orig)
        year = t_entry.year

        spread_price, slip_price, _ = get_friction_year(year, spreads_cfg)

        # Tính thời gian giữ lệnh tối đa 24 hàng H1
        next_h1_idx = time_to_row.get(t_entry)
        if next_h1_idx is not None:
            max_h1_idx = min(next_h1_idx + 23, len(h1_df) - 1)
            max_hold_time = h1_times.iloc[max_h1_idx] + duration
        else:
            max_hold_time = t_entry + pd.Timedelta(hours=24)

        # 1. Nhánh BUY
        sl_buy = entry_open - d
        entry_exec_buy = entry_open + spread_price + slip_price
        r_dist_buy = entry_exec_buy - sl_buy
        tp_buy = entry_exec_buy + 2.0 * r_dist_buy

        _, _, exit_p_buy = scan_position_exit_m1(
            direction="BUY",
            entry_time=t_entry,
            entry_price=entry_exec_buy,
            sl=sl_buy,
            tp=tp_buy,
            spread=spread_price,
            slip=slip_price,
            m1_df=m1_df,
            max_exit_time=max_hold_time,
            split_end=split_end,
        )
        r_buy = compute_trade_r("BUY", entry_exec_buy, exit_p_buy, r_dist_buy)
        pnl_buys.append(r_buy)

        # 2. Nhánh SELL
        sl_sell = entry_open + d
        entry_exec_sell = entry_open - slip_price
        r_dist_sell = sl_sell - entry_exec_sell
        tp_sell = entry_exec_sell - 2.0 * r_dist_sell

        _, _, exit_p_sell = scan_position_exit_m1(
            direction="SELL",
            entry_time=t_entry,
            entry_price=entry_exec_sell,
            sl=sl_sell,
            tp=tp_sell,
            spread=spread_price,
            slip=slip_price,
            m1_df=m1_df,
            max_exit_time=max_hold_time,
            split_end=split_end,
        )
        r_sell = compute_trade_r("SELL", entry_exec_sell, exit_p_sell, r_dist_sell)
        pnl_sells.append(r_sell)

    return np.array(pnl_buys, dtype=np.float64), np.array(pnl_sells, dtype=np.float64)


def run_monte_carlo_permutation(
    pnl_buys: np.ndarray,
    pnl_sells: np.ndarray,
    actual_expectancy: float,
    n_simulations: int = 1000,
    seed: int = 42,
) -> dict[str, Any]:
    """Chạy 1.000 lần hoán vị Monte Carlo vector hóa theo quy ước C4(c)."""
    n_trades = len(pnl_buys)
    if n_trades == 0:
        return {
            "p_value": 1.0,
            "actual_expectancy": actual_expectancy,
            "n_exceed": 0,
            "n_simulations": n_simulations,
            "seed": seed,
            "sim_mean": 0.0,
            "sim_std": 0.0,
            "always_buy_expectancy": 0.0,
            "always_sell_expectancy": 0.0,
        }

    always_buy = float(np.mean(pnl_buys))
    always_sell = float(np.mean(pnl_sells))

    rng = np.random.default_rng(seed)
    # choices: ma trận (n_simulations, n_trades) nhị phân với p = 0.5
    choices = rng.integers(0, 2, size=(n_simulations, n_trades))
    sim_pnls = np.where(choices == 0, pnl_buys, pnl_sells)
    sim_expectancies = np.mean(sim_pnls, axis=1)

    n_exceed = int(np.sum(sim_expectancies >= actual_expectancy))
    p_value = float((1 + n_exceed) / (n_simulations + 1))

    return {
        "p_value": p_value,
        "actual_expectancy": actual_expectancy,
        "n_exceed": n_exceed,
        "n_simulations": n_simulations,
        "seed": seed,
        "sim_mean": float(np.mean(sim_expectancies)),
        "sim_std": float(np.std(sim_expectancies)),
        "sim_p50": float(np.median(sim_expectancies)),
        "sim_p95": float(np.percentile(sim_expectancies, 95)),
        "always_buy_expectancy": always_buy,
        "always_sell_expectancy": always_sell,
    }
