"""Script đếm tần suất tín hiệu SMC Sweep Confirmation v1 trên tập Development (2018-01-01 -> 2025-06-30).

Thực hiện:
1. Nạp H1, M1, Sweeps, Structure, FVG theo đúng quy chuẩn pre-registration.
2. Chạy generate_smc_signals và ghi nhận toàn bộ thác nước bộ đếm hủy (C11.3).
3. Thống kê số lượng tín hiệu ENTERED theo từng năm (2018 - 2025) và kiểm tra ngưỡng >= 800.
4. Báo cáo phân bổ chi tiết lý do thoát lệnh và các mã hủy.
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
logger = logging.getLogger("count_signals")


def main():
    start_date = "2018-01-01"
    end_date = "2025-06-30"

    logger.info("=== BẮT ĐẦU ĐẾM TẦN SUẤT TÍN HIỆU SMC SWEEP CONFIRMATION V1 ===")
    logger.info("Phạm vi dữ liệu: Lớp Development [%s -> %s]", start_date, end_date)

    # 1. Nạp H1 và tính ATR(14) có warm-up
    logger.info("1. Nạp nến H1 kèm warm-up để ATR(14) hoàn toàn ổn định tại 2018-01-01...")
    h1_full = load_h1(start_date=None, end_date=end_date)
    h1_full["atr_14"] = compute_wilder_atr(
        h1_full["high"].to_numpy(),
        h1_full["low"].to_numpy(),
        h1_full["close"].to_numpy(),
        period=14,
    )
    # Lấy lát cắt từ 2018-01-01
    h1_dev = h1_full[h1_full["time_utc"] >= pd.to_datetime(start_date)].reset_index(drop=True)
    logger.info("   -> Số hàng H1 Development: %d (từ %s đến %s)", len(h1_dev), h1_dev["time_utc"].iloc[0], h1_dev["time_utc"].iloc[-1])

    # 2. Nạp Sweeps, Structure, FVG
    logger.info("2. Nạp các bảng SMC chuẩn bị sự kiện...")
    _, sweeps_lc = load_liquidity(start_date=start_date, end_date=end_date)
    logger.info("   -> Sweeps lifecycle events: %d dòng", len(sweeps_lc))

    struct_df = load_structure(start_date=start_date, end_date=end_date)
    logger.info("   -> Structure events: %d dòng", len(struct_df))

    fvg_df, fvg_lc = load_fvg(start_date=start_date, end_date=end_date)
    logger.info("   -> FVG tables: %d FVGs, %d lifecycle events", len(fvg_df), len(fvg_lc))

    # 3. Nạp M1 Development
    logger.info("3. Nạp dữ liệu nến M1 Development (để khớp lệnh phút :00 và quét thoát vị thế)...")
    m1_data = load_m1(start_date, end_date)
    m1_df = m1_data.to_dataframe()
    logger.info("   -> Số nến M1: %d dòng", len(m1_df))

    # 4. Chạy tạo tín hiệu SMC v1 (có position filter: 1 vị thế duy nhất)
    logger.info("4. Sinh tín hiệu SMC Sweep Confirmation v1 (enable_position_filter=True)...")
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

    print("\n" + "=" * 70)
    print("=== THỐNG KÊ THÁC NƯỚC BỘ ĐẾM HỦY TÍN HIỆU (WATERFALL DROP COUNTERS) ===")
    print("=" * 70)
    for k, v in summary.items():
        print(f"  * {k:<25}: {v:>6}")

    total_entered = summary.get("ENTERED", 0)
    print("-" * 70)
    print(f"  TỔNG SỐ TÍN HIỆU ĐƯỢC KHỚP (ENTERED): {total_entered}")
    if total_entered >= 800:
        print(f"  -> ĐẠT tiêu chuẩn cỡ mẫu tối thiểu (>= 800): CÓ ({total_entered} >= 800)")
    else:
        print(f"  -> ĐẠT tiêu chuẩn cỡ mẫu tối thiểu (>= 800): KHÔNG ({total_entered} < 800, UNDER-SAMPLED)")
    print("=" * 70 + "\n")

    if not signals_df.empty:
        signals_df["year"] = pd.to_datetime(signals_df["candle_c_time"]).dt.year
        
        # Bảng phân bổ theo năm
        print("=== PHÂN BỔ TÍN HIỆU ENTERED THEO NĂM ===")
        yearly = signals_df.groupby(["year", "direction"]).size().unstack(fill_value=0)
        yearly["TOTAL"] = yearly.sum(axis=1)
        print(yearly.to_string())
        print("-" * 70)

        # Bảng phân bổ lý do thoát lệnh
        print("\n=== PHÂN BỔ LÝ DO THOÁT LỆNH (EXIT REASONS) ===")
        exit_counts = signals_df.groupby(["year", "exit_reason"]).size().unstack(fill_value=0)
        print(exit_counts.to_string())
        print("-" * 70)

        # Thống kê phân phối R_dist và ATR
        print("\n=== THỐNG KÊ R_DIST & ATR NẾN c ===")
        print(f"  * R_dist (points): Mean={signals_df['r_dist'].mean()*100:.1f} | Median={signals_df['r_dist'].median()*100:.1f} | Min={signals_df['r_dist'].min()*100:.1f} | Max={signals_df['r_dist'].max()*100:.1f}")
        print(f"  * ATR[c] (points): Mean={signals_df['atr_c'].mean()*100:.1f} | Median={signals_df['atr_c'].median()*100:.1f} | Min={signals_df['atr_c'].min()*100:.1f} | Max={signals_df['atr_c'].max()*100:.1f}")
        print("=" * 70 + "\n")

    # 5. Chạy đối chứng không lọc vị thế (Unconstrained / Raw Signals)
    logger.info("5. Chạy đối chứng không lọc vị thế (enable_position_filter=False)...")
    sig_raw_df, summary_raw = generate_smc_signals(
        h1_df=h1_dev,
        sweeps_lifecycle_df=sweeps_lc,
        structure_df=struct_df,
        fvg_df=fvg_df,
        fvg_lifecycle_df=fvg_lc,
        m1_df=m1_df,
        split_name="development",
        enable_position_filter=False,
    )
    print("=== ĐỐI CHỨNG: TÍN HIỆU KHI KHÔNG BỊ CHẶN BỞI DROP_POSITION_BUSY ===")
    print(f"  * CANDIDATES_RAW              : {summary_raw.get('CANDIDATES_RAW', 0)}")
    print(f"  * ENTERED (No position filter): {summary_raw.get('ENTERED', 0)}")
    print(f"  * DROP_POSITION_BUSY bị triệt tiêu: {summary.get('DROP_POSITION_BUSY', 0)} tín hiệu")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
