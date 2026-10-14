"""Vẽ đồ thị kiểm tra trực quan Fair Value Gap (FVG) và Vòng đời trên nến H1.

Theo PROTOCOL.md mục 7 & SMC_SPEC.md v2.2 mục 10.1:
Vẽ 2 tuần nến H1 (02/01/2020 -> 17/01/2020) kèm:
- Nến H1 Candlestick.
- Các vùng FVG (vùng hình chữ nhật Bullish màu xanh ngọc, Bearish màu đỏ nhạt).
- Sự kiện vòng đời (CREATED, TESTED, INVALIDATED, EXPIRED) gắn trên các nến tương ứng.
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
from src.backtest.smc.fvg import load_fvg


def main():
    start_date = "2020-01-02"
    end_date = "2020-01-17"

    # 1. Nạp nến H1 hiển thị
    df_h1 = load_h1(start_date, end_date)

    # 2. Nạp FVG và nhật ký vòng đời (mặc định warmup_months=None để bảo toàn ATR chuẩn xác)
    fvg_df, lc_df = load_fvg(start_date, end_date, warmup_months=None, gamma=0.15, m=24)

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

    # 2. Vẽ các vùng FVG (Shaded Rectangles)
    for _, fvg in fvg_df.iterrows():
        fid = int(fvg["fvg_id"])
        direction = str(fvg["direction"])
        t_create = fvg["time_candle"]
        top = float(fvg["top"])
        bottom = float(fvg["bottom"])
        spans_gap = bool(fvg["spans_gap"])

        # Tìm thời điểm kết thúc trong lifecycle
        fvg_events = lc_df[lc_df["fvg_id"] == fid]
        term_event = fvg_events[fvg_events["event_type"].isin(["INVALIDATED", "EXPIRED"])]

        if not term_event.empty:
            t_end = term_event.iloc[0]["time_candle"]
        else:
            t_end = df_h1["time_utc"].max()

        # Màu sắc vùng FVG
        if spans_gap:
            fill_color = "rgba(158, 158, 158, 0.15)"  # Xám nếu vắt qua cuối tuần
            line_color = "rgba(158, 158, 158, 0.4)"
        elif direction == "BULLISH":
            fill_color = "rgba(38, 166, 154, 0.20)"  # Xanh ngọc
            line_color = "rgba(38, 166, 154, 0.6)"
        else:
            fill_color = "rgba(239, 83, 80, 0.20)"   # Đỏ cam
            line_color = "rgba(239, 83, 80, 0.6)"

        fig.add_shape(
            type="rect",
            x0=t_create,
            x1=t_end,
            y0=bottom,
            y1=top,
            fillcolor=fill_color,
            line=dict(color=line_color, width=1, dash="dot" if spans_gap else "solid"),
        )

    # 3. Vẽ các sự kiện vòng đời (TESTED, INVALIDATED, EXPIRED)
    tested_events = lc_df[lc_df["event_type"] == "TESTED"]
    if not tested_events.empty:
        fig.add_trace(go.Scatter(
            x=tested_events["time_candle"],
            y=tested_events["price_trigger"],
            mode="markers",
            marker=dict(symbol="diamond", size=9, color="#ffd54f", line=dict(color="#000000", width=1)),
            name="FVG TESTED",
            hoverinfo="text",
            hovertext=[f"FVG {fid} TESTED lúc {t}" for fid, t in zip(tested_events["fvg_id"], tested_events["time_candle"])],
        ))

    inv_events = lc_df[lc_df["event_type"] == "INVALIDATED"]
    if not inv_events.empty:
        fig.add_trace(go.Scatter(
            x=inv_events["time_candle"],
            y=inv_events["price_trigger"],
            mode="markers",
            marker=dict(symbol="x", size=10, color="#ff5252", line=dict(width=2)),
            name="FVG INVALIDATED",
            hoverinfo="text",
            hovertext=[f"FVG {fid} INVALIDATED lúc {t} (đóng cửa {p:.2f})" for fid, t, p in zip(inv_events["fvg_id"], inv_events["time_candle"], inv_events["price_trigger"])],
        ))

    exp_events = lc_df[lc_df["event_type"] == "EXPIRED"]
    if not exp_events.empty:
        # Lấy mức giá trung bình của FVG để hiển thị marker hết hạn
        exp_prices = []
        for _, r in exp_events.iterrows():
            f_row = fvg_df[fvg_df["fvg_id"] == r["fvg_id"]]
            if not f_row.empty:
                exp_prices.append((f_row.iloc[0]["top"] + f_row.iloc[0]["bottom"]) / 2.0)
            else:
                exp_prices.append(df_h1["close"].mean())

        fig.add_trace(go.Scatter(
            x=exp_events["time_candle"],
            y=exp_prices,
            mode="markers",
            marker=dict(symbol="circle-open", size=8, color="#90a4ae", line=dict(width=1.5)),
            name="FVG EXPIRED (24 bars)",
            hoverinfo="text",
            hovertext=[f"FVG {fid} EXPIRED lúc {t}" for fid, t in zip(exp_events["fvg_id"], exp_events["time_candle"])],
        ))

    fig.update_layout(
        title=f"Kiểm tra trực quan Fair Value Gap (FVG) & Vòng đời trên H1 Gold ({start_date} -> {end_date})",
        xaxis_title="Thời gian (UTC)",
        yaxis_title="Giá XAU/USD",
        template="plotly_dark",
        xaxis_rangeslider_visible=False,
        height=800,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    out_path = _REPO_ROOT / "artifacts" / "fvg_h1_sample.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out_path))
    print(f"Da xuat bieu do truc quan FVG tai: {out_path}")


if __name__ == "__main__":
    main()
