"""Mô-đun metrics.py - Đo lường hiệu suất và đánh giá 5 tiêu chí PASS.

Quy ước đo lường C4 (đã đăng ký tại PROTOCOL.md mục 9):
(a) “8 năm lịch” = 2018-2025; năm 2025 tính là một năm dù chỉ đến 30/6;
    điều kiện: kỳ vọng dương ở ít nhất 5 trong 8 năm.
(b) Kỳ vọng = trung bình pnl_r;
    profit factor = tổng pnl_r dương / |tổng pnl_r âm|;
    lệnh pnl_r = 0 không tính vào bên nào.
(c) Kiểm soát ngẫu nhiên: với mỗi lệnh thật, mô phỏng cả BUY và SELL cùng entry_time,
    cùng entry_open, cùng khoảng cách SL theo giá d = |entry_open - SL gốc|
    (SL BUY = entry_open - d, SL SELL = entry_open + d), TP = 2R theo C3, cùng chi phí.
    Mỗi lần mô phỏng rút ngẫu nhiên BUY hoặc SELL cho từng lệnh với xác suất 0,5;
    1.000 lần; seed cố định; thống kê = kỳ vọng;
    p = (1 + số lần kỳ vọng ngẫu nhiên >= kỳ vọng thật) / 1001.

Theo PROTOCOL.md mục 5 (Lớp Development, phải đạt CẢ 5 điều kiện):
1. Tổng số lệnh >= 800.
2. Kỳ vọng ròng >= +0,05R mỗi lệnh (sau chi phí).
3. Profit factor >= 1,10.
4. Kỳ vọng dương ở ít nhất 5 trên 8 năm lịch.
5. Hơn kiểm soát ngẫu nhiên với p <= 0,05 (1.000 lần mô phỏng).
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

import numpy as np
import pandas as pd

from .data import M1Data
from .simulate import (
    DEFAULT_POINT,
    DEFAULT_SLIP_POINTS,
    DEFAULT_SPREAD_MULTIPLIER,
    get_effective_spread,
    simulate_single_trade,
)

logger = logging.getLogger(__name__)

# 8 năm lịch chuẩn của lớp Development (PROTOCOL.md mục 2 & C4)
DEFAULT_DEVELOPMENT_YEARS: list[int] = list(range(2018, 2026))  # [2018, 2019, ..., 2025]


def summarize(trades: pd.DataFrame | Sequence[float]) -> dict[str, Any]:
    """Tổng kết các chỉ số hiệu suất cốt lõi từ bảng lệnh (hoặc chuỗi pnl_r).

    Quy ước C4:
    - Kỳ vọng = trung bình pnl_r.
    - Profit factor = tổng pnl_r dương / |tổng pnl_r âm|; lệnh pnl_r = 0 không tính vào bên nào.
      Nếu không có lệnh âm, trả về inf (nếu có lệnh dương) hoặc 0.0.
    - Tỷ lệ thắng = số lệnh pnl_r > 0 / tổng số lệnh.

    Tham số:
        trades: DataFrame chứa cột 'pnl_r' hoặc chuỗi/mảng các giá trị pnl_r.

    Trả về:
        dict chứa:
        - total_trades: int
        - expectancy: float
        - profit_factor: float
        - win_rate: float
        - loss_rate: float
        - sum_pnl_r: float
        - n_win: int
        - n_loss: int
        - n_breakeven: int
    """
    if isinstance(trades, pd.DataFrame):
        if "pnl_r" not in trades.columns:
            raise KeyError("DataFrame trades cần có cột 'pnl_r'")
        pnl = trades["pnl_r"].to_numpy(dtype=np.float64)
    else:
        pnl = np.asarray(trades, dtype=np.float64)

    total_trades = len(pnl)
    if np.isnan(pnl).any():
        n_nans = int(np.isnan(pnl).sum())
        raise ValueError(f"Phát hiện {n_nans} giá trị NaN trong pnl_r. Chặn lại để tránh làm sai lệch các so sánh ngưỡng.")

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
        }

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
    }


def by_year(
    trades: pd.DataFrame,
    years: Sequence[int] = DEFAULT_DEVELOPMENT_YEARS,
) -> pd.DataFrame:
    """Thống kê chi tiết số lệnh và kỳ vọng từng năm (8 năm lịch 2018-2025).

    Quy ước C4:
    - 8 năm lịch = 2018-2025; năm 2025 tính là một năm dù chỉ đến 30/6.
    - Điều kiện: Kỳ vọng dương ở ít nhất 5 trong 8 năm (expectancy > 0 và n_trades > 0).

    Tham số:
        trades: DataFrame chứa cột 'pnl_r' và 'year' (hoặc 'date').
        years: Danh sách các năm cần đánh giá (mặc định 2018-2025).

    Trả về:
        DataFrame với mỗi dòng là 1 năm gồm:
        - year, n_trades, expectancy, profit_factor, win_rate, sum_pnl_r, is_positive
    """
    if trades.empty:
        rows = [
            {
                "year": y,
                "n_trades": 0,
                "expectancy": 0.0,
                "profit_factor": 0.0,
                "win_rate": 0.0,
                "sum_pnl_r": 0.0,
                "is_positive": False,
            }
            for y in years
        ]
        return pd.DataFrame(rows)

    trades_copy = trades.copy()
    if "year" not in trades_copy.columns:
        if "date" in trades_copy.columns:
            trades_copy["year"] = pd.to_datetime(trades_copy["date"]).dt.year
        else:
            raise KeyError("DataFrame trades cần có cột 'year' hoặc 'date'")

    rows: list[dict[str, Any]] = []
    for y in years:
        year_trades = trades_copy[trades_copy["year"] == y]
        if year_trades.empty:
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
            s = summarize(year_trades)
            exp_val = s["expectancy"]
            rows.append({
                "year": int(y),
                "n_trades": int(s["total_trades"]),
                "expectancy": float(exp_val),
                "profit_factor": float(s["profit_factor"]),
                "win_rate": float(s["win_rate"]),
                "sum_pnl_r": float(s["sum_pnl_r"]),
                "is_positive": bool(exp_val > 0.0 and s["total_trades"] > 0),
            })

    res_df = pd.DataFrame(rows)
    res_df.attrs["n_positive_years"] = int(res_df["is_positive"].sum())
    res_df.attrs["total_years"] = len(years)
    return res_df


def random_control(
    trades: pd.DataFrame,
    m1: pd.DataFrame | M1Data | None = None,
    n_simulations: int = 1000,
    seed: int = 42,
    point: float = DEFAULT_POINT,
    spread_multiplier: float = DEFAULT_SPREAD_MULTIPLIER,
    slip_points: float = DEFAULT_SLIP_POINTS,
    precomputed_pairs: tuple[np.ndarray, np.ndarray] | None = None,
) -> dict[str, Any]:
    """Kiểm soát ngẫu nhiên (Monte Carlo permutation test) theo quy ước C4.

    Quy trình:
    1. Với mỗi lệnh thật trong trades:
       - Lấy cùng entry_time, cùng entry_open, cùng chi phí.
       - Khoảng cách SL theo giá: d = |entry_open - SL gốc|.
       - Mô phỏng nhánh BUY: SL = entry_open - d, TP = 2R.
       - Mô phỏng nhánh SELL: SL = entry_open + d, TP = 2R.
    2. Rút ngẫu nhiên BUY hoặc SELL cho từng lệnh với xác suất 0,5 qua NumPy vectorization.
    3. Thực hiện n_simulations lần (mặc định 1.000 lần) với seed cố định.
    4. Thống kê = kỳ vọng ròng (trung bình pnl_r).
    5. Tính p-value chuẩn xác: p = (1 + số lần kỳ vọng ngẫu nhiên >= kỳ vọng thật) / (n_simulations + 1).

    Tham số:
        trades: DataFrame chứa các lệnh giao dịch thật.
        m1: Dữ liệu nến M1 (DataFrame hoặc M1Data).
        n_simulations: Số lần mô phỏng ngẫu nhiên (mặc định 1.000).
        seed: Random seed cố định để đảm bảo kết quả tái lập tuyệt đối (mặc định 42).
        point: Kích thước point của GOLD (mặc định 0.01).
        spread_multiplier: Hệ số spread năm (mặc định 1.25).
        slip_points: Trượt giá (mặc định 5.0).
        precomputed_pairs: Cặp mảng (pnl_buys, pnl_sells) nạp sẵn (phục vụ unit test độc lập).

    Trả về:
        dict chứa:
        - p_value: float
        - actual_expectancy: float
        - n_exceed: int
        - n_simulations: int
        - seed: int
        - sim_mean: float
        - sim_std: float
        - sim_p50: float
        - sim_p95: float
        - sim_expectancies: np.ndarray (mảng 1.000 giá trị kỳ vọng ngẫu nhiên)
    """
    if trades.empty:
        return {
            "p_value": 1.0,
            "actual_expectancy": 0.0,
            "n_exceed": 0,
            "n_simulations": n_simulations,
            "seed": seed,
            "always_buy_expectancy": 0.0,
            "always_sell_expectancy": 0.0,
            "sim_mean": 0.0,
            "sim_std": 0.0,
            "sim_p50": 0.0,
            "sim_p95": 0.0,
            "sim_expectancies": np.zeros(n_simulations, dtype=np.float64),
            "pnl_buys": np.array([], dtype=np.float64),
            "pnl_sells": np.array([], dtype=np.float64),
        }

    actual_pnl = trades["pnl_r"].to_numpy(dtype=np.float64)
    actual_expectancy = float(np.mean(actual_pnl))

    if precomputed_pairs is not None:
        pnl_buys_arr, pnl_sells_arr = precomputed_pairs
        pnl_buys_arr = np.asarray(pnl_buys_arr, dtype=np.float64)
        pnl_sells_arr = np.asarray(pnl_sells_arr, dtype=np.float64)
    else:
        if m1 is None:
            raise ValueError("Cần cung cấp dữ liệu M1 để mô phỏng hai nhánh kiểm soát ngẫu nhiên.")

        m1_df = m1.to_dataframe() if isinstance(m1, M1Data) else m1.copy()
        m1_df["time_utc"] = pd.to_datetime(m1_df["time_utc"])
        m1_df["trade_date"] = m1_df["time_utc"].dt.floor("D")

        trade_dates_set = set(pd.to_datetime(trades["date"]).dt.floor("D"))
        m1_filtered = m1_df[m1_df["trade_date"].isin(trade_dates_set)]
        grouped_m1 = dict(tuple(m1_filtered.groupby("trade_date")))

        pnl_buys: list[float] = []
        pnl_sells: list[float] = []

        slip_val = float(slip_points * point)

        for _, row in trades.iterrows():
            trade_date = pd.Timestamp(row["date"]).floor("D")
            entry_time = pd.Timestamp(row["entry_time"])
            year = int(row["year"]) if "year" in row else trade_date.year

            # Lấy entry_open: ưu tiên dùng trực tiếp từ cột entry_open
            if "entry_open" in row and pd.notna(row["entry_open"]):
                open_val = float(row["entry_open"])
            elif str(row["direction"]).upper() == "BUY":
                spread_trade = float(row["spread"]) if "spread" in row else get_effective_spread(year, multiplier=spread_multiplier, point=point)
                open_val = float(row["entry_price"]) - spread_trade - slip_val
            else:
                open_val = float(row["entry_price"]) + slip_val

            sl_orig = float(row["sl"])
            d = abs(open_val - sl_orig)

            spread_val = float(row["spread"]) if "spread" in row else get_effective_spread(year, multiplier=spread_multiplier, point=point)

            day_bars = grouped_m1.get(trade_date)
            if day_bars is None or day_bars.empty:
                raise ValueError(
                    f"Không tìm thấy nến M1 cho ngày {trade_date.strftime('%Y-%m-%d')} trong random_control. "
                    f"Vì lệnh thật đã được mô phỏng từ đúng ngày đó, thiếu nến là lỗi bất thường."
                )

            # Nhánh BUY ngẫu nhiên: SL = open_val - d
            sl_buy = open_val - d
            res_buy = simulate_single_trade(
                direction="BUY",
                entry_time=entry_time,
                entry_open=open_val,
                sl=sl_buy,
                m1_bars=day_bars,
                spread=spread_val,
                slip=slip_val,
                point=point,
                trade_date=trade_date,
                year=year,
            )
            pnl_buys.append(res_buy["pnl_r"])

            # Nhánh SELL ngẫu nhiên: SL = open_val + d
            sl_sell = open_val + d
            res_sell = simulate_single_trade(
                direction="SELL",
                entry_time=entry_time,
                entry_open=open_val,
                sl=sl_sell,
                m1_bars=day_bars,
                spread=spread_val,
                slip=slip_val,
                point=point,
                trade_date=trade_date,
                year=year,
            )
            pnl_sells.append(res_sell["pnl_r"])

        pnl_buys_arr = np.array(pnl_buys, dtype=np.float64)
        pnl_sells_arr = np.array(pnl_sells, dtype=np.float64)

    n_trades = len(pnl_buys_arr)
    always_buy_exp = float(np.mean(pnl_buys_arr)) if n_trades > 0 else 0.0
    always_sell_exp = float(np.mean(pnl_sells_arr)) if n_trades > 0 else 0.0

    if n_trades == 0:
        return {
            "p_value": 1.0,
            "actual_expectancy": actual_expectancy,
            "n_exceed": 0,
            "n_simulations": n_simulations,
            "seed": seed,
            "always_buy_expectancy": 0.0,
            "always_sell_expectancy": 0.0,
            "sim_mean": 0.0,
            "sim_std": 0.0,
            "sim_p50": 0.0,
            "sim_p95": 0.0,
            "sim_expectancies": np.zeros(n_simulations, dtype=np.float64),
            "pnl_buys": np.array([], dtype=np.float64),
            "pnl_sells": np.array([], dtype=np.float64),
        }

    # Vectorized Monte Carlo permutation
    rng = np.random.default_rng(seed)
    # choices: ma trận (n_simulations, n_trades), nhận giá trị 0 (BUY) hoặc 1 (SELL) với p=0.5
    choices = rng.integers(0, 2, size=(n_simulations, n_trades))
    sim_pnls = np.where(choices == 0, pnl_buys_arr, pnl_sells_arr)
    sim_expectancies = np.mean(sim_pnls, axis=1)

    # p = (1 + số lần kỳ vọng ngẫu nhiên >= kỳ vọng thật) / (n_simulations + 1)
    n_exceed = int(np.sum(sim_expectancies >= actual_expectancy))
    p_value = float((1 + n_exceed) / (n_simulations + 1))

    return {
        "p_value": p_value,
        "actual_expectancy": actual_expectancy,
        "n_exceed": n_exceed,
        "n_simulations": n_simulations,
        "seed": seed,
        "always_buy_expectancy": always_buy_exp,
        "always_sell_expectancy": always_sell_exp,
        "sim_mean": float(np.mean(sim_expectancies)),
        "sim_std": float(np.std(sim_expectancies)),
        "sim_p50": float(np.median(sim_expectancies)),
        "sim_p95": float(np.percentile(sim_expectancies, 95)),
        "sim_expectancies": sim_expectancies,
        "pnl_buys": pnl_buys_arr,
        "pnl_sells": pnl_sells_arr,
    }


def evaluate_pass(
    trades: pd.DataFrame,
    m1: pd.DataFrame | M1Data | None = None,
    n_simulations: int = 1000,
    seed: int = 42,
    years: Sequence[int] = DEFAULT_DEVELOPMENT_YEARS,
    precomputed_pairs: tuple[np.ndarray, np.ndarray] | None = None,
) -> dict[str, Any]:
    """Đánh giá toàn diện 5 tiêu chí PASS trên lớp Development theo PROTOCOL.md mục 5.

    5 điều kiện bắt buộc:
    1. Tổng số lệnh >= 800.
    2. Kỳ vọng ròng >= +0,05R mỗi lệnh (sau chi phí).
    3. Profit factor >= 1,10.
    4. Kỳ vọng dương ở ít nhất 5 trên 8 năm lịch (2018-2025).
    5. Hơn kiểm soát ngẫu nhiên với p <= 0,05 (1.000 lần mô phỏng).

    Trả về:
        dict chi tiết chứa giá trị thực tế, ngưỡng chuẩn và cờ passed cho từng tiêu chí,
        kèm cờ tổng hợp 'all_passed' (chỉ True khi đúng CẢ 5 điều kiện).
    """
    summary_stats = summarize(trades)
    total_trades = summary_stats["total_trades"]
    expectancy = summary_stats["expectancy"]
    profit_factor = summary_stats["profit_factor"]

    # 4. Thống kê theo năm
    yearly_df = by_year(trades, years=years)
    n_pos_years = int(yearly_df["is_positive"].sum())
    total_years = len(years)

    # 5. Kiểm soát ngẫu nhiên
    if m1 is not None or precomputed_pairs is not None:
        rc_res = random_control(
            trades,
            m1=m1,
            n_simulations=n_simulations,
            seed=seed,
            precomputed_pairs=precomputed_pairs,
        )
        p_val = rc_res["p_value"]
        c5_passed = bool(p_val <= 0.05)
    else:
        rc_res = None
        p_val = None
        c5_passed = False

    c1_passed = bool(total_trades >= 800)
    c2_passed = bool(expectancy >= 0.05)
    c3_passed = bool(profit_factor >= 1.10)
    c4_passed = bool(n_pos_years >= 5)

    all_passed = bool(c1_passed and c2_passed and c3_passed and c4_passed and c5_passed)

    return {
        "all_passed": all_passed,
        "c1_total_trades": {
            "description": "Tổng số lệnh >= 800",
            "actual": total_trades,
            "threshold": 800,
            "passed": c1_passed,
        },
        "c2_expectancy": {
            "description": "Kỳ vọng ròng >= +0.05R mỗi lệnh (sau chi phí)",
            "actual": expectancy,
            "threshold": 0.05,
            "passed": c2_passed,
        },
        "c3_profit_factor": {
            "description": "Profit factor >= 1.10",
            "actual": profit_factor,
            "threshold": 1.10,
            "passed": c3_passed,
        },
        "c4_positive_years": {
            "description": "Kỳ vọng dương ở ít nhất 5 trong 8 năm lịch (2018-2025)",
            "actual": n_pos_years,
            "threshold": 5,
            "total_years": total_years,
            "passed": c4_passed,
            "by_year": yearly_df,
        },
        "c5_random_control": {
            "description": "Hơn kiểm soát ngẫu nhiên với p <= 0.05 (1.000 lần mô phỏng)",
            "actual_p": p_val,
            "threshold": 0.05,
            "passed": c5_passed,
            "details": rc_res,
        },
        "summary": summary_stats,
    }


def evaluate_pass_robustness_holdout(trades: pd.DataFrame) -> dict[str, Any]:
    """Đánh giá tiêu chí PASS cho lớp Robustness và Holdout theo PROTOCOL.md mục 5.

    Theo PROTOCOL.md:
    "Lớp Robustness và Holdout (mỗi lớp chạy một lần): cần đạt điều kiện 2 và 3.
    FAIL sát nút vẫn là FAIL, không tự nới ngưỡng sau khi thấy kết quả."

    2 điều kiện bắt buộc:
    - Điều kiện 2: Kỳ vọng ròng >= +0,05R mỗi lệnh (sau chi phí).
    - Điều kiện 3: Profit factor >= 1,10.

    Tham số:
        trades: DataFrame chứa các lệnh giao dịch đã mô phỏng.

    Trả về:
        dict chi tiết chứa:
        - all_passed: bool (True khi đạt cả điều kiện 2 và 3)
        - c2_expectancy: dict
        - c3_profit_factor: dict
        - summary: dict
    """
    summary_stats = summarize(trades)
    expectancy = summary_stats["expectancy"]
    profit_factor = summary_stats["profit_factor"]

    c2_passed = bool(expectancy >= 0.05)
    c3_passed = bool(profit_factor >= 1.10)
    all_passed = bool(c2_passed and c3_passed)

    return {
        "all_passed": all_passed,
        "c2_expectancy": {
            "description": "Kỳ vọng ròng >= +0.05R mỗi lệnh (sau chi phí)",
            "actual": expectancy,
            "threshold": 0.05,
            "passed": c2_passed,
        },
        "c3_profit_factor": {
            "description": "Profit factor >= 1.10",
            "actual": profit_factor,
            "threshold": 1.10,
            "passed": c3_passed,
        },
        "summary": summary_stats,
    }
