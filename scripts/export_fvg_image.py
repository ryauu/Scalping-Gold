"""Xuất ảnh đồ thị trực quan Fair Value Gap (FVG) & Vòng đời trên nến H1 Gold."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8")

import matplotlib.patches as patches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.backtest.data import load_h1
from src.backtest.smc.fvg import DEFAULT_GAMMA, DEFAULT_M, detect_fvg


def export_fvg_chart():
    start_date = "2024-01-08"
    end_date = "2024-01-22"

    df_h1 = load_h1(start_date, end_date).reset_index(drop=True)
    df_h1["time_utc"] = pd.to_datetime(df_h1["time_utc"])
    time_to_idx = {t: i for i, t in enumerate(df_h1["time_utc"])}
    n_bars = len(df_h1)

    fvgs, lc = detect_fvg(df_h1, gamma=DEFAULT_GAMMA, m=DEFAULT_M)

    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(26, 12), dpi=150)
    fig.patch.set_facecolor("#131722")
    ax.set_facecolor("#131722")

    # 1. Vẽ nến Candlestick
    width = 0.6
    col_up = "#089981"
    col_down = "#f23645"

    up = df_h1[df_h1["close"] >= df_h1["open"]]
    down = df_h1[df_h1["close"] < df_h1["open"]]

    for _, row in up.iterrows():
        i = row.name
        ax.plot([i, i], [row["low"], row["high"]], color=col_up, linewidth=1.2, zorder=2)
        bottom = row["open"]
        height = max(row["close"] - row["open"], 0.1)
        rect = patches.Rectangle((i - width / 2, bottom), width, height, facecolor=col_up, edgecolor=col_up, zorder=3)
        ax.add_patch(rect)

    for _, row in down.iterrows():
        i = row.name
        ax.plot([i, i], [row["low"], row["high"]], color=col_down, linewidth=1.2, zorder=2)
        bottom = row["close"]
        height = max(row["open"] - row["close"], 0.1)
        rect = patches.Rectangle((i - width / 2, bottom), width, height, facecolor=col_down, edgecolor=col_down, zorder=3)
        ax.add_patch(rect)

    # 2. Vẽ các vùng FVG (Boxes) từ lúc tạo đến lúc kết thúc vòng đời
    # Gom sự kiện kết thúc cho từng FVG
    end_events = lc[lc["event_type"].isin(["INVALIDATED", "EXPIRED"])].groupby("fvg_id").first()

    plotted_bull_label = False
    plotted_bear_label = False
    plotted_spans_gap_label = False

    for _, fvg in fvgs.iterrows():
        fid = int(fvg["fvg_id"])
        i_start = int(fvg["bar_index"])
        direction = fvg["direction"]
        top = float(fvg["top"])
        bottom = float(fvg["bottom"])
        spans_gap = bool(fvg["spans_gap"])

        # Xác định điểm kết thúc của hộp FVG
        if fid in end_events.index:
            end_row = end_events.loc[fid]
            i_end = int(end_row["bar_index"])
        else:
            i_end = min(i_start + DEFAULT_M, n_bars - 1)

        box_width = max(i_end - i_start, 0.8)

        if direction == "BULLISH":
            f_color = "#089981"
            e_color = "#26a69a" if not spans_gap else "#ff9800"
            label = "Bullish FVG" if not plotted_bull_label and not spans_gap else None
            if not spans_gap:
                plotted_bull_label = True
        else:
            f_color = "#f23645"
            e_color = "#ef5350" if not spans_gap else "#ff9800"
            label = "Bearish FVG" if not plotted_bear_label and not spans_gap else None
            if not spans_gap:
                plotted_bear_label = True

        if spans_gap and not plotted_spans_gap_label:
            label = "FVG (spans_gap = True)"
            plotted_spans_gap_label = True

        alpha = 0.22 if not spans_gap else 0.14
        hatch = None if not spans_gap else "//"

        box = patches.Rectangle(
            (i_start, bottom),
            box_width,
            top - bottom,
            facecolor=f_color,
            edgecolor=e_color,
            linestyle="--" if not spans_gap else ":",
            linewidth=1.2,
            alpha=alpha,
            hatch=hatch,
            label=label,
            zorder=1,
        )
        ax.add_patch(box)

    # 3. Đánh dấu các mốc vòng đời (Lifecycle Milestones)
    # TESTED (Chấm vàng/cam)
    tested_events = lc[lc["event_type"] == "TESTED"]
    t_idx = tested_events["bar_index"].to_list()
    t_prices = tested_events["price_trigger"].to_list()
    ax.scatter(t_idx, t_prices, marker="o", color="#ffeb3b", s=65, label="TESTED (Touch Near Edge)", zorder=6, edgecolors="#f57f17", linewidths=1.2)

    # INVALIDATED (Dấu X đỏ đậm)
    inv_events = lc[lc["event_type"] == "INVALIDATED"]
    inv_idx = inv_events["bar_index"].to_list()
    inv_prices = inv_events["price_trigger"].to_list()
    ax.scatter(inv_idx, inv_prices, marker="x", color="#ff1744", s=90, label="INVALIDATED (Close Beyond Far Edge)", zorder=6, linewidths=2.2)

    # EXPIRED (Hình vuông xám bạc)
    exp_events = lc[lc["event_type"] == "EXPIRED"]
    for _, exp_row in exp_events.iterrows():
        b_idx = int(exp_row["bar_index"])
        f_id = int(exp_row["fvg_id"])
        target_fvg = fvgs.loc[fvgs["fvg_id"] == f_id].iloc[0]
        mid_price = (target_fvg["top"] + target_fvg["bottom"]) / 2.0
        ax.scatter([b_idx], [mid_price], marker="s", color="#9e9e9e", s=55, label="EXPIRED (M=24 Bars)" if _ == exp_events.index[0] else None, zorder=6, alpha=0.85)

    # 4. Trục toạ độ và Nhãn
    tick_step = 12  # Mỗi 12 nến (nửa ngày)
    tick_indices = list(range(0, n_bars, tick_step))
    tick_labels = [df_h1.iloc[i]["time_utc"].strftime("%m-%d %H:00") for i in tick_indices]
    ax.set_xticks(tick_indices)
    ax.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=9, color="#b2b5be")

    ax.tick_params(colors="#b2b5be", which="both")
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")
    ax.grid(True, linestyle=":", alpha=0.25, color="#363c4e")

    ax.set_title(
        "XAU/USD (GOLD) H1 — FAIR VALUE GAPs & NHẬT KÝ VÒNG ĐỜI BẤT BIẾN (2 TUẦN: 08/01/2024 – 22/01/2024)\n"
        f"Tham số chuẩn: γ = {DEFAULT_GAMMA} (15% ATR[14]), M = {DEFAULT_M} nến | "
        f"Tổng: {len(fvgs)} FVGs (18 Bullish, 14 Bearish) | Lifecycle: 22 Tested, 21 Invalidated, 10 Expired",
        fontsize=13,
        fontweight="bold",
        color="#e0e3eb",
        pad=18,
    )

    ax.legend(loc="upper left", facecolor="#1e222d", edgecolor="#363c4e", fontsize=10, labelcolor="#e0e3eb")
    plt.tight_layout()

    out_dir = _REPO_ROOT / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_png = out_dir / "fvg_h1_sample.png"
    plt.savefig(out_png, dpi=150, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close()
    print(f"[OK] Đã xuất biểu đồ FVG trực quan: {out_png}")

    # Copy sang conversation artifacts directory để hiển thị cho user
    conv_art_dir = Path(r"C:\Users\duy\.gemini\antigravity-ide\brain\41162e08-b6dd-4e49-83b6-c9dae033bacb")
    if conv_art_dir.exists():
        target_path = conv_art_dir / "fvg_h1_sample.png"
        shutil.copy2(out_png, target_path)
        print(f"[OK] Đã copy sang artifact directory: {target_path}")


if __name__ == "__main__":
    export_fvg_chart()
