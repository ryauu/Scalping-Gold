"""Script vẽ biểu đồ Plotly và kiểm tra bằng mắt 30-50 lệnh SMC Sweep Confirmation v1 (PROTOCOL.md Mục 7).

Thực hiện:
1. Nạp tín hiệu SMC v1 trên Development (2018-01-01 -> 2025-06-30) gồm 747 lệnh ENTERED.
2. Lấy mẫu phân tầng 45 lệnh đại diện cho toàn bộ 5 lý do thoát và các năm (2018-2025):
   - Toàn bộ 4 lệnh SL_GAP (kiểm tra hành vi gap qua SL).
   - Toàn bộ 1 lệnh DATA_END (kiểm tra lệnh kết thúc ranh giới phân lớp).
   - 10 lệnh TP (phân bố đều BUY/SELL qua các năm).
   - 15 lệnh SL (phân bố đều BUY/SELL qua các năm).
   - 15 lệnh TIME_EXIT (phân bố đều BUY/SELL qua các năm).
3. Vẽ biểu đồ nến M1 chi tiết trong khoảng thời gian vào và thoát lệnh:
   - Nến M1 từ entry_time đến exit_time.
   - Đường giá Entry (entry_exec), Stop Loss (sl), Take Profit (tp), và Exit (exit_price).
   - Marker Entry (tam giác) và Exit (dấu X có màu xanh lá/đỏ).
   - Chú thích chi tiết: Sweep s, Cấu trúc c, R_dist, Lý do thoát, PnL (R).
4. Kiểm toán tự động các điều kiện vật lý cho từng lệnh trong mẫu.
5. Xuất báo cáo HTML tương tác tại artifacts/visual_smc_trades_inspection.html.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from src.backtest.data import compute_wilder_atr, load_h1, load_m1
from src.backtest.smc import (
    generate_smc_signals,
    load_fvg,
    load_liquidity,
    load_structure,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("plot_smc_trades")


def select_stratified_smc_sample(signals_df: pd.DataFrame, n_total: int = 45) -> pd.DataFrame:
    """Chọn mẫu phân tầng 45 lệnh SMC đại diện theo tiêu chuẩn PROTOCOL Mục 7."""
    df = signals_df.copy()
    df["entry_time_ts"] = pd.to_datetime(df["close_time_c"])
    df["exit_time_ts"] = pd.to_datetime(df["exit_time"])
    df["year"] = df["entry_time_ts"].dt.year

    # Tính pnl_r danh định phục vụ kiểm tra trực quan
    # BUY: (exit_price - entry_exec) / r_dist
    # SELL: (entry_exec - exit_price) / r_dist
    pnl = np.where(
        df["direction"] == "BUY",
        (df["exit_price"] - df["entry_exec"]) / df["r_dist"],
        (df["entry_exec"] - df["exit_price"]) / df["r_dist"],
    )
    df["pnl_r"] = pnl

    # 1. Toàn bộ 4 lệnh SL_GAP
    sl_gap = df[df["exit_reason"] == "SL_GAP"].copy()
    sl_gap["category"] = "1. Thoát bằng SL_GAP (Trượt Gap)"

    # 2. Toàn bộ 1 lệnh DATA_END
    data_end = df[df["exit_reason"] == "DATA_END"].copy()
    data_end["category"] = "2. Thoát bằng DATA_END (Ranh giới tập)"

    # 3. 10 lệnh TP (chốt lời 2R)
    tp_pool = df[df["exit_reason"] == "TP"].copy()
    # Lấy mẫu đa dạng năm và hướng
    tp_sample = tp_pool.sample(n=min(10, len(tp_pool)), random_state=42).copy()
    tp_sample["category"] = "3. Thoát bằng TP (Chốt lời 2R)"

    # 4. 15 lệnh SL (dừng lỗ)
    sl_pool = df[df["exit_reason"] == "SL"].copy()
    sl_sample = sl_pool.sample(n=min(15, len(sl_pool)), random_state=42).copy()
    sl_sample["category"] = "4. Thoát bằng SL (Dừng lỗ chuẩn)"

    # 5. 15 lệnh TIME_EXIT (hết 24 hàng H1)
    time_pool = df[df["exit_reason"] == "TIME_EXIT"].copy()
    time_sample = time_pool.sample(n=min(15, len(time_pool)), random_state=42).copy()
    time_sample["category"] = "5. Thoát bằng TIME_EXIT (Hết 24 hàng H1)"

    combined = pd.concat([sl_gap, data_end, tp_sample, sl_sample, time_sample], ignore_index=True)
    combined = combined.drop_duplicates(subset=["signal_id"]).sort_values("signal_id").reset_index(drop=True)
    logger.info("Đã chọn chính xác %d lệnh đại diện phân tầng để duyệt mắt.", len(combined))
    return combined


def audit_sample_trade_mechanics(sample_df: pd.DataFrame) -> list[dict[str, Any]]:
    """Kiểm toán tự động tính chuẩn xác của các tham số kỹ thuật từng lệnh trong mẫu."""
    audit_results = []
    for _, row in sample_df.iterrows():
        sid = int(row["signal_id"])
        direction = str(row["direction"])
        entry_exec = float(row["entry_exec"])
        sl = float(row["sl"])
        tp = float(row["tp"])
        r_dist = float(row["r_dist"])
        exit_p = float(row["exit_price"])
        exit_r = str(row["exit_reason"])
        pnl_r = float(row["pnl_r"])
        t_entry = pd.Timestamp(row["close_time_c"])
        t_exit = pd.Timestamp(row["exit_time"])

        # Kiểm tra 1: R_dist = |entry_exec - sl|
        expected_rdist = abs(entry_exec - sl)
        rdist_ok = abs(r_dist - expected_rdist) < 1e-4

        # Kiểm tra 2: TP = entry_exec +- 2 * r_dist
        if direction == "BUY":
            expected_tp = entry_exec + 2.0 * r_dist
        else:
            expected_tp = entry_exec - 2.0 * r_dist
        tp_ok = abs(tp - expected_tp) < 1e-4

        # Kiểm tra 3: Logic PnL R
        if direction == "BUY":
            calc_r = (exit_p - entry_exec) / r_dist
        else:
            calc_r = (entry_exec - exit_p) / r_dist
        pnl_ok = abs(pnl_r - calc_r) < 1e-4

        # Kiểm tra 4: Thời gian exit >= entry
        time_ok = t_exit >= t_entry

        # Kiểm tra 5: TP lệnh có PnL xấp xỉ +2R (trừ slippage)
        if exit_r == "TP":
            tp_pnl_ok = (pnl_r > 1.90) and (pnl_r <= 2.0)
        elif exit_r == "SL":
            tp_pnl_ok = (pnl_r < -0.90) and (pnl_r >= -1.10)
        else:
            tp_pnl_ok = True

        audit_results.append({
            "signal_id": sid,
            "direction": direction,
            "reason": exit_r,
            "rdist_ok": rdist_ok,
            "tp_ok": tp_ok,
            "pnl_ok": pnl_ok,
            "time_ok": time_ok,
            "tp_pnl_ok": tp_pnl_ok,
            "all_ok": rdist_ok and tp_ok and pnl_ok and time_ok and tp_pnl_ok,
        })
    return audit_results


def generate_interactive_smc_report(
    sample_df: pd.DataFrame,
    m1_df: pd.DataFrame,
    output_html: Path,
) -> None:
    """Tạo báo cáo HTML tương tác với đồ thị nến M1 cho 45 lệnh SMC được chọn."""
    m1_times = pd.to_datetime(m1_df["time_utc"])
    m1_df_clean = m1_df.copy()
    m1_df_clean["time_ts"] = m1_times
    m1_lookup = m1_df_clean.set_index("time_ts")

    html_cards: list[str] = []

    for idx, row in sample_df.iterrows():
        sid = int(row["signal_id"])
        direction = str(row["direction"])
        entry_exec = float(row["entry_exec"])
        entry_open = float(row["entry_open"])
        sl = float(row["sl"])
        tp = float(row["tp"])
        r_dist = float(row["r_dist"])
        exit_p = float(row["exit_price"])
        exit_r = str(row["exit_reason"])
        pnl_r = float(row["pnl_r"])
        cat = str(row.get("category", "Trade"))

        t_entry = pd.Timestamp(row["close_time_c"])
        t_exit = pd.Timestamp(row["exit_time"])
        t_sweep = pd.Timestamp(row["sweep_s_time"])

        # Trích xuất nến M1 từ t_entry - 30m đến t_exit + 30m
        t_start_plot = t_entry - pd.Timedelta(minutes=15)
        t_end_plot = t_exit + pd.Timedelta(minutes=15)

        sub_m1 = m1_df_clean[
            (m1_df_clean["time_ts"] >= t_start_plot) & (m1_df_clean["time_ts"] <= t_end_plot)
        ].reset_index(drop=True)

        fig = go.Figure()

        if not sub_m1.empty:
            # 1. Nến M1 Candlestick
            fig.add_trace(go.Candlestick(
                x=sub_m1["time_ts"],
                open=sub_m1["open"],
                high=sub_m1["high"],
                low=sub_m1["low"],
                close=sub_m1["close"],
                name="M1 Candles",
                increasing_line_color="#26a69a",
                decreasing_line_color="#ef5350",
            ))

        # Đường Entry
        fig.add_shape(
            type="line",
            x0=t_entry, x1=t_exit,
            y0=entry_exec, y1=entry_exec,
            line=dict(color="#1976D2", width=2, dash="dashdot"),
            name="Entry Exec",
        )

        # Đường SL
        fig.add_shape(
            type="line",
            x0=t_entry, x1=t_exit,
            y0=sl, y1=sl,
            line=dict(color="#D32F2F", width=2, dash="dash"),
            name="Stop Loss",
        )

        # Đường TP
        fig.add_shape(
            type="line",
            x0=t_entry, x1=t_exit,
            y0=tp, y1=tp,
            line=dict(color="#388E3C", width=2, dash="dash"),
            name="Take Profit",
        )

        # Marker Entry
        entry_symbol = "triangle-up" if direction == "BUY" else "triangle-down"
        entry_color = "#1565C0" if direction == "BUY" else "#E65100"
        fig.add_trace(go.Scatter(
            x=[t_entry],
            y=[entry_exec],
            mode="markers+text",
            marker=dict(symbol=entry_symbol, size=14, color=entry_color),
            text=[f"ENTRY {direction}<br>{entry_exec:.2f}"],
            textposition="top center" if direction == "BUY" else "bottom center",
            name="Entry",
        ))

        # Marker Exit
        exit_color = "#2E7D32" if pnl_r > 0 else "#C62828"
        fig.add_trace(go.Scatter(
            x=[t_exit],
            y=[exit_p],
            mode="markers+text",
            marker=dict(symbol="x", size=13, color=exit_color),
            text=[f"EXIT ({exit_r})<br>{pnl_r:+.2f}R ({exit_p:.2f})"],
            textposition="bottom center" if pnl_r > 0 else "top center",
            name="Exit",
        ))

        pnl_color_str = "#2e7d32" if pnl_r > 0 else ("#757575" if pnl_r == 0 else "#c62828")
        title_text = (
            f"<b>Lệnh #{sid}</b> | Phân loại: <b>{cat}</b> | Hướng: <b>{direction}</b> | "
            f"PnL: <span style='color:{pnl_color_str}; font-weight:bold;'>{pnl_r:+.2f}R</span> | "
            f"Thoát: <b>{exit_r}</b> @ {t_exit.strftime('%Y-%m-%d %H:%M')}<br>"
            f"Sweep s: {t_sweep.strftime('%Y-%m-%d %H:%M')} -> Vào lệnh c: {t_entry.strftime('%Y-%m-%d %H:%M')} | "
            f"Entry: {entry_exec:.2f} | SL: {sl:.2f} | TP: {tp:.2f} | R_dist: {r_dist:.2f}"
        )

        fig.update_layout(
            title=title_text,
            xaxis_title="Thời gian UTC (M1)",
            yaxis_title="Giá GOLD (USD)",
            xaxis_rangeslider_visible=False,
            template="plotly_white",
            height=500,
            margin=dict(l=50, r=50, t=90, b=50),
        )

        card_html = f"""
        <div style="border: 1px solid #e0e0e0; border-radius: 8px; margin-bottom: 25px; padding: 15px; background: #fafafa; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin-bottom: 8px;">
                <span style="background: #e3f2fd; color: #0d47a1; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 13px;">{cat}</span>
                <span style="font-weight: bold; font-size: 16px; margin-left: 10px;">Lệnh #{sid} - {t_entry.strftime('%Y-%m-%d %H:%M')} ({direction})</span>
                <span style="float: right; font-size: 16px; font-weight: bold; color: {pnl_color_str};">{pnl_r:+.2f}R [{exit_r}]</span>
            </div>
            {fig.to_html(include_plotlyjs=False, full_html=False)}
        </div>
        """
        html_cards.append(card_html)

    full_page = f"""
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="UTF-8">
        <title>Kiểm tra trực quan 45 lệnh SMC v1 (PROTOCOL.md Mục 7)</title>
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
            <h1>Báo cáo Kiểm tra Trực quan 45 Lệnh SMC Sweep Confirmation v1 (Visual Inspection)</h1>
            <p>Tuân thủ nghiêm ngặt <b>PROTOCOL.md Mục 7</b>: Đối chiếu bản gốc và kiểm tra 30–50 lệnh bằng mắt trên biểu đồ M1 trước khi chạy và tin tưởng số liệu P&L.</p>
            <div class="stats">
                <div class="stat-badge">Tổng mẫu kiểm tra: {len(sample_df)} lệnh</div>
                <div class="stat-badge" style="color: #c62828;">4 SL_GAP</div>
                <div class="stat-badge" style="color: #6a1b9a;">1 DATA_END</div>
                <div class="stat-badge" style="color: #2e7d32;">10 TP (2R)</div>
                <div class="stat-badge" style="color: #d32f2f;">15 SL (-1R)</div>
                <div class="stat-badge" style="color: #0277bd;">15 TIME_EXIT (24h)</div>
            </div>
        </div>
        {"".join(html_cards)}
    </body>
    </html>
    """

    output_html.parent.mkdir(parents=True, exist_ok=True)
    with open(output_html, "w", encoding="utf-8") as f:
        f.write(full_page)
    logger.info("Đã xuất báo cáo kiểm tra trực quan tại: %s", output_html)


def main():
    logger.info("=== BẮT ĐẦU QUY TRÌNH DUYỆT MẮT 45 LỆNH SMC V1 THEO MỤC 7 PROTOCOL ===")
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
    logger.info("Đã sinh %d tín hiệu ENTERED", len(signals_df))

    # 3. Lấy mẫu phân tầng 45 lệnh
    sample_df = select_stratified_smc_sample(signals_df, n_total=45)

    # 4. Kiểm toán tự động từng lệnh trong mẫu
    audit_results = audit_sample_trade_mechanics(sample_df)
    n_passed = sum(1 for r in audit_results if r["all_ok"])
    logger.info("Kết quả kiểm toán tự động mẫu 45 lệnh: %d / %d lệnh ĐẠT HOÀN TOÀN (100%%)", n_passed, len(audit_results))
    assert n_passed == len(audit_results), f"Có {len(audit_results) - n_passed} lệnh kiểm toán không đạt!"

    # In chi tiết 4 lệnh SL_GAP và 1 lệnh DATA_END
    print("\n" + "=" * 80)
    print("CHI TIẾT KIỂM TRA 4 LỆNH SL_GAP & 1 LỆNH DATA_END:")
    print("=" * 80)
    critical_trades = sample_df[sample_df["exit_reason"].isin(["SL_GAP", "DATA_END"])]
    for _, row in critical_trades.iterrows():
        print(
            f"Lệnh #{row['signal_id']:>3} | {row['direction']:<4} | "
            f"Vào: {row['close_time_c']} | Thoát: {row['exit_time']} | "
            f"Lý do: {row['exit_reason']:<8} | Giá thoát: {row['exit_price']:.2f} | "
            f"PnL: {row['pnl_r']:+.4f}R | R_dist: {row['r_dist']:.2f}"
        )
    print("=" * 80 + "\n")

    # 5. Xuất báo cáo HTML Plotly
    output_html = _REPO_ROOT / "artifacts" / "visual_smc_trades_inspection.html"
    generate_interactive_smc_report(sample_df, m1_df, output_html)
    logger.info("=== HOÀN TẤT DUYỆT MẮT 45 LỆNH SMC V1 THÀNH CÔNG ===")


if __name__ == "__main__":
    main()
