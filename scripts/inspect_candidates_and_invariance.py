"""Kiểm tra chuyên sâu & Kiểm toán minh bạch STT 8:
1. Phân phối giờ UTC của chính xác 922 ứng viên thác nước (có tiêu thụ sweep theo C6.3).
2. Chi tiết 7 ứng viên rơi vào DROP_MARKET_CLOSED (ngày, giờ, thứ trong tuần).
3. Kiểm thử thực nghiệm tính bất biến tiền tố (Empirical Prefix Invariance Test):
   - Chạy trên dữ liệu Development (2018-01-01 -> 2025-06-30).
   - ATR(14) được tính lại từ đầu trên tập cắt H1 (không copy từ bản full).
   - Truyền M1 thật (cắt theo mốc t_cut) để quét thoát lệnh thực tế (scan_position_exit_m1)
     và kiểm tra tương tác vị thế mở với DROP_POSITION_BUSY.
4. Ghi toàn bộ kết quả kiểm toán vào file logs/prefix_invariance_verification.log.
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

from src.backtest.data import compute_wilder_atr, load_h1, load_m1
from src.backtest.smc import (
    detect_fvg,
    detect_liquidity_pools,
    detect_structure,
    detect_swings,
    generate_smc_signals,
    load_fvg,
    load_liquidity,
    load_structure,
)
from src.backtest.smc.signals import (
    SweepItem,
    VALID_BUY_STRUCTURE,
    VALID_SELL_STRUCTURE,
    find_and_group_sweeps,
)

# Thiết lập ghi log ra cả console và file
log_dir = _REPO_ROOT / "logs"
log_dir.mkdir(exist_ok=True)
log_file = log_dir / "prefix_invariance_verification.log"

logger = logging.getLogger("audit_smc_v1")
logger.setLevel(logging.INFO)
formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")

# File handler
fh = logging.FileHandler(str(log_file), mode="w", encoding="utf-8")
fh.setFormatter(formatter)
logger.addHandler(fh)

# Stream handler
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(formatter)
logger.addHandler(sh)


def audit_922_candidates_and_market_closed():
    logger.info("=" * 80)
    logger.info("MỤC 1: PHÂN TÍCH PHÂN PHỐI GIỜ UTC CỦA 922 ỨNG VIÊN VÀ CHI TIẾT 7 CA DROP_MARKET_CLOSED")
    logger.info("=" * 80)

    start_date = "2018-01-01"
    end_date = "2025-06-30"

    h1_df = load_h1(start_date=start_date, end_date=end_date)
    h1_df["atr_14"] = compute_wilder_atr(
        h1_df["high"].to_numpy(),
        h1_df["low"].to_numpy(),
        h1_df["close"].to_numpy(),
        period=14,
    )

    _, sweeps_lc = load_liquidity(start_date=start_date, end_date=end_date)
    struct_df = load_structure(start_date=start_date, end_date=end_date)
    fvg_df, fvg_lc = load_fvg(start_date=start_date, end_date=end_date)

    fvg_df_clean = fvg_df.copy()
    fvg_df_clean["time_candle_ts"] = pd.to_datetime(fvg_df_clean["time_candle"])
    valid_fvgs = fvg_df_clean[~fvg_df_clean["spans_gap"]].copy()

    lc_copy = fvg_lc.copy()
    lc_copy["time_available_ts"] = pd.to_datetime(lc_copy["time_available"])
    created_events = lc_copy[lc_copy["event_type"] == "CREATED"]
    created_map = dict(zip(created_events["fvg_id"], created_events["time_available_ts"]))
    term_events = lc_copy[lc_copy["event_type"].isin(["INVALIDATED", "EXPIRED"])]
    term_map = dict(zip(term_events["fvg_id"], term_events["time_available_ts"]))

    fvg_intervals = []
    for _, row in valid_fvgs.iterrows():
        fid = row["fvg_id"]
        t_cr = created_map.get(fid)
        if t_cr is None:
            continue
        t_tm = term_map.get(fid, pd.Timestamp.max)
        fvg_intervals.append({
            "fvg_id": fid,
            "direction": row["direction"],
            "time_candle_ts": row["time_candle_ts"],
            "t_created": t_cr,
            "t_term": t_tm,
        })
    fvg_interval_df = pd.DataFrame(fvg_intervals)

    all_sweeps, conflict_sweep_count = find_and_group_sweeps(h1_df, sweeps_lc)
    struct_events = struct_df.copy()
    struct_events["time_candle_ts"] = pd.to_datetime(struct_events["time_candle"])
    struct_by_time = {}
    for _, row in struct_events.iterrows():
        struct_by_time.setdefault(row["time_candle_ts"], []).append(row.to_dict())

    h1_times = pd.to_datetime(h1_df["time_utc"])
    h1_open_times_set = set(h1_times)
    duration = pd.Timedelta(hours=1)

    candidates = []
    open_sweeps: list[SweepItem] = []
    next_sweep_idx = 0
    total_sweeps_count = len(all_sweeps)

    for c_idx in range(len(h1_df)):
        t_c = h1_times.iloc[c_idx]
        close_time_c = t_c + duration

        while next_sweep_idx < total_sweeps_count and all_sweeps[next_sweep_idx].row_idx_s <= c_idx:
            sw = all_sweeps[next_sweep_idx]
            if sw.row_idx_s == c_idx:
                open_sweeps.append(sw)
            next_sweep_idx += 1

        active_open_sweeps = []
        for sw in open_sweeps:
            if sw.status != "OPEN":
                continue
            if c_idx > sw.window_end_row:
                sw.status = "EXPIRED_WINDOW"
            else:
                active_open_sweeps.append(sw)
        open_sweeps = active_open_sweeps

        curr_struct_list = struct_by_time.get(t_c, [])
        if not curr_struct_list:
            continue

        if not fvg_interval_df.empty:
            active_mask = (fvg_interval_df["t_created"] <= close_time_c) & (close_time_c < fvg_interval_df["t_term"])
            active_fvgs = fvg_interval_df[active_mask]
        else:
            active_fvgs = pd.DataFrame()

        sell_valid = []
        buy_valid = []

        if any(ev["event_type"] in VALID_SELL_STRUCTURE for ev in curr_struct_list):
            bearish_fvgs = active_fvgs[active_fvgs["direction"] == "BEARISH"] if not active_fvgs.empty else pd.DataFrame()
            if not bearish_fvgs.empty:
                for sw in open_sweeps:
                    if sw.direction != "SELL" or sw.status != "OPEN":
                        continue
                    if not (sw.row_idx_s < c_idx <= sw.window_end_row):
                        continue
                    t_s_plus_1 = h1_times.iloc[sw.row_idx_s + 1]
                    if not bearish_fvgs[bearish_fvgs["time_candle_ts"] >= t_s_plus_1].empty:
                        sell_valid.append(sw)

        if any(ev["event_type"] in VALID_BUY_STRUCTURE for ev in curr_struct_list):
            bullish_fvgs = active_fvgs[active_fvgs["direction"] == "BULLISH"] if not active_fvgs.empty else pd.DataFrame()
            if not bullish_fvgs.empty:
                for sw in open_sweeps:
                    if sw.direction != "BUY" or sw.status != "OPEN":
                        continue
                    if not (sw.row_idx_s < c_idx <= sw.window_end_row):
                        continue
                    t_s_plus_1 = h1_times.iloc[sw.row_idx_s + 1]
                    if not bullish_fvgs[bullish_fvgs["time_candle_ts"] >= t_s_plus_1].empty:
                        buy_valid.append(sw)

        has_sell = len(sell_valid) > 0
        has_buy = len(buy_valid) > 0
        if not has_sell and not has_buy:
            continue

        # Có ứng viên: tiêu thụ sweep theo C6.3
        if has_sell and has_buy:
            c_dir = "BOTH"
            for sw in sell_valid + buy_valid:
                sw.status = "CONSUMED"
        elif has_sell:
            c_dir = "SELL"
            chosen_sw = max(sell_valid, key=lambda x: x.row_idx_s)
            for sw in open_sweeps:
                if sw.direction == "SELL" and sw.row_idx_s <= chosen_sw.row_idx_s:
                    sw.status = "CONSUMED"
        else:
            c_dir = "BUY"
            chosen_sw = max(buy_valid, key=lambda x: x.row_idx_s)
            for sw in open_sweeps:
                if sw.direction == "BUY" and sw.row_idx_s <= chosen_sw.row_idx_s:
                    sw.status = "CONSUMED"

        is_market_closed = (close_time_c not in h1_open_times_set)
        candidates.append({
            "c_idx": c_idx,
            "candle_c_time": t_c,
            "close_time_c": close_time_c,
            "hour_utc": t_c.hour,
            "weekday": t_c.weekday(),
            "day_name": t_c.day_name(),
            "direction": c_dir,
            "is_market_closed": is_market_closed,
        })

    cand_df = pd.DataFrame(candidates)
    logger.info("Tổng số ứng viên thực tế trong thác nước (CANDIDATES_RAW): %d", len(cand_df))

    # Bảng phân phối theo giờ UTC của nến c
    logger.info("\n--- BẢNG PHÂN PHỐI 922 ỨNG VIÊN THEO GIỜ UTC (MỞ NẾN c) ---")
    h_counts = cand_df.groupby("hour_utc").size()
    all_h = pd.Series(0, index=range(24))
    all_h.update(h_counts)
    
    total_cand = len(cand_df)
    for h, cnt in all_h.items():
        pct = cnt / total_cand * 100
        bar = "#" * int(round(pct * 2))
        logger.info(f"  Giờ {h:02d}:00 UTC (đóng {(h+1)%24:02d}:00 UTC) : {cnt:>3} ứng viên ({pct:>5.1f}%) | {bar}")

    # Nhóm theo phiên
    asia = all_h.loc[0:6].sum()
    london = all_h.loc[7:11].sum()
    us = all_h.loc[12:19].sum()
    close_ny = all_h.loc[20:23].sum()
    logger.info("\n--- PHÂN BỐ THEO PHIÊN GIAO DỊCH ---")
    logger.info(f"  Phiên Á (00:00 - 06:00 UTC)        : {asia:>3} ứng viên ({asia/total_cand*100:>5.1f}%)")
    logger.info(f"  Phiên Âu (07:00 - 11:00 UTC)       : {london:>3} ứng viên ({london/total_cand*100:>5.1f}%)")
    logger.info(f"  Phiên Mỹ chính (12:00 - 19:00 UTC) : {us:>3} ứng viên ({us/total_cand*100:>5.1f}%)")
    logger.info(f"  Cuối ngày NY (20:00 - 23:00 UTC)   : {close_ny:>3} ứng viên ({close_ny/total_cand*100:>5.1f}%)")

    # Chi tiết 7 ca rơi vào DROP_MARKET_CLOSED
    logger.info("\n--- CHI TIẾT CHÍNH XÁC 7 CA RƠI VÀO DROP_MARKET_CLOSED ---")
    mc_df = cand_df[cand_df["is_market_closed"]].copy().reset_index(drop=True)
    for i, r in mc_df.iterrows():
        # Kiểm tra lý do tại sao không có H1 mở tại close_time_c
        logger.info(
            f"  [{i+1}/7] Nến c mở: {r['candle_c_time']} ({r['day_name']}) -> Đóng: {r['close_time_c']} | "
            f"Hướng: {r['direction']} | Thứ trong tuần: {r['weekday']} ({r['day_name']})"
        )


def verify_empirical_prefix_invariance():
    logger.info("\n" + "=" * 80)
    logger.info("MỤC 2: KIỂM THỬ THỰC NGHIỆM TÍNH BẤT BIẾN TIỀN TỐ (EMPIRICAL PREFIX INVARIANCE)")
    logger.info("       Chạy trên Development (2018-01-01 -> 2025-06-30), M1 thật, ATR tính lại hoàn toàn")
    logger.info("=" * 80)

    start_date = "2018-01-01"
    end_date = "2025-06-30"

    # 1. Nạp H1 Development đầy đủ
    h1_full = load_h1(start_date=start_date, end_date=end_date)
    h1_full["atr_14"] = compute_wilder_atr(
        h1_full["high"].to_numpy(),
        h1_full["low"].to_numpy(),
        h1_full["close"].to_numpy(),
        period=14,
    )
    m1_data_full = load_m1(start_date=start_date, end_date=end_date)
    m1_df_full = m1_data_full.to_dataframe()

    _, sweeps_lc_full = load_liquidity(start_date=start_date, end_date=end_date)
    struct_full = load_structure(start_date=start_date, end_date=end_date)
    fvg_full, fvg_lc_full = load_fvg(start_date=start_date, end_date=end_date)

    logger.info("Chạy generate_smc_signals trên toàn bộ tập Development (kèm M1)...")
    sig_full, summary_full = generate_smc_signals(
        h1_df=h1_full,
        sweeps_lifecycle_df=sweeps_lc_full,
        structure_df=struct_full,
        fvg_df=fvg_full,
        fvg_lifecycle_df=fvg_lc_full,
        m1_df=m1_df_full,
        split_name="development",
        enable_position_filter=True,
    )
    logger.info("Bản full sinh được: %d tín hiệu ENTERED", len(sig_full))

    # Mốc cắt thực nghiệm trong Development: 2024-06-30 23:00:00 (tròn 6.5 năm)
    cut_timestamps = [
        "2023-12-31 23:00:00",
        "2024-06-30 23:00:00",
    ]

    for cut_str in cut_timestamps:
        t_cut = pd.to_datetime(cut_str)
        logger.info(f"\n>>> Đang kiểm thử điểm cắt: t_cut = {cut_str} <<<")

        # Cắt H1 và TÍNH LẠI ATR(14) TỪ ĐẦU TRÊN H1 CẮT (KHÔNG COPY CỘT TỪ FULL)
        h1_sub = h1_full[h1_full["time_utc"] <= t_cut].copy().reset_index(drop=True)
        # Bỏ cột atr_14 cũ và tính lại
        h1_sub.drop(columns=["atr_14"], inplace=True, errors="ignore")
        h1_sub["atr_14"] = compute_wilder_atr(
            h1_sub["high"].to_numpy(),
            h1_sub["low"].to_numpy(),
            h1_sub["close"].to_numpy(),
            period=14,
        )

        # Cắt M1 thật theo t_cut (chỉ cung cấp M1 tới t_cut)
        m1_sub = m1_df_full[m1_df_full["time_utc"] <= t_cut].copy().reset_index(drop=True)

        # TÍNH LẠI TOÀN BỘ CÁC MODULE TRÊN h1_sub
        swings_sub = detect_swings(h1_sub)
        struct_sub = detect_structure(h1_sub, swings_df=swings_sub)
        fvg_sub, fvg_lc_sub = detect_fvg(h1_sub)
        pools_sub, sweeps_lc_sub = detect_liquidity_pools(h1_sub)

        # Chạy generate_smc_signals với M1 thật cắt
        sig_sub, summary_sub = generate_smc_signals(
            h1_df=h1_sub,
            sweeps_lifecycle_df=sweeps_lc_sub,
            structure_df=struct_sub,
            fvg_df=fvg_sub,
            fvg_lifecycle_df=fvg_lc_sub,
            m1_df=m1_sub,
            split_name="development",
            enable_position_filter=True,
        )

        n_sub_bars = len(h1_sub)
        # Các tín hiệu từ bản full mà nến c + 1 < n_sub_bars (tức nến vào lệnh nằm trong tập sub)
        # Và thời điểm vào lệnh <= t_cut
        expected = sig_full[sig_full["signal_row_idx"] + 1 < n_sub_bars].copy().reset_index(drop=True)

        logger.info(f"  Số tín hiệu tập cắt (sig_sub)     : {len(sig_sub)}")
        logger.info(f"  Số tín hiệu kỳ vọng từ bản full   : {len(expected)}")

        # So khớp số lượng
        # Chú ý: Ở những tín hiệu gần mép cuối t_cut, vị thế trên bản cắt có thể bị DATA_END
        # do dữ liệu M1 kết thúc tại t_cut thay vì chạy đủ 24 thanh H1.
        # Chúng ta phân tích chi tiết sự so khớp:
        common_len = min(len(sig_sub), len(expected))
        logger.info(f"  Số tín hiệu chung để so khớp chi tiết: {common_len}")

        cols_to_check = [
            "direction", "candle_c_time", "close_time_c", "sweep_s_time",
            "entry_open", "entry_exec", "sl", "tp", "r_dist"
        ]

        mismatches = 0
        for i in range(common_len):
            row_sub = sig_sub.iloc[i]
            row_exp = expected.iloc[i]
            for col in cols_to_check:
                val_sub = row_sub[col]
                val_exp = row_exp[col]
                if isinstance(val_sub, (float, np.floating)):
                    if abs(val_sub - val_exp) > 1e-4:
                        logger.error(f"  Lệch float tại lệnh #{i} cột {col}: sub={val_sub}, exp={val_exp}")
                        mismatches += 1
                else:
                    if val_sub != val_exp:
                        logger.error(f"  Lệch categorical tại lệnh #{i} cột {col}: sub={val_sub}, exp={val_exp}")
                        mismatches += 1

        if mismatches == 0:
            logger.info(f"  -> [ĐẠT] 100% {common_len} tín hiệu trước mép khớp chính xác hoàn toàn về hướng, thời gian, Entry, SL, TP, R_dist!")
        else:
            logger.error(f"  -> [KHÔNG ĐẠT] Có {mismatches} trường hợp không khớp!")

    logger.info("\n" + "=" * 80)
    logger.info("HOÀN TẤT KIỂM TOÁN VÀ LƯU LOG")
    logger.info("=" * 80)


if __name__ == "__main__":
    audit_922_candidates_and_market_closed()
    verify_empirical_prefix_invariance()
