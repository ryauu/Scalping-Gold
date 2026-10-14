"""Xuất ảnh đồ thị trực quan Cấu trúc thị trường H1 Gold (BOS & CHoCH)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import pandas as pd

from src.backtest.data import load_h1
from src.backtest.smc.structure import load_structure
from src.backtest.smc.swings import detect_swings


def export_chart():
    start_date = "2020-01-02"
    end_date = "2020-01-14"

    df_h1 = load_h1(start_date, end_date).reset_index(drop=True)
    df_h1["time_utc"] = pd.to_datetime(df_h1["time_utc"])
    time_to_idx = {t: i for i, t in enumerate(df_h1["time_utc"])}

    swings = detect_swings(df_h1, k=3)
    struct = load_structure(start_date, end_date, warmup_months=None, k=3)

    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(24, 11), dpi=150)
    fig.patch.set_facecolor("#131722")
    ax.set_facecolor("#131722")

    # 1. Vẽ nến Candlestick
    width = 0.6
    width2 = 0.12

    up = df_h1[df_h1["close"] >= df_h1["open"]]
    down = df_h1[df_h1["close"] < df_h1["open"]]

    # Up candles (xanh ngọc)
    col_up = "#089981"
    col_down = "#f23645"

    for idx, row in up.iterrows():
        i = row.name
        ax.plot([i, i], [row["low"], row["high"]], color=col_up, linewidth=1.2, zorder=2)
        bottom = row["open"]
        height = max(row["close"] - row["open"], 0.1)
        rect = patches.Rectangle((i - width / 2, bottom), width, height, facecolor=col_up, edgecolor=col_up, zorder=3)
        ax.add_patch(rect)

    for idx, row in down.iterrows():
        i = row.name
        ax.plot([i, i], [row["low"], row["high"]], color=col_down, linewidth=1.2, zorder=2)
        bottom = row["close"]
        height = max(row["open"] - row["close"], 0.1)
        rect = patches.Rectangle((i - width / 2, bottom), width, height, facecolor=col_down, edgecolor=col_down, zorder=3)
        ax.add_patch(rect)

    # 2. Vẽ Swing Highs & Swing Lows
    highs = swings[swings["swing_type"] == "HIGH"]
    lows = swings[swings["swing_type"] == "LOW"]

    h_indices = [time_to_idx[t] for t in highs["time_candle"] if t in time_to_idx]
    h_prices = [highs.loc[highs["time_candle"] == t, "price"].values[0] for t in highs["time_candle"] if t in time_to_idx]
    ax.scatter(h_indices, [p + 1.2 for p in h_prices], marker="v", color="#26a69a", s=80, label="Swing High (k=3)", zorder=5)

    l_indices = [time_to_idx[t] for t in lows["time_candle"] if t in time_to_idx]
    l_prices = [lows.loc[lows["time_candle"] == t, "price"].values[0] for t in lows["time_candle"] if t in time_to_idx]
    ax.scatter(l_indices, [p - 1.2 for p in l_prices], marker="^", color="#ef5350", s=80, label="Swing Low (k=3)", zorder=5)

    # 3. Vẽ sự kiện BOS & CHoCH
    for _, ev in struct.iterrows():
        tc = pd.to_datetime(ev["time_candle"])
        if tc not in time_to_idx:
            continue
        i_break = time_to_idx[tc]
        ev_type = str(ev["event_type"])
        p_broken = float(ev["price_broken"])
        close_p = float(ev["close_price"])

        is_bullish = "BULLISH" in ev_type
        is_choch = "CHOCH" in ev_type

        if is_choch:
            color = "#f59e0b" if is_bullish else "#ec4899"
            tag = "BULL CHoCH" if is_bullish else "BEAR CHoCH"
        else:
            color = "#10b981" if is_bullish else "#ef4444"
            tag = "BULL BOS" if is_bullish else "BEAR BOS"

        # Đường gạch ngang mức swing bị phá
        i_start_line = max(0, i_break - 6)
        ax.plot([i_start_line, i_break], [p_broken, p_broken], color=color, linestyle="--", linewidth=1.2, alpha=0.8, zorder=4)

        # Gắn nhãn sự kiện và mũi tên (hiển thị cả Giá đóng cửa và Mức bị phá)
        offset_y = 4.2 if is_bullish else -4.2
        ax.annotate(
            f"{tag}\nClose: {close_p:.1f}\nLevel: {p_broken:.1f}",
            xy=(i_break, close_p),
            xytext=(i_break, close_p + offset_y),
            arrowprops=dict(arrowstyle="->", color=color, lw=1.2),
            ha="center",
            va="bottom" if is_bullish else "top",
            fontsize=7.5,
            fontweight="bold",
            color=color,
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#1e222d", edgecolor=color, alpha=0.9),
            zorder=6,
        )

    # Cấu hình trục và tiêu đề
    step = 12
    ticks = list(range(0, len(df_h1), step))
    tick_labels = [df_h1.iloc[t]["time_utc"].strftime("%d/%m\n%H:%M") for t in ticks]
    ax.set_xticks(ticks)
    ax.set_xticklabels(tick_labels, fontsize=9, color="#9ca3af")
    ax.tick_params(colors="#9ca3af", which="both")
    ax.grid(True, linestyle=":", color="#2a2e39", alpha=0.6)

    ax.set_title(
        f"KIỂM TRA TRỰC QUAN CẤU TRÚC THỊ TRƯỜNG SMC (BOS & CHoCH) TRÊN H1 GOLD ({start_date} -> {end_date})\n"
        f"Quy tắc k=3, First Break Only, Shadowed Swings Max Rule & Khởi động toàn bộ lịch sử (warmup_months=None)",
        fontsize=14,
        fontweight="bold",
        color="#f3f4f6",
        pad=15,
    )
    ax.set_ylabel("Giá Vàng (USD)", fontsize=11, color="#d1d5db")
    ax.set_xlabel("Thời gian nến H1 (UTC)", fontsize=11, color="#d1d5db")
    ax.legend(loc="upper left", framealpha=0.8, facecolor="#1e222d", edgecolor="#374151", fontsize=10)

    plt.tight_layout()

    # Lưu cả 2 vị trí
    out_repo = _REPO_ROOT / "artifacts" / "structure_h1_sample.png"
    out_brain = Path(r"C:\Users\duy\.gemini\antigravity-ide\brain\41162e08-b6dd-4e49-83b6-c9dae033bacb\structure_h1_sample.png")

    out_repo.parent.mkdir(parents=True, exist_ok=True)
    out_brain.parent.mkdir(parents=True, exist_ok=True)

    fig.savefig(str(out_repo), bbox_inches="tight")
    fig.savefig(str(out_brain), bbox_inches="tight")
    plt.close(fig)

    print(f"Da xuat anh tai: {out_repo}")
    print(f"Da xuat anh tai: {out_brain}")


if __name__ == "__main__":
    export_chart()
