"""Script vẽ biểu đồ Plotly kiểm tra bằng mắt 30-50 lệnh theo PROTOCOL.md mục 7.

Lựa chọn mẫu đại diện kiểm tra:
- 15 lệnh thua lớn nhất (đặc biệt các ca SL, SL_GAP).
- 10 lệnh thắng lớn nhất (TP).
- 10 lệnh thoát theo thời gian (TIME lúc 16:00 UTC).
- Toàn bộ lệnh hết dữ liệu (DATA_END nếu có).
- 10 lệnh ngẫu nhiên khác để đa dạng hóa mẫu (tổng ~45-50 lệnh).

Mỗi biểu đồ thể hiện:
- Nến M1 từ 00:00 đến 17:00 UTC.
- Khối vùng Á [00:00, 07:00) từ asia_low đến asia_high (màu xanh nhạt).
- 2 đường buffer phá vỡ (± 0.1 ATR).
- Mũi tên / điểm vào lệnh (entry_time, entry_price).
- Đường SL (đỏ) và TP (xanh lá).
- Điểm thoát lệnh (exit_time, exit_price, exit_reason, pnl_r).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.backtest.data import DEFAULT_DB_PATH

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("plot_trades")


def select_sample_trades(trades_df: pd.DataFrame, n_total: int = 45) -> pd.DataFrame:
    """Chọn ra danh sách 30-50 lệnh đại diện kiểm tra theo quy chuẩn mục 7."""
    trades = trades_df.copy().reset_index(drop=True)
    trades["trade_id"] = trades.index

    # 1. 15 lệnh thua lớn nhất
    worst_losses = trades.sort_values("pnl_r", ascending=True).head(15).copy()
    worst_losses["category"] = "1. Thua lớn nhất (Worst Losses)"

    # 2. 10 lệnh thắng lớn nhất
    best_wins = trades.sort_values("pnl_r", ascending=False).head(10).copy()
    best_wins["category"] = "2. Thắng lớn nhất (Best Wins)"

    # 3. 10 lệnh thoát theo thời gian TIME (16:00)
    time_exits = trades[trades["exit_reason"] == "TIME"].head(10).copy()
    time_exits["category"] = "3. Thoát theo giờ 16:00 (TIME)"

    # 4. Toàn bộ lệnh DATA_END nếu có
    data_end = trades[trades["exit_reason"] == "DATA_END"].copy()
    if not data_end.empty:
        data_end["category"] = "4. Hết dữ liệu (DATA_END)"

    # 5. Các lệnh ngẫu nhiên bổ sung
    selected_ids = set(pd.concat([worst_losses, best_wins, time_exits, data_end])["trade_id"])
    remaining = trades[~trades["trade_id"].isin(selected_ids)]
    n_random = max(0, n_total - len(selected_ids))
    random_sample = remaining.sample(n=min(n_random, len(remaining)), random_state=42).copy()
    random_sample["category"] = "5. Mẫu ngẫu nhiên (Random Sample)"

    combined = pd.concat([worst_losses, best_wins, time_exits, data_end, random_sample], ignore_index=True)
    combined = combined.drop_duplicates(subset=["trade_id"]).reset_index(drop=True)
    logger.info("Đã chọn %d lệnh đại diện để vẽ biểu đồ kiểm tra bằng mắt.", len(combined))
    return combined


def load_day_m1_bars(date_str: str, db_path: Path = DEFAULT_DB_PATH) -> pd.DataFrame:
    """Nạp nến M1 trong ngày từ 00:00:00 đến 17:00:00 UTC."""
    start_ts = f"{date_str} 00:00:00"
    end_ts = f"{date_str} 17:00:00"
    query = """
        SELECT time_utc, open, high, low, close
        FROM m1_utc
        WHERE time_utc >= ? AND time_utc <= ?
        ORDER BY time_utc
    """
    with duckdb.connect(str(db_path), read_only=True) as con:
        df = con.execute(query, [start_ts, end_ts]).df()
    df["time_utc"] = pd.to_datetime(df["time_utc"])
    return df


def generate_interactive_trade_report(
    sampled_trades: pd.DataFrame,
    output_html: Path,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Tạo báo cáo HTML đa lệnh với menu dropdown tương tác chọn lệnh kiểm tra."""
    html_cards: list[str] = []

    for idx, trade in sampled_trades.iterrows():
        trade_id = int(trade["trade_id"])
        date_str = pd.Timestamp(trade["date"]).strftime("%Y-%m-%d")
        category = trade.get("category", "Trade")
        direction = str(trade["direction"]).upper()
        pnl_r = float(trade["pnl_r"])
        exit_reason = str(trade["exit_reason"])
        entry_time = pd.Timestamp(trade["entry_time"])
        exit_time = pd.Timestamp(trade["exit_time"])
        entry_price = float(trade["entry_price"])
        exit_price = float(trade["exit_price"])
        sl = float(trade["sl"])
        tp = float(trade["tp"])
        asia_high = float(trade["asia_high"]) if pd.notna(trade.get("asia_high")) else None
        asia_low = float(trade["asia_low"]) if pd.notna(trade.get("asia_low")) else None
        atr = float(trade["atr"]) if pd.notna(trade.get("atr")) else 0.0

        # Lấy dữ liệu nến M1
        df_bars = load_day_m1_bars(date_str, db_path=db_path)
        if df_bars.empty:
            continue
        df_bars["time_utc"] = pd.to_datetime(df_bars["time_utc"]).dt.tz_localize(None)

        entry_time_naive = entry_time.tz_localize(None) if entry_time.tzinfo else entry_time
        exit_time_naive = exit_time.tz_localize(None) if exit_time.tzinfo else exit_time

        fig = go.Figure()

        # 1. Nến M1
        fig.add_trace(go.Candlestick(
            x=df_bars["time_utc"],
            open=df_bars["open"],
            high=df_bars["high"],
            low=df_bars["low"],
            close=df_bars["close"],
            name="M1 Candles",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        ))

        # 2. Vùng Á (00:00 - 07:00)
        asia_start = pd.Timestamp(f"{date_str} 00:00:00")
        asia_end = pd.Timestamp(f"{date_str} 07:00:00")
        if asia_high is not None and asia_low is not None:
            fig.add_shape(
                type="rect",
                x0=asia_start,
                x1=asia_end,
                y0=asia_low,
                y1=asia_high,
                fillcolor="rgba(33, 150, 243, 0.15)",
                line=dict(color="#1976D2", width=1.5, dash="dot"),
                name="Asian Range",
            )
            # Ngưỡng phá vỡ ± 0.1 ATR
            buf = 0.1 * atr
            fig.add_shape(type="line", x0=asia_start, x1=pd.Timestamp(f"{date_str} 13:00:00"),
                          y0=asia_high + buf, y1=asia_high + buf,
                          line=dict(color="#673AB7", width=1, dash="dash"), name="Buy Threshold")
            fig.add_shape(type="line", x0=asia_start, x1=pd.Timestamp(f"{date_str} 13:00:00"),
                          y0=asia_low - buf, y1=asia_low - buf,
                          line=dict(color="#673AB7", width=1, dash="dash"), name="Sell Threshold")

        # 3. Đường SL và TP
        t_max_line = min(exit_time_naive + pd.Timedelta(minutes=30), pd.Timestamp(f"{date_str} 16:30:00"))
        fig.add_shape(type="line", x0=entry_time_naive, x1=t_max_line, y0=sl, y1=sl,
                      line=dict(color="#d32f2f", width=2, dash="dash"), name="SL")
        fig.add_shape(type="line", x0=entry_time_naive, x1=t_max_line, y0=tp, y1=tp,
                      line=dict(color="#388e3c", width=2, dash="dash"), name="TP")

        # 4. Marker Entry
        entry_color = "#1565C0" if direction == "BUY" else "#E65100"
        fig.add_trace(go.Scatter(
            x=[entry_time_naive],
            y=[entry_price],
            mode="markers+text",
            marker=dict(symbol="triangle-up" if direction == "BUY" else "triangle-down", size=14, color=entry_color),
            text=[f"ENTRY {direction}<br>{entry_price:.2f}"],
            textposition="top center" if direction == "BUY" else "bottom center",
            name="Entry",
        ))

        # 5. Marker Exit
        exit_marker_color = "#2e7d32" if pnl_r > 0 else "#c62828"
        fig.add_trace(go.Scatter(
            x=[exit_time_naive],
            y=[exit_price],
            mode="markers+text",
            marker=dict(symbol="x", size=12, color=exit_marker_color),
            text=[f"EXIT ({exit_reason})<br>{pnl_r:+.2f}R"],
            textposition="bottom center" if pnl_r > 0 else "top center",
            name="Exit",
        ))


        pnl_color_str = "#2e7d32" if pnl_r > 0 else "#c62828"
        title_text = (
            f"<b>Lệnh #{trade_id} ({category})</b> | Ngày: {date_str} | Hướng: {direction} | "
            f"PnL: <span style='color:{pnl_color_str}'>{pnl_r:+.2f}R</span> | "
            f"Thoát: {exit_reason} @ {exit_time.strftime('%H:%M')} | Giá: {entry_price:.2f} -> {exit_price:.2f}"
        )

        fig.update_layout(
            title=title_text,
            xaxis_title="Thời gian (UTC)",
            yaxis_title="Giá GOLD (USD)",
            xaxis_rangeslider_visible=False,
            template="plotly_white",
            height=600,
            margin=dict(l=50, r=50, t=80, b=50),
        )

        card_html = f"""
        <div style="border: 1px solid #e0e0e0; border-radius: 8px; margin-bottom: 30px; padding: 15px; background: #fafafa; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin-bottom: 10px;">
                <span style="background: #e3f2fd; color: #0d47a1; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 13px;">{category}</span>
                <span style="font-weight: bold; font-size: 16px; margin-left: 10px;">Lệnh #{trade_id} - {date_str} ({direction})</span>
                <span style="float: right; font-size: 16px; font-weight: bold; color: {pnl_color_str};">{pnl_r:+.2f}R ({exit_reason})</span>
            </div>
            {fig.to_html(include_plotlyjs="cdn" if idx == 0 else False, full_html=False)}
        </div>
        """
        html_cards.append(card_html)

    full_page = f"""
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="UTF-8">
        <title>Kiểm tra trực quan 30-50 lệnh (PROTOCOL.md Mục 7)</title>
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
            .stats {{ margin-top: 15px; display: flex; gap: 20px; font-weight: bold; }}
            .stat-badge {{ background: #eceff1; padding: 6px 12px; border-radius: 6px; font-size: 13px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Báo cáo kiểm tra trực quan 30-50 lệnh bằng mắt (Visual Inspection)</h1>
            <p>Tuân thủ nghiêm ngặt <b>PROTOCOL.md mục 7</b>: Đối chiếu bản gốc và kiểm tra 30-50 lệnh bằng mắt trên biểu đồ M1 trước khi tin tưởng số liệu thống kê.</p>
            <div class="stats">
                <span class="stat-badge">Tổng số lệnh kiểm tra: {len(sampled_trades)} lệnh</span>
                <span class="stat-badge">Lớp dữ liệu: Development (2018-01-01 -> 2025-06-30)</span>
                <span class="stat-badge">Quy ước khớp lệnh: C3 (Spread trung vị năm x 1.25, Slip 5 points)</span>
            </div>
        </div>
        <div>
            {''.join(html_cards)}
        </div>
    </body>
    </html>
    """

    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(full_page, encoding="utf-8")
    logger.info("Đã xuất thành công báo cáo trực quan ra file HTML: %s", output_html)


def main():
    trades_path = _REPO_ROOT / "artifacts" / "trades_development.parquet"
    if not trades_path.exists():
        trades_path = _REPO_ROOT / "artifacts" / "trades_development.csv"
        if not trades_path.exists():
            logger.error("Không tìm thấy file kết quả trades tại artifacts/. Hãy chạy pipeline_development.py trước!")
            return
        trades_df = pd.read_csv(trades_path)
    else:
        trades_df = pd.read_parquet(trades_path)

    sampled = select_sample_trades(trades_df, n_total=45)
    out_html = _REPO_ROOT / "artifacts" / "visual_inspection_trades.html"
    generate_interactive_trade_report(sampled, output_html=out_html)


if __name__ == "__main__":
    main()
