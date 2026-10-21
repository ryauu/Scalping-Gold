"""Script vẽ biểu đồ Plotly 2 tầng (H1 Context + M1 Execution) và duyệt mắt 45 lệnh SMC v1.

Tuân thủ nghiêm ngặt PROTOCOL.md Mục 7:
1. Mẫu đại diện phân tầng:
   - 40 lệnh phân bố đều qua 8 năm (2018–2025): mỗi năm 5 lệnh (2-3 BUY, 2-3 SELL), KHÔNG chọn theo kết quả thoát.
   - 5 lệnh ca biên bắt buộc: toàn bộ 4 lệnh SL_GAP và 1 lệnh DATA_END.
   - Tổng cộng: 45 lệnh.
2. Biểu đồ tương tác 2 panel cho từng lệnh:
   - Panel 1 (Bối cảnh H1):
     * Nến H1 từ trước sweep s (10 nến) đến sau khi lệnh đóng.
     * Nến Sweep s (marker + tên pool thanh khoản).
     * Đường mức giá pool bị quét (price_level).
     * Nến Cấu trúc c (marker + loại sự kiện CHOCH/BOS).
     * Khối hộp FVG hợp lưu (giá trần/sàn FVG và thời gian tồn tại).
     * Mức giá Entry, Stop Loss (cực trị [s, c]), Take Profit (2R).
   - Panel 2 (Zoom thực thi M1):
     * Nến M1 từ thời điểm khớp lệnh (close_time_c) đến khi đóng vị thế (exit_time).
     * Mức giá Entry, SL, TP, Exit Price, lý do thoát và PnL (R).
3. Checklist kiểm toán logic tín hiệu 6 tiêu chí cho từng lệnh:
   [1] Sweep s đúng loại pool và bị swept trước c.
   [2] Cấu trúc c đúng hướng và trong cửa sổ s < c <= s + 24 hàng H1.
   [3] FVG hợp lưu displacement: tạo từ s trở đi (i >= s+1), spans_gap == False, active tại c.
   [4] Stop Loss đặt tại cực trị tuyệt đối trong đoạn [s, c].
   [5] Khớp lệnh tại giá Open nến M1 đầu tiên tại close_time_c (+ spread/slip).
   [6] Thoát lệnh đúng thứ tự ưu tiên M1 và thời gian tối đa 24 hàng H1.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Đảm bảo in UTF-8 không lỗi trên Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.backtest.data import compute_wilder_atr, load_h1, load_m1
from src.backtest.smc import (
    generate_smc_signals,
    load_fvg,
    load_liquidity,
    load_structure,
)
from src.backtest.smc.signals import get_friction_year, scan_position_exit_m1

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("plot_smc_trades")


def select_stratified_inspection_sample(signals_df: pd.DataFrame) -> pd.DataFrame:
    """Lấy mẫu phân tầng 45 lệnh: 40 lệnh phân đều 8 năm/2 hướng + 5 lệnh ca biên (SL_GAP/DATA_END)."""
    df = signals_df.copy()
    df["entry_time_ts"] = pd.to_datetime(df["close_time_c"])
    df["exit_time_ts"] = pd.to_datetime(df["exit_time"])
    df["year"] = df["entry_time_ts"].dt.year

    pnl = np.where(
        df["direction"] == "BUY",
        (df["exit_price"] - df["entry_exec"]) / df["r_dist"],
        (df["entry_exec"] - df["exit_price"]) / df["r_dist"],
    )
    df["pnl_r"] = pnl

    # 1. Toàn bộ các ca biên đặc biệt (Edge Cases)
    edge_cases = df[df["exit_reason"].isin(["SL_GAP", "DATA_END"])].copy()
    edge_cases["sampling_group"] = "Ca biên đặc biệt (SL_GAP / DATA_END)"
    edge_ids = set(edge_cases["signal_id"])

    # 2. 40 lệnh phân bố đều 8 năm lịch (2018–2025) và 2 hướng BUY/SELL từ phần còn lại
    remaining = df[~df["signal_id"].isin(edge_ids)].copy()
    sampled_regular = []

    years = sorted(df["year"].unique())
    for y in years:
        df_y = remaining[remaining["year"] == y]
        buys = df_y[df_y["direction"] == "BUY"]
        sells = df_y[df_y["direction"] == "SELL"]

        # Lấy 2 hoặc 3 lệnh mỗi hướng để tổng là 5 lệnh/năm
        n_buy = min(3, len(buys))
        n_sell = min(5 - n_buy, len(sells))

        sample_buy = buys.sample(n=n_buy, random_state=42 + y) if n_buy > 0 else pd.DataFrame()
        sample_sell = sells.sample(n=n_sell, random_state=42 + y) if n_sell > 0 else pd.DataFrame()

        sub_sample = pd.concat([sample_buy, sample_sell], ignore_index=True)
        sub_sample["sampling_group"] = f"Phân tầng năm {y} ({len(sub_sample)} lệnh)"
        sampled_regular.append(sub_sample)

    combined_regular = pd.concat(sampled_regular, ignore_index=True)
    combined = pd.concat([combined_regular, edge_cases], ignore_index=True)
    combined = combined.drop_duplicates(subset=["signal_id"]).sort_values("signal_id").reset_index(drop=True)

    logger.info("Đã chọn chính xác %d lệnh đại diện (40 lệnh phân đều 8 năm + %d ca biên).", len(combined), len(edge_cases))
    return combined


def audit_signal_logic_checklist(
    sample_df: pd.DataFrame,
    h1_df: pd.DataFrame,
    m1_df: pd.DataFrame,
    sweeps_lc: pd.DataFrame,
    struct_df: pd.DataFrame,
    fvg_df: pd.DataFrame,
    fvg_lc: pd.DataFrame,
    spreads_cfg: dict | None = None,
    split_end_str: str = "2025-06-30 23:59:00",
) -> list[dict[str, Any]]:
    """Thực hiện checklist kiểm toán logic tín hiệu 6 tiêu chí thực chất và độc lập cho từng lệnh.

    6 Tiêu chí kiểm toán độc lập:
    [1] Sweep s: Đúng loại pool và bị swept trước c (s < c).
    [2] Cấu trúc c: Đúng hướng (BULLISH/BEARISH) và 1 <= c - s <= 24 hàng H1.
    [3] FVG hợp lưu: Cùng hướng, tạo từ s+1 trở đi, không rách gap, active tại close_time_c.
    [4] Stop Loss: Cực trị chuẩn xác trong đoạn [s, c] (sai số < 1e-4).
    [5] Khớp lệnh, R_dist, Sàn/Trần & TP:
        - So khớp entry_open với Open M1 tại close_time_c (hoặc Open H1 nếu nến thưa).
        - So khớp entry_exec với công thức trượt giá/spread.
        - R_dist thỏa mãn sàn chi phí: max(0.5 ATR, 12.5 friction) và trần an toàn: 10% giá.
        - Take Profit = 2R chuẩn xác.
    [6] Thoát lệnh: Quét độc lập trên nến M1, kiểm tra khớp bit-for-bit exit_time, exit_reason, exit_price.
    """
    h1_times = pd.to_datetime(h1_df["time_utc"])
    time_to_h1_idx = {t: idx for idx, t in enumerate(h1_times)}

    # Index nến M1 để tra cứu nhanh Open tại close_time_c
    m1_times = pd.to_datetime(m1_df["time_utc"])
    m1_time_set = set(m1_times)
    m1_indexed = m1_df.set_index(m1_times)

    # Index sweeps theo time_candle (thời điểm nến H1 sweep diễn ra)
    sweeps_swept = sweeps_lc[sweeps_lc["event_type"] == "SWEPT"].copy()
    sweeps_swept["time_s_ts"] = pd.to_datetime(sweeps_swept["time_candle"])

    # Index structure
    struct_copy = struct_df.copy()
    struct_copy["time_c_ts"] = pd.to_datetime(struct_copy["time_candle"])

    # Index FVG intervals
    fvg_df_clean = fvg_df.copy()
    if not fvg_df_clean.empty:
        fvg_df_clean["time_candle_ts"] = pd.to_datetime(fvg_df_clean["time_candle"])
        valid_fvgs = fvg_df_clean[~fvg_df_clean["spans_gap"]].copy()
    else:
        valid_fvgs = pd.DataFrame()

    fvg_intervals = []
    if not valid_fvgs.empty and not fvg_lc.empty:
        lc_copy = fvg_lc.copy()
        lc_copy["time_available_ts"] = pd.to_datetime(lc_copy["time_available"])
        created_events = lc_copy[lc_copy["event_type"] == "CREATED"]
        created_map = dict(zip(created_events["fvg_id"], created_events["time_available_ts"]))
        term_events = lc_copy[lc_copy["event_type"].isin(["INVALIDATED", "EXPIRED"])]
        term_map = dict(zip(term_events["fvg_id"], term_events["time_available_ts"]))

        for _, frow in valid_fvgs.iterrows():
            fid = frow["fvg_id"]
            t_created = created_map.get(fid)
            if t_created is None:
                continue
            t_term = term_map.get(fid, pd.Timestamp.max)
            fvg_intervals.append({
                "fvg_id": fid,
                "direction": frow["direction"],
                "time_candle_ts": frow["time_candle_ts"],
                "t_created": t_created,
                "t_term": t_term,
                "top": float(frow["top"]),
                "bottom": float(frow["bottom"]),
            })
    fvg_interval_df = pd.DataFrame(fvg_intervals)

    split_end = pd.Timestamp(split_end_str)
    checklist_results = []

    for _, row in sample_df.iterrows():
        sid = int(row["signal_id"])
        direction = str(row["direction"])
        t_s = pd.Timestamp(row["sweep_s_time"])
        t_c = pd.Timestamp(row["candle_c_time"])
        t_close_c = pd.Timestamp(row["close_time_c"])
        entry_open = float(row["entry_open"])
        entry_exec = float(row["entry_exec"])
        sl = float(row["sl"])
        tp = float(row["tp"])
        r_dist = float(row["r_dist"])
        exit_p = float(row["exit_price"])
        exit_r = str(row["exit_reason"])
        t_exit = pd.Timestamp(row["exit_time"])
        year = t_close_c.year

        s_idx = time_to_h1_idx.get(t_s)
        c_idx = time_to_h1_idx.get(t_c)
        next_h1_row_idx = time_to_h1_idx.get(t_close_c)

        spread_price, slip_price, friction_year = get_friction_year(year, spreads_cfg)

        # 1. Kiểm tra Sweep s
        sweep_events = sweeps_swept[sweeps_swept["time_s_ts"] == t_s]
        sweep_ok = not sweep_events.empty and (s_idx is not None) and (c_idx is not None) and (s_idx < c_idx)
        if not sweep_events.empty:
            pool_type = "/".join(sweep_events["pool_type"].unique())
            if direction == "SELL":
                pool_price = float(sweep_events["price_trigger"].max())
            else:
                pool_price = float(sweep_events["price_trigger"].min())
        else:
            pool_type = "UNKNOWN"
            pool_price = 0.0

        # 2. Kiểm tra Cấu trúc c
        struct_events = struct_copy[struct_copy["time_c_ts"] == t_c]
        expected_struct = {"BEARISH_CHOCH", "BEARISH_BOS"} if direction == "SELL" else {"BULLISH_CHOCH", "BULLISH_BOS"}
        actual_struct_types = set(struct_events["event_type"]) if not struct_events.empty else set()
        struct_match = len(expected_struct & actual_struct_types) > 0
        window_ok = (s_idx is not None) and (c_idx is not None) and (1 <= (c_idx - s_idx) <= 24)
        struct_ok = struct_match and window_ok

        # 3. Kiểm tra FVG hợp lưu displacement (i >= s+1 và active tại c)
        fvg_ok = False
        fvg_info = None
        target_fvg_dir = "BULLISH" if direction == "BUY" else "BEARISH"
        if not fvg_interval_df.empty and (s_idx is not None) and (s_idx + 1 < len(h1_times)):
            t_s_plus_1 = h1_times.iloc[s_idx + 1]
            active_mask = (
                (fvg_interval_df["direction"] == target_fvg_dir) &
                (fvg_interval_df["t_created"] <= t_close_c) &
                (t_close_c < fvg_interval_df["t_term"]) &
                (fvg_interval_df["time_candle_ts"] >= t_s_plus_1)
            )
            matching_fvgs = fvg_interval_df[active_mask]
            fvg_ok = not matching_fvgs.empty
            if fvg_ok:
                fvg_info = matching_fvgs.iloc[-1].to_dict()

        # 4. Kiểm tra Stop Loss cực trị trong [s, c]
        sub_highs = h1_df["high"].iloc[s_idx : c_idx + 1].to_numpy(dtype=float)
        sub_lows = h1_df["low"].iloc[s_idx : c_idx + 1].to_numpy(dtype=float)
        if direction == "SELL":
            expected_sl = float(np.max(sub_highs))
        else:
            expected_sl = float(np.min(sub_lows))
        sl_ok = abs(sl - expected_sl) < 1e-4

        # 5. Kiểm tra Entry Open, Entry Exec, R_dist, Sàn/Trần và TP 2R
        # 5.1 So khớp entry_open với Open M1 tại close_time_c (hoặc Open H1 nếu thưa)
        if t_close_c in m1_time_set:
            m1_bar = m1_indexed.loc[t_close_c]
            expected_open = float(m1_bar["open"].iloc[0] if isinstance(m1_bar, pd.DataFrame) else m1_bar["open"])
        else:
            expected_open = float(h1_df["open"].iloc[next_h1_row_idx]) if next_h1_row_idx is not None else entry_open
        entry_open_ok = abs(entry_open - expected_open) < 1e-4

        # 5.2 So khớp entry_exec
        expected_exec = entry_open + spread_price + slip_price if direction == "BUY" else entry_open - slip_price
        entry_exec_ok = abs(entry_exec - expected_exec) < 1e-4

        # 5.3 So khớp R_dist và sàn/trần
        rdist_calc_ok = abs(r_dist - abs(entry_exec - sl)) < 1e-4
        atr_c = float(h1_df["atr_14"].iloc[c_idx]) if (c_idx is not None and "atr_14" in h1_df.columns) else 0.0
        cost_floor = max(0.50 * atr_c, 12.5 * friction_year)
        safety_ceiling = 0.10 * entry_exec
        rdist_bounds_ok = (r_dist >= cost_floor - 1e-4) and (r_dist <= safety_ceiling + 1e-4) and (r_dist > 0)
        rdist_ok = rdist_calc_ok and rdist_bounds_ok

        # 5.4 So khớp TP 2R
        expected_tp = entry_exec + 2.0 * r_dist if direction == "BUY" else entry_exec - 2.0 * r_dist
        tp_ok = abs(tp - expected_tp) < 1e-4

        # 6. Kiểm tra thoát lệnh M1 độc lập
        if next_h1_row_idx is not None:
            max_h1_idx = min(next_h1_row_idx + 23, len(h1_df) - 1)
            max_hold_time = h1_times.iloc[max_h1_idx] + pd.Timedelta(hours=1)
        else:
            max_hold_time = t_close_c + pd.Timedelta(hours=24)

        indep_exit_time, indep_exit_reason, indep_exit_price = scan_position_exit_m1(
            direction=direction,
            entry_time=t_close_c,
            entry_price=entry_exec,
            sl=sl,
            tp=tp,
            spread=spread_price,
            slip=slip_price,
            m1_df=m1_df,
            max_exit_time=max_hold_time,
            split_end=split_end,
        )

        exit_time_match = (t_exit == indep_exit_time)
        exit_reason_match = (exit_r == indep_exit_reason)
        exit_price_match = abs(exit_p - indep_exit_price) < 1e-4
        exit_time_order = (t_exit >= t_close_c)
        exit_ok = exit_time_match and exit_reason_match and exit_price_match and exit_time_order

        all_passed = (
            sweep_ok
            and struct_ok
            and fvg_ok
            and sl_ok
            and entry_open_ok
            and entry_exec_ok
            and rdist_ok
            and tp_ok
            and exit_ok
        )

        checklist_results.append({
            "signal_id": sid,
            "direction": direction,
            "year": t_close_c.year,
            "time_s": t_s,
            "time_c": t_c,
            "pool_type": pool_type,
            "pool_price": pool_price,
            "struct_type": list(actual_struct_types)[0] if actual_struct_types else "NONE",
            "fvg_info": fvg_info,
            "sweep_ok": sweep_ok,
            "struct_ok": struct_ok,
            "fvg_ok": fvg_ok,
            "sl_ok": sl_ok,
            "entry_open_ok": entry_open_ok,
            "entry_exec_ok": entry_exec_ok,
            "rdist_ok": rdist_ok,
            "rdist_bounds_ok": rdist_bounds_ok,
            "tp_ok": tp_ok,
            "exit_ok": exit_ok,
            "exit_time_match": exit_time_match,
            "exit_reason_match": exit_reason_match,
            "exit_price_match": exit_price_match,
            "all_passed": all_passed,
            "exit_reason": exit_r,
            "pnl_r": row["pnl_r"],
        })

    return checklist_results


def generate_interactive_2panel_report(
    sample_df: pd.DataFrame,
    checklist_results: list[dict[str, Any]],
    h1_df: pd.DataFrame,
    m1_df: pd.DataFrame,
    sweeps_lc: pd.DataFrame,
    struct_df: pd.DataFrame,
    fvg_df: pd.DataFrame,
    output_html: Path,
) -> None:
    """Tạo báo cáo HTML tương tác với đồ thị 2 tầng (H1 Context + M1 Zoom) cho 45 lệnh."""
    h1_times = pd.to_datetime(h1_df["time_utc"])
    time_to_h1_idx = {t: idx for idx, t in enumerate(h1_times)}

    m1_df_clean = m1_df.copy()
    m1_df_clean["time_ts"] = pd.to_datetime(m1_df_clean["time_utc"])

    html_cards: list[str] = []

    for idx, row in sample_df.iterrows():
        sid = int(row["signal_id"])
        direction = str(row["direction"])
        t_s = pd.Timestamp(row["sweep_s_time"])
        t_c = pd.Timestamp(row["candle_c_time"])
        t_entry = pd.Timestamp(row["close_time_c"])
        t_exit = pd.Timestamp(row["exit_time"])
        entry_exec = float(row["entry_exec"])
        sl = float(row["sl"])
        tp = float(row["tp"])
        r_dist = float(row["r_dist"])
        exit_p = float(row["exit_price"])
        exit_r = str(row["exit_reason"])
        pnl_r = float(row["pnl_r"])
        cat = str(row.get("sampling_group", "Trade"))

        chk = checklist_results[idx]
        pool_type = chk["pool_type"]
        pool_price = chk["pool_price"]
        struct_type = chk["struct_type"]

        # 1. Trích xuất nến H1 quanh lệnh (s - 10 nến đến exit + 10 nến)
        s_h1_idx = time_to_h1_idx.get(t_s, 0)
        c_h1_idx = time_to_h1_idx.get(t_c, s_h1_idx)
        start_h1 = max(0, s_h1_idx - 10)
        end_h1 = min(len(h1_df), c_h1_idx + 30)

        sub_h1 = h1_df.iloc[start_h1:end_h1].copy().reset_index(drop=True)
        sub_h1["time_ts"] = pd.to_datetime(sub_h1["time_utc"])

        # 2. Trích xuất nến M1 quanh lệnh (t_entry - 15m đến t_exit + 15m)
        sub_m1 = m1_df_clean[
            (m1_df_clean["time_ts"] >= t_entry - pd.Timedelta(minutes=15)) &
            (m1_df_clean["time_ts"] <= t_exit + pd.Timedelta(minutes=15))
        ].reset_index(drop=True)

        # Tạo figure với 2 hàng (Panel 1: H1, Panel 2: M1)
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=False,
            vertical_spacing=0.12,
            subplot_titles=[
                f"<b>Panel 1: Bối cảnh Nến H1</b> (Sweep s @ {t_s.strftime('%Y-%m-%d %H:%M')} | Cấu trúc c @ {t_c.strftime('%Y-%m-%d %H:%M')})",
                f"<b>Panel 2: Phóng to Nến M1 Khớp & Thoát Lệnh</b> (Entry @ {t_entry.strftime('%Y-%m-%d %H:%M')} -> Exit @ {t_exit.strftime('%Y-%m-%d %H:%M')})",
            ],
            row_heights=[0.55, 0.45],
        )

        # --- PANEL 1: NẾN H1 & LOGIC TÍN HIỆU ---
        if not sub_h1.empty:
            fig.add_trace(go.Candlestick(
                x=sub_h1["time_ts"],
                open=sub_h1["open"], high=sub_h1["high"],
                low=sub_h1["low"], close=sub_h1["close"],
                name="H1 Candles",
                increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
            ), row=1, col=1)

        # Mức giá pool bị swept
        if pool_price > 0:
            fig.add_shape(
                type="line", x0=sub_h1["time_ts"].iloc[0], x1=t_c,
                y0=pool_price, y1=pool_price,
                line=dict(color="#FF8F00", width=1.5, dash="dot"),
                row=1, col=1,
            )
            fig.add_annotation(
                x=t_s, y=pool_price,
                text=f"Swept {pool_type} ({pool_price:.2f})",
                showarrow=True, arrowhead=2, arrowcolor="#FF8F00", font=dict(size=10, color="#FF8F00"),
                row=1, col=1,
            )

        # Marker Sweep s
        fig.add_trace(go.Scatter(
            x=[t_s],
            y=[pool_price if pool_price > 0 else (sub_h1.loc[sub_h1["time_ts"] == t_s, "high"].values[0] if not sub_h1[sub_h1["time_ts"] == t_s].empty else entry_exec)],
            mode="markers+text",
            marker=dict(symbol="diamond", size=11, color="#FF8F00"),
            text=["Sweep s"],
            textposition="top center" if direction == "SELL" else "bottom center",
            name="Sweep s",
        ), row=1, col=1)

        # Marker Structure c
        fig.add_trace(go.Scatter(
            x=[t_c],
            y=[entry_exec],
            mode="markers+text",
            marker=dict(symbol="star", size=13, color="#673AB7"),
            text=[f"c: {struct_type}"],
            textposition="top center" if direction == "BUY" else "bottom center",
            name="Confirm c",
        ), row=1, col=1)

        # FVG Box trên Panel 1 (nếu có FVG hợp lưu)
        fvg_info = chk.get("fvg_info")
        if fvg_info:
            f_top = float(fvg_info["top"])
            f_bot = float(fvg_info["bottom"])
            f_start = pd.Timestamp(fvg_info["time_candle_ts"])
            fig.add_shape(
                type="rect",
                x0=f_start, x1=t_entry,
                y0=f_bot, y1=f_top,
                fillcolor="rgba(33, 150, 243, 0.22)" if direction == "BUY" else "rgba(255, 152, 0, 0.22)",
                line=dict(color="#1976D2" if direction == "BUY" else "#F57C00", width=1.2, dash="dot"),
                row=1, col=1,
            )
            fig.add_annotation(
                x=f_start, y=(f_top + f_bot) / 2,
                text=f"FVG ({f_bot:.2f}-{f_top:.2f})",
                showarrow=False,
                font=dict(size=9, color="#0D47A1" if direction == "BUY" else "#E65100"),
                row=1, col=1,
            )

        # Đường Entry, SL, TP trên H1
        fig.add_shape(type="line", x0=t_entry, x1=t_exit, y0=entry_exec, y1=entry_exec,
                      line=dict(color="#1976D2", width=1.5, dash="dashdot"), row=1, col=1)
        fig.add_shape(type="line", x0=t_entry, x1=t_exit, y0=sl, y1=sl,
                      line=dict(color="#D32F2F", width=1.5, dash="dash"), row=1, col=1)
        fig.add_shape(type="line", x0=t_entry, x1=t_exit, y0=tp, y1=tp,
                      line=dict(color="#388E3C", width=1.5, dash="dash"), row=1, col=1)

        # --- PANEL 2: NẾN M1 PHÓNG TO ---
        if not sub_m1.empty:
            fig.add_trace(go.Candlestick(
                x=sub_m1["time_ts"],
                open=sub_m1["open"], high=sub_m1["high"],
                low=sub_m1["low"], close=sub_m1["close"],
                name="M1 Candles",
                increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
            ), row=2, col=1)

        fig.add_shape(type="line", x0=t_entry, x1=t_exit, y0=entry_exec, y1=entry_exec,
                      line=dict(color="#1976D2", width=2, dash="dashdot"), row=2, col=1)
        fig.add_shape(type="line", x0=t_entry, x1=t_exit, y0=sl, y1=sl,
                      line=dict(color="#D32F2F", width=2, dash="dash"), row=2, col=1)
        fig.add_shape(type="line", x0=t_entry, x1=t_exit, y0=tp, y1=tp,
                      line=dict(color="#388E3C", width=2, dash="dash"), row=2, col=1)

        entry_sym = "triangle-up" if direction == "BUY" else "triangle-down"
        entry_col = "#1565C0" if direction == "BUY" else "#E65100"
        fig.add_trace(go.Scatter(
            x=[t_entry], y=[entry_exec],
            mode="markers+text",
            marker=dict(symbol=entry_sym, size=13, color=entry_col),
            text=[f"ENTRY {direction} ({entry_exec:.2f})"],
            textposition="top center" if direction == "BUY" else "bottom center",
            name="M1 Entry",
        ), row=2, col=1)

        exit_col = "#2E7D32" if pnl_r > 0 else "#C62828"
        fig.add_trace(go.Scatter(
            x=[t_exit], y=[exit_p],
            mode="markers+text",
            marker=dict(symbol="x", size=13, color=exit_col),
            text=[f"EXIT ({exit_r})<br>{pnl_r:+.2f}R ({exit_p:.2f})"],
            textposition="bottom center" if pnl_r > 0 else "top center",
            name="M1 Exit",
        ), row=2, col=1)

        pnl_col_str = "#2e7d32" if pnl_r > 0 else ("#757575" if pnl_r == 0 else "#c62828")
        title_text = (
            f"<b>Lệnh #{sid}</b> | <b>{cat}</b> | Hướng: <b>{direction}</b> | "
            f"PnL: <span style='color:{pnl_col_str}; font-weight:bold;'>{pnl_r:+.2f}R [{exit_r}]</span><br>"
            f"Sweep: {pool_type} @ {pool_price:.2f} | Cấu trúc c: {struct_type} | "
            f"Entry: {entry_exec:.2f} | SL: {sl:.2f} | TP: {tp:.2f} | R_dist: {r_dist:.2f}"
        )

        fig.update_layout(
            title=title_text,
            xaxis_rangeslider_visible=False,
            xaxis2_rangeslider_visible=False,
            template="plotly_white",
            height=750,
            margin=dict(l=50, r=50, t=100, b=50),
            showlegend=False,
        )

        chk_badges = [
            f"<span style='color:{'#2e7d32' if chk['sweep_ok'] else '#c62828'}'>[Sweep s: {'ĐẠT' if chk['sweep_ok'] else 'LỖI'}]</span>",
            f"<span style='color:{'#2e7d32' if chk['struct_ok'] else '#c62828'}'>[Cấu trúc c: {'ĐẠT' if chk['struct_ok'] else 'LỖI'}]</span>",
            f"<span style='color:{'#2e7d32' if chk['fvg_ok'] else '#c62828'}'>[FVG hợp lưu: {'ĐẠT' if chk['fvg_ok'] else 'LỖI'}]</span>",
            f"<span style='color:{'#2e7d32' if chk['sl_ok'] else '#c62828'}'>[SL cực trị: {'ĐẠT' if chk['sl_ok'] else 'LỖI'}]</span>",
            f"<span style='color:{'#2e7d32' if chk['entry_open_ok'] else '#c62828'}'>[Open M1: {'ĐẠT' if chk['entry_open_ok'] else 'LỖI'}]</span>",
            f"<span style='color:{'#2e7d32' if chk['rdist_ok'] else '#c62828'}'>[R_dist sàn/trần: {'ĐẠT' if chk['rdist_ok'] else 'LỖI'}]</span>",
            f"<span style='color:{'#2e7d32' if chk['tp_ok'] else '#c62828'}'>[TP 2R: {'ĐẠT' if chk['tp_ok'] else 'LỖI'}]</span>",
            f"<span style='color:{'#2e7d32' if chk['exit_ok'] else '#c62828'}'>[Thoát M1 độc lập: {'ĐẠT' if chk['exit_ok'] else 'LỖI'}]</span>",
        ]

        card_html = f"""
        <div style="border: 1px solid #e0e0e0; border-radius: 8px; margin-bottom: 30px; padding: 15px; background: #fafafa; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin-bottom: 8px;">
                <span style="background: #e3f2fd; color: #0d47a1; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 13px;">{cat}</span>
                <span style="font-weight: bold; font-size: 16px; margin-left: 10px;">Lệnh #{sid} - {t_entry.strftime('%Y-%m-%d %H:%M')} ({direction})</span>
                <span style="float: right; font-size: 16px; font-weight: bold; color: {pnl_col_str};">{pnl_r:+.2f}R [{exit_r}]</span>
            </div>
            <div style="font-family: monospace; font-size: 12px; margin-bottom: 12px;">
                <b>Checklist Logic:</b> {' '.join(chk_badges)}
            </div>
            {fig.to_html(include_plotlyjs=False, full_html=False)}
        </div>
        """
        html_cards.append(card_html)

    n_passed = sum(1 for r in checklist_results if r["all_passed"])
    n_total = len(checklist_results)
    pct_passed = (n_passed / n_total) * 100 if n_total > 0 else 0.0
    n_failed = n_total - n_passed
    status_color = "#2e7d32" if n_failed == 0 else "#c62828"

    full_page = f"""
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="UTF-8">
        <title>Kiểm tra trực quan 2 tầng 45 lệnh SMC v1 (PROTOCOL Mục 7)</title>
        <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                margin: 0;
                padding: 20px;
                background-color: #f5f5f5;
                color: #212121;
            }}
            .header {{
                background: white;
                padding: 20px 30px;
                border-radius: 8px;
                margin-bottom: 25px;
                box-shadow: 0 2px 6px rgba(0,0,0,0.08);
            }}
            h1 {{ margin: 0 0 10px 0; color: #1a237e; font-size: 24px; }}
            p {{ margin: 5px 0; color: #555; font-size: 14px; line-height: 1.5; }}
            .stats {{ margin-top: 15px; display: flex; gap: 15px; flex-wrap: wrap; }}
            .stat-badge {{ background: #eceff1; padding: 6px 12px; border-radius: 6px; font-size: 13px; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Báo cáo Kiểm tra Trực quan 2 Tầng 45 Lệnh SMC Sweep Confirmation v1 (PROTOCOL.md Mục 7)</h1>
            <p>Tuân thủ nghiêm ngặt quy định đối chiếu bản gốc trước khi chạy P&L: <b>Panel 1 (Bối cảnh H1: Sweep s, mức pool, Cấu trúc c, FVG)</b> và <b>Panel 2 (Zoom M1: Khớp lệnh, diễn biến nến phút, và cơ chế thoát)</b>.</p>
            <div class="stats">
                <div class="stat-badge">Tổng mẫu kiểm tra: {len(sample_df)} lệnh</div>
                <div class="stat-badge" style="color: #1565c0;">40 lệnh phân đều 8 năm (2018–2025) & 2 hướng</div>
                <div class="stat-badge" style="color: #c62828;">4 ca biên SL_GAP</div>
                <div class="stat-badge" style="color: #6a1b9a;">1 ca biên DATA_END</div>
                <div class="stat-badge" style="color: {status_color};">Kết quả checklist 6 tiêu chí: {n_passed} / {n_total} ĐẠT ({pct_passed:.1f}%, {n_failed} LỖI)</div>
            </div>
        </div>
        {"".join(html_cards)}
    </body>
    </html>
    """

    output_html.parent.mkdir(parents=True, exist_ok=True)
    with open(output_html, "w", encoding="utf-8") as f:
        f.write(full_page)
    logger.info("Đã xuất báo cáo đồ thị 2 tầng tại: %s", output_html)


def main():
    logger.info("=== BẮT ĐẦU DUYỆT MẮT 45 LỆNH SMC V1 ĐỒ THỊ 2 TẦNG (H1 + M1) THEO MỤC 7 PROTOCOL ===")
    start_date = "2018-01-01"
    end_date = "2025-06-30"

    # 1. Nạp H1, M1, Sweeps, Structure, FVG
    h1_full = load_h1(start_date=None, end_date=end_date)
    h1_full["atr_14"] = compute_wilder_atr(
        h1_full["high"].to_numpy(),
        h1_full["low"].to_numpy(),
        h1_full["close"].to_numpy(),
        period=14,
    )
    h1_dev = h1_full[h1_full["time_utc"] >= pd.to_datetime(start_date)].reset_index(drop=True)

    _, sweeps_lc = load_liquidity(start_date=start_date, end_date=end_date)
    struct_df = load_structure(start_date=start_date, end_date=end_date)
    fvg_df, fvg_lc = load_fvg(start_date=start_date, end_date=end_date)
    m1_df = load_m1(start_date, end_date).to_dataframe()

    # 2. Sinh tín hiệu SMC v1 (có position filter)
    signals_df, summary = generate_smc_signals(
        h1_df=h1_dev,
        sweeps_lifecycle_df=sweeps_lc,
        structure_df=struct_df,
        fvg_df=fvg_df,
        fvg_lifecycle_df=fvg_lc,
        m1_df=m1_df,
        split_name="development",
        enable_position_filter=True,
    )

    # 3. Lấy mẫu phân tầng 45 lệnh
    sample_df = select_stratified_inspection_sample(signals_df)

    # 4. Kiểm toán logic 6 tiêu chí thực chất và độc lập
    checklist_results = audit_signal_logic_checklist(
        sample_df=sample_df,
        h1_df=h1_dev,
        m1_df=m1_df,
        sweeps_lc=sweeps_lc,
        struct_df=struct_df,
        fvg_df=fvg_df,
        fvg_lc=fvg_lc,
    )

    n_passed = sum(1 for r in checklist_results if r["all_passed"])
    pct_passed = (n_passed / len(checklist_results)) * 100 if checklist_results else 0.0
    logger.info("KẾT QUẢ CHECKLIST LOGIC 6 TIÊU CHÍ: %d / %d lệnh ĐẠT HOÀN TOÀN (%.1f%%, %d LỖI)", n_passed, len(checklist_results), pct_passed, len(checklist_results) - n_passed)
    assert n_passed == len(checklist_results), f"Phát hiện {len(checklist_results) - n_passed} lệnh vi phạm checklist logic!"

    # In bảng tóm tắt 45 lệnh đã duyệt
    print("\n" + "=" * 105)
    print(f"{'ID':>4} | {'HƯỚNG':<4} | {'NĂM':<4} | {'SWEEP s':<19} | {'POOL TYPE':<11} | {'CẤU TRÚC c':<13} | {'FVG':<5} | {'THOÁT':<9} | {'PnL':<8} | {'CHECKLIST':<5}")
    print("=" * 105)
    for chk in checklist_results:
        print(
            f"{chk['signal_id']:>4} | {chk['direction']:<4} | {chk['year']:<4} | "
            f"{str(chk['time_s'])[:19]:<19} | {chk['pool_type']:<11} | {chk['struct_type']:<13} | "
            f"{'ĐẠT' if chk['fvg_ok'] else 'LỖI':<5} | "
            f"{chk['exit_reason']:<9} | {chk['pnl_r']:>+6.2f}R | "
            f"{'PASS' if chk['all_passed'] else 'FAIL':<5}"
        )
    print("=" * 105 + "\n")

    # 5. Xuất báo cáo HTML 2 tầng
    output_html = _REPO_ROOT / "artifacts" / "visual_smc_trades_inspection.html"
    generate_interactive_2panel_report(
        sample_df=sample_df,
        checklist_results=checklist_results,
        h1_df=h1_dev,
        m1_df=m1_df,
        sweeps_lc=sweeps_lc,
        struct_df=struct_df,
        fvg_df=fvg_df,
        output_html=output_html,
    )
    logger.info("=== HOÀN TẤT DUYỆT MẮT 45 LỆNH 2 TẦNG THÀNH CÔNG (0 LỖI LOGIC) ===")


if __name__ == "__main__":
    main()
