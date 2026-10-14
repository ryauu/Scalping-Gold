import sys
from pathlib import Path
import shutil
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches

sys.stdout.reconfigure(encoding="utf-8")
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.backtest.data import load_h1
from src.backtest.smc.fvg import detect_fvg, DEFAULT_GAMMA, DEFAULT_M

def export_fvg_zoom_chart():
    # Cắt cửa sổ nến hiển thị từ Thứ Năm 11/01/2024 đến Thứ Ba 16/01/2024
    df_raw = load_h1("2024-01-08", "2024-01-17").reset_index(drop=True)
    df_raw["time_utc"] = pd.to_datetime(df_raw["time_utc"])

    # Chạy detect_fvg trên tập lớn để lấy đúng fvg_id và lifecycle
    fvgs_all, lc_all = detect_fvg(df_raw, gamma=DEFAULT_GAMMA, m=DEFAULT_M)

    # Lọc các FVG trọng tâm thuộc tuần 11/01 - 16/01 (FVG 10, 11, 12, 13, 14, 15)
    focus_fids = [10, 11, 12, 13, 14, 15]
    fvgs = fvgs_all[fvgs_all["fvg_id"].isin(focus_fids)].copy()
    lc = lc_all[lc_all["fvg_id"].isin(focus_fids)].copy()

    # Cắt cửa sổ nến hiển thị từ bar 85 (11/01 16:00) đến bar 155 (16/01 19:00)
    min_bar = 85
    max_bar = 155
    df = df_raw.iloc[min_bar:max_bar + 1].copy().reset_index()
    bar_orig_to_sub = {row["index"]: i for i, row in df.iterrows()}
    n_bars = len(df)

    # Tìm vị trí nến cuối tuần trong cửa sổ này
    df["prev_time"] = df["time_utc"].shift(1)
    df["dt_hours"] = (df["time_utc"] - df["prev_time"]).dt.total_seconds() / 3600.0
    weekend_rows = df[df["dt_hours"] >= 24.0]

    plt.style.use("dark_background")
    fig, (ax, ax_tab) = plt.subplots(
        2, 1,
        figsize=(22, 14),
        dpi=160,
        gridspec_kw={"height_ratios": [3.2, 1.2]}
    )
    fig.patch.set_facecolor("#131722")
    ax.set_facecolor("#131722")
    ax_tab.set_facecolor("#181c27")

    # 1. Vẽ nến Candlestick
    width = 0.58
    col_up = "#089981"
    col_down = "#f23645"

    up = df[df["close"] >= df["open"]]
    down = df[df["close"] < df["open"]]

    for i, row in up.iterrows():
        ax.plot([i, i], [row["low"], row["high"]], color=col_up, linewidth=1.2, zorder=3)
        rect = patches.Rectangle((i - width/2, row["open"]), width, max(row["close"] - row["open"], 0.1),
                                 facecolor=col_up, edgecolor=col_up, zorder=4)
        ax.add_patch(rect)

    for i, row in down.iterrows():
        ax.plot([i, i], [row["low"], row["high"]], color=col_down, linewidth=1.2, zorder=3)
        rect = patches.Rectangle((i - width/2, row["close"]), width, max(row["open"] - row["close"], 0.1),
                                 facecolor=col_down, edgecolor=col_down, zorder=4)
        ax.add_patch(rect)

    # 2. Vùng Shading thể hiện rõ khoảng nghỉ cuối tuần (Weekend Gap)
    for i_w, r_w in weekend_rows.iterrows():
        ax.axvspan(i_w - 0.5, i_w + 0.5, color="#2a2e39", alpha=0.45, zorder=1)
        ax.axvline(i_w - 0.5, color="#ff9800", linestyle="--", linewidth=1.5, alpha=0.8, zorder=2)
        ax.axvline(i_w + 0.5, color="#ff9800", linestyle="--", linewidth=1.5, alpha=0.8, zorder=2)
        
        ax.text(i_w, 2018.5, f"KHOẢNG NGHỈ CUỐI TUẦN (GAP 50 GIỜ)\nĐóng: {r_w['prev_time'].strftime('%m-%d %H:%M')}\nMở: {r_w['time_utc'].strftime('%m-%d %H:%M')}",
                color="#ffb74d", fontsize=9.5, fontweight="bold", ha="center", va="bottom",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#1e222d", edgecolor="#ff9800", alpha=0.9), zorder=8)

    # 3. Vẽ các hộp FVG và xác định điểm kết thúc
    end_events = lc[lc["event_type"].isin(["INVALIDATED", "EXPIRED"])].set_index("fvg_id")

    for _, fvg in fvgs.iterrows():
        fid = int(fvg["fvg_id"])
        orig_start = int(fvg["bar_index"])
        top = float(fvg["top"])
        bottom = float(fvg["bottom"])
        direction = fvg["direction"]
        
        # Tìm điểm kết thúc
        if fid in end_events.index:
            orig_end = int(end_events.loc[fid, "bar_index"])
        else:
            orig_end = orig_start + DEFAULT_M
            
        x_start = bar_orig_to_sub.get(orig_start, 0)
        x_end = bar_orig_to_sub.get(orig_end, n_bars - 1)
        box_w = max(x_end - x_start, 0.8)
        
        # Kiểm tra FVG này có vắt qua cuối tuần không
        w_idx = weekend_rows.index[0]
        crosses_weekend = (x_start < w_idx) and (x_end >= w_idx)
        
        f_col = "#089981" if direction == "BULLISH" else "#f23645"
        e_col = "#26a69a" if direction == "BULLISH" else "#ef5350"
        
        if crosses_weekend:
            e_col = "#ffb300"
            lw = 2.0
            line_s = "-"
            alpha = 0.28
        else:
            lw = 1.2
            line_s = "--"
            alpha = 0.18
            
        rect = patches.Rectangle((x_start, bottom), box_w, top - bottom,
                                 facecolor=f_col, edgecolor=e_col, linestyle=line_s,
                                 linewidth=lw, alpha=alpha, zorder=2)
        ax.add_patch(rect)
        
        label_txt = f"FVG #{fid} ({direction[0]})"
        if crosses_weekend:
            label_txt += " [SỐNG QUA CUỐI TUẦN]"
        
        # Định vị nhãn không đè nến
        if fid == 12:
            y_txt = top + 0.4
            va_txt = "bottom"
            x_txt = x_start + 0.3
        elif fid == 14:
            y_txt = top
            va_txt = "bottom"
            x_txt = x_start + 1.8  # Đẩy sang phải để không chạm chấm T#14
        elif direction == "BULLISH":
            y_txt = top
            va_txt = "bottom"
            x_txt = x_start + 0.3
        else:
            y_txt = bottom
            va_txt = "top"
            x_txt = x_start + 0.3
            
        ax.text(x_txt, y_txt, label_txt,
                color=e_col, fontsize=8.5, fontweight="bold",
                va=va_txt, zorder=5)

    # 4. Vẽ các Marker vòng đời (TESTED, INVALIDATED, EXPIRED)
    for _, r in lc.iterrows():
        fid = int(r["fvg_id"])
        ev = r["event_type"]
        orig_b = int(r["bar_index"])
        if orig_b not in bar_orig_to_sub:
            continue
        x = bar_orig_to_sub[orig_b]
        price = r["price_trigger"]
        
        if ev == "TESTED":
            ax.scatter([x], [price], marker="o", color="#ffeb3b", s=90, edgecolors="#f57f17", linewidths=1.5, zorder=7)
            y_off = 12 if fid != 14 else 14
            ax.annotate(f"T#{fid}", (x, price), textcoords="offset points", xytext=(0, y_off),
                        color="#fff59d", fontsize=8, fontweight="bold", ha="center", zorder=8)
        elif ev == "INVALIDATED":
            ax.scatter([x], [price], marker="X", color="#ff1744", s=130, edgecolors="#b71c1c", linewidths=1.5, zorder=7)
            ax.annotate(f"INV#{fid}", (x, price), textcoords="offset points", xytext=(0, -14),
                        color="#ff8a80", fontsize=8, fontweight="bold", ha="center", zorder=8)
        elif ev == "EXPIRED":
            target_fvg = fvgs.loc[fvgs["fvg_id"] == fid].iloc[0]
            mid_p = (target_fvg["top"] + target_fvg["bottom"]) / 2.0
            ax.scatter([x], [mid_p], marker="s", color="#b0bec5", s=80, edgecolors="#37474f", linewidths=1.2, zorder=7)
            if fid == 10:
                ax.annotate(f"EXP#{fid} (M=24)", (x, mid_p), textcoords="offset points", xytext=(-55, 12),
                            color="#cfd8dc", fontsize=8, fontweight="bold", ha="center", zorder=8)
            else:
                ax.annotate(f"EXP#{fid} (M=24)", (x, mid_p), textcoords="offset points", xytext=(0, 10),
                            color="#cfd8dc", fontsize=8, fontweight="bold", ha="center", zorder=8)

    # Trục X và nhãn
    tick_step = 6
    tick_idx = list(range(0, n_bars, tick_step))
    tick_lbls = [df.iloc[i]["time_utc"].strftime("%m-%d %H:00") for i in tick_idx]
    ax.set_xticks(tick_idx)
    ax.set_xticklabels(tick_lbls, rotation=35, ha="right", fontsize=9, color="#b2b5be")
    ax.tick_params(colors="#b2b5be")
    ax.yaxis.tick_right()
    ax.grid(True, linestyle=":", alpha=0.2, color="#363c4e")
    ax.set_ylim(2015, 2060)

    ax.set_title(
        "XAU/USD H1 PHÓNG TO TUẦN 11/01/2024 – 16/01/2024: ĐỐI CHIẾU VÒNG ĐỜI & FVG SỐNG QUA CUỐI TUẦN\n"
        "Hộp Bullish #11 (2039.96–2044.03) tạo thứ Sáu sống liền mạch qua 50h cuối tuần sang trưa thứ Hai mới EXPIRED ở nến M=24",
        fontsize=12.5, fontweight="bold", color="#e0e3eb", pad=14
    )

    # 5. Bảng tổng hợp đối chiếu vòng đời phía dưới
    # Tách bạch rõ 2 khái niệm: spans_gap (bộ ba lúc tạo) và Sống qua gap (trong vòng đời)
    table_data = []
    headers = [
        "ID", "Hướng", "Thời điểm tạo (UTC)", "Mép gần", "Mép xa",
        "spans_gap\n(bộ ba tạo)", "Vòng đời sống qua gap",
        "Sự kiện 1 (TESTED)", "Sự kiện kết thúc", "Số nến sống"
    ]

    surviving_gap_map = {
        10: "Nghỉ 2h hàng ngày",
        11: "Cuối tuần 50h",
        12: "Cuối tuần 50h",
        13: "Nghỉ lễ 4h (MLK)",
        14: "Nghỉ lễ 4h (MLK)",
        15: "Nghỉ 2h hàng ngày",
    }

    for _, f in fvgs.iterrows():
        fid = int(f["fvg_id"])
        d = f["direction"]
        t_c = f["time_candle"].strftime("%m-%d %H:00")
        near = f"{f['near_edge']:.2f}"
        far = f"{f['far_edge']:.2f}"
        spans_creation = str(f["spans_gap"])
        surviving_gap = surviving_gap_map.get(fid, "Không")
        
        orig_s = int(f["bar_index"])
        t_row = lc[(lc["fvg_id"] == fid) & (lc["event_type"] == "TESTED")]
        if not t_row.empty:
            t_b = int(t_row.iloc[0]["bar_index"])
            t_str = f"{t_row.iloc[0]['time_candle'].strftime('%m-%d %H:00')} (+{t_b - orig_s} nến)"
        else:
            t_str = "Chưa chạm"
            
        term_row = lc[(lc["fvg_id"] == fid) & (lc["event_type"].isin(["INVALIDATED", "EXPIRED"]))]
        if not term_row.empty:
            term_ev = term_row.iloc[0]["event_type"]
            term_b = int(term_row.iloc[0]["bar_index"])
            term_str = f"{term_ev} @ {term_row.iloc[0]['time_candle'].strftime('%m-%d %H:00')} (+{term_b - orig_s} nến)"
            lifespan = f"{term_b - orig_s} nến"
        else:
            term_str = "Còn Active"
            lifespan = "> Cửa sổ"
            
        table_data.append([
            f"#{fid}", d, t_c, near, far,
            spans_creation, surviving_gap,
            t_str, term_str, lifespan
        ])

    ax_tab.axis("off")
    col_widths = [0.04, 0.07, 0.11, 0.07, 0.07, 0.09, 0.13, 0.15, 0.20, 0.07]
    table = ax_tab.table(
        cellText=table_data,
        colLabels=headers,
        colWidths=col_widths,
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8.8)
    table.scale(1.0, 1.85)

    for (row_idx, col_idx), cell in table.get_celld().items():
        if row_idx == 0:
            cell.set_facecolor("#2a2e39")
            cell.set_text_props(color="#ffb74d", fontweight="bold")
        else:
            if table_data[row_idx - 1][0] == "#11":
                cell.set_facecolor("#1f2c24")
                cell.set_text_props(color="#a5d6a7", fontweight="bold")
            elif table_data[row_idx - 1][0] == "#12":
                cell.set_facecolor("#2c1f24")
                cell.set_text_props(color="#ef9a9a", fontweight="bold")
            else:
                cell.set_facecolor("#181c27" if row_idx % 2 == 0 else "#1c212e")
                cell.set_text_props(color="#e0e3eb")
        cell.set_edgecolor("#363c4e")

    plt.tight_layout()

    out_dir = _REPO_ROOT / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_png = out_dir / "fvg_zoom_weekend_sample.png"
    plt.savefig(out_png, dpi=160, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close()
    print(f"[OK] Đã xuất biểu đồ zoom-in: {out_png}")

    conv_art_dir = Path(r"C:\Users\duy\.gemini\antigravity-ide\brain\0fae2a1a-8976-43a9-916b-dd50c1e47f40")
    if conv_art_dir.exists():
        target_path = conv_art_dir / "fvg_zoom_weekend_sample.png"
        shutil.copy2(out_png, target_path)
        print(f"[OK] Đã copy sang conversation artifacts: {target_path}")


if __name__ == "__main__":
    export_fvg_zoom_chart()
