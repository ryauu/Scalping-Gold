"""Vẽ đồ thị kiểm tra trực quan Cấu trúc thị trường (BOS & CHoCH) trên nến H1.

Theo PROTOCOL.md mục 7 & SMC_SPEC.md v2 mục 10.1 (Kiểm tra trực quan):
Vẽ 2 tuần nến H1 (02/01/2020 -> 17/01/2020) kèm:
- Nến H1 Candlestick.
- Đỉnh Swing High / Đáy Swing Low (k=3).
- Sự kiện BOS (tiếp diễn xu hướng) và CHoCH (đảo chiều xu hướng).
- Mức swing bị phá vỡ (đường gióng ngang từ swing đến nến phá vỡ).
- Đường chấm thể hiện độ trễ xác nhận (từ time_candle đến time_available = giờ đóng nến) chống look-ahead.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd
import plotly.graph_objects as go

from src.backtest.data import load_h1
from src.backtest.smc.structure import load_structure
from src.backtest.smc.swings import detect_swings


def main():
    start_date = "2020-01-02"
    end_date = "2020-01-17"

    # 1. Nạp nến H1 trong giai đoạn hiển thị
    df_h1 = load_h1(start_date, end_date)
    swings = detect_swings(df_h1, k=3)

    # 2. Nạp cấu trúc thị trường bằng load_structure (mặc định warmup_months=None để bảo toàn ký ức dài hạn)
    struct_df = load_structure(start_date, end_date, warmup_months=None, k=3)

    fig = go.Figure()

    # 1. Nến H1 Candlestick
    fig.add_trace(go.Candlestick(
        x=df_h1["time_utc"],
        open=df_h1["open"],
        high=df_h1["high"],
        low=df_h1["low"],
        close=df_h1["close"],
        name="H1 Candles",
        increasing_line_color="#26a69a",
        decreasing_line_color="#ef5350",
    ))

    # 2. Swing Highs & Lows
    highs = swings[swings["swing_type"] == "HIGH"]
    fig.add_trace(go.Scatter(
        x=highs["time_candle"],
        y=highs["price"],
        mode="markers",
        marker=dict(symbol="triangle-down", size=10, color="#2e7d32"),
        name="Swing High (k=3)",
        hoverinfo="text",
        hovertext=[f"Swing High: {p:.2f}<br>Tạo: {t}<br>Khả dụng: {a}" for p, t, a in zip(highs["price"], highs["time_candle"], highs["time_available"])],
    ))

    lows = swings[swings["swing_type"] == "LOW"]
    fig.add_trace(go.Scatter(
        x=lows["time_candle"],
        y=lows["price"],
        mode="markers",
        marker=dict(symbol="triangle-up", size=10, color="#c62828"),
        name="Swing Low (k=3)",
        hoverinfo="text",
        hovertext=[f"Swing Low: {p:.2f}<br>Tạo: {t}<br>Khả dụng: {a}" for p, t, a in zip(lows["price"], lows["time_candle"], lows["time_available"])],
    ))

    # 3. Vẽ các mức swing bị phá vỡ và đường xác nhận cấu trúc
    color_map = {
        "BULLISH_BOS": "#00e676",      # Xanh lá sáng
        "BEARISH_BOS": "#ff1744",      # Đỏ tươi
        "BULLISH_CHOCH": "#00e5ff",    # Cyan rực rỡ
        "BEARISH_CHOCH": "#ff9100",    # Cam đậm
    }

    symbol_map = {
        "BULLISH_BOS": "triangle-up",
        "BEARISH_BOS": "triangle-down",
        "BULLISH_CHOCH": "diamond",
        "BEARISH_CHOCH": "diamond",
    }

    for ev_type in ["BULLISH_BOS", "BEARISH_BOS", "BULLISH_CHOCH", "BEARISH_CHOCH"]:
        sub = struct_df[struct_df["event_type"] == ev_type]
        if sub.empty:
            continue

        hover_texts = []
        y_pos = []
        for _, r in sub.iterrows():
            hover_texts.append(
                f"<b>{r['event_type']}</b><br>"
                f"Nến phá vỡ: {r['time_candle']}<br>"
                f"Khả dụng lúc: {r['time_available']}<br>"
                f"Mức bị phá: {r['price_broken']:.2f}<br>"
                f"Đóng cửa: {r['close_price']:.2f}<br>"
                f"Xu hướng: {r['prior_trend']} &rarr; {r['new_trend']}"
            )
            # Đặt marker trên high hoặc dưới low để dễ nhìn
            if "BULLISH" in ev_type:
                y_pos.append(r["close_price"] + 2.0)
            else:
                y_pos.append(r["close_price"] - 2.0)

        fig.add_trace(go.Scatter(
            x=sub["time_candle"],
            y=y_pos,
            mode="markers+text",
            marker=dict(
                symbol=symbol_map[ev_type],
                size=13,
                color=color_map[ev_type],
                line=dict(width=1.5, color="#ffffff"),
            ),
            text=[ev_type.replace("BULLISH_", "").replace("BEARISH_", "") for _ in range(len(sub))],
            textposition="top center" if "BULLISH" in ev_type else "bottom center",
            textfont=dict(size=10, color=color_map[ev_type]),
            hoverinfo="text",
            hovertext=hover_texts,
            name=ev_type,
        ))

    # 4. Vẽ đường nét đứt biểu diễn mức giá bị phá vỡ và độ trễ time_available
    for _, r in struct_df.iterrows():
        p_broken = r["price_broken"]
        t_break = r["time_candle"]
        t_avail = r["time_available"]
        ev_type = r["event_type"]
        c = color_map.get(ev_type, "#ffffff")

        # Đường chấm ngang biểu diễn mức swing bị phá tại nến phá vỡ
        fig.add_shape(
            type="line",
            x0=t_break - pd.Timedelta(hours=4),
            x1=t_break,
            y0=p_broken,
            y1=p_broken,
            line=dict(color=c, width=1.5, dash="dash"),
        )

        # Đường gióng dọc từ time_candle đến time_available thể hiện độ trễ đóng nến (Zero Look-Ahead)
        fig.add_shape(
            type="line",
            x0=t_break,
            x1=t_avail,
            y0=r["close_price"],
            y1=r["close_price"],
            line=dict(color=c, width=2, dash="dot"),
        )
        fig.add_trace(go.Scatter(
            x=[t_avail],
            y=[r["close_price"]],
            mode="markers",
            marker=dict(symbol="circle-open", size=6, color=c),
            showlegend=False,
            hoverinfo="text",
            hovertext=f"Tín hiệu {ev_type} chính thức khả dụng lúc đóng nến: {t_avail}",
        ))

    fig.update_layout(
        title=f"Kiểm tra trực quan Cấu trúc SMC (BOS & CHoCH) trên H1 GOLD ({start_date} -> {end_date})",
        xaxis_title="Thời gian (UTC)",
        yaxis_title="Giá Vàng (USD)",
        template="plotly_dark",
        xaxis_rangeslider_visible=False,
        height=800,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    out_path = _REPO_ROOT / "artifacts" / "structure_h1_sample.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out_path))
    print(f"Da xuat bieu do truc quan tai: {out_path}")


if __name__ == "__main__":
    main()
