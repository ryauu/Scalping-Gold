"""Vẽ đồ thị kiểm tra trực quan Swing High / Swing Low trên nến H1 (PROTOCOL.md mục 7 & SMC_SPEC.md).

Vẽ 4 tuần nến H1 (tháng 01/2020) kèm:
- Nến H1 Candlestick.
- Đỉnh Swing High (marker tam giác xanh lá trên đỉnh nến).
- Đáy Swing Low (marker tam giác đỏ dưới đáy nến).
- Mũi tên / đường nét đứt nối từ đỉnh/đáy đến time_available để thấy rõ độ trễ k nến đóng cửa (chống look-ahead).
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
from src.backtest.smc.swings import detect_swings


def main():
    # Nạp 1 tháng nến H1 (2020-01-01 -> 2020-02-01)
    df_h1 = load_h1("2020-01-01", "2020-02-01")
    swings = detect_swings(df_h1, k=3)

    fig = go.Figure()

    # 1. Nến H1
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

    # 2. Swing Highs
    highs = swings[swings["swing_type"] == "HIGH"]
    fig.add_trace(go.Scatter(
        x=highs["time_candle"],
        y=highs["price"],
        mode="markers+text",
        marker=dict(symbol="triangle-down", size=12, color="#2e7d32"),
        text=[f"SH {p:.1f}" for p in highs["price"]],
        textposition="top center",
        name="Swing High (k=3)",
    ))

    # 3. Swing Lows
    lows = swings[swings["swing_type"] == "LOW"]
    fig.add_trace(go.Scatter(
        x=lows["time_candle"],
        y=lows["price"],
        mode="markers+text",
        marker=dict(symbol="triangle-up", size=12, color="#c62828"),
        text=[f"SL {p:.1f}" for p in lows["price"]],
        textposition="bottom center",
        name="Swing Low (k=3)",
    ))

    # 4. Vẽ các đường gióng ngang từ điểm tạo swing đến time_available
    for _, sw in swings.head(20).iterrows():
        t_c = sw["time_candle"]
        t_a = sw["time_available"]
        p = sw["price"]
        color = "#81c784" if sw["swing_type"] == "HIGH" else "#e57373"
        fig.add_shape(
            type="line",
            x0=t_c,
            x1=t_a,
            y0=p,
            y1=p,
            line=dict(color=color, width=1.5, dash="dot"),
        )
        # Điểm xác nhận
        fig.add_trace(go.Scatter(
            x=[t_a],
            y=[p],
            mode="markers",
            marker=dict(symbol="circle-open", size=7, color=color),
            showlegend=False,
            hoverinfo="text",
            hovertext=f"Khả dụng lúc: {t_a}",
        ))

    fig.update_layout(
        title="<b>Kiểm tra trực quan Swing High / Swing Low trên H1 (Tháng 01/2020)</b><br>Đường chấm thể hiện độ trễ xác nhận k=3 nến đóng cửa (time_available)",
        xaxis_title="Thời gian (UTC)",
        yaxis_title="Giá GOLD (USD)",
        xaxis_rangeslider_visible=False,
        template="plotly_white",
        height=750,
    )

    out_path = _REPO_ROOT / "artifacts" / "swings_h1_sample.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out_path), include_plotlyjs="cdn")
    print(f"Exported swings plot to: {out_path}")


if __name__ == "__main__":
    main()
