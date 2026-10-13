"""Script ghép nối kiểm thử pipeline lớp Development (2018-01-01 -> 2025-06-30).

Trình tự:
1. Nạp M1 lớp Development từ DuckDB view m1_utc.
2. Gắn ATR(H1, 14) đã dịch chống look-ahead qua attach_h1_atr.
3. Tính vùng Á qua compute_asian_range.
4. Sinh tín hiệu phá vỡ qua generate_breakout_signals.
5. Mô phỏng khớp lệnh chi tiết qua simulate_trades (theo quy ước C3).
6. In log đếm chất lượng dữ liệu:
   - Tổng số ngày, ngày vùng Á không hợp lệ, ngày vùng Á hợp lệ.
   - Ngày không có tín hiệu, ngày có tín hiệu nhưng không vào lệnh.
   - n_skipped, phân phối entry_delay.
7. Lưu bảng lệnh đã mô phỏng ra file để phục vụ bước vẽ biểu đồ kiểm tra bằng mắt (Plotly).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Đảm bảo import được src
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
import pandas as pd

from src.backtest.data import attach_h1_atr, load_m1
from src.backtest.signals import compute_asian_range, generate_breakout_signals
from src.backtest.simulate import simulate_trades

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("dev_pipeline")


def run_pipeline() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    logger.info("=== BẮT ĐẦU CHẠY PIPELINE LỚP DEVELOPMENT (2018-01-01 -> 2025-06-30) ===")

    # 1. Nạp M1 Development
    logger.info("1. Đang nạp nến M1 từ DuckDB view m1_utc...")
    m1_data = load_m1("2018-01-01", "2025-06-30")
    m1_df = m1_data.to_dataframe()
    logger.info("   -> Đã nạp %d nến M1 từ %s đến %s", len(m1_df), m1_df["time_utc"].min(), m1_df["time_utc"].max())

    # 2. Gắn ATR H1 (Wilder 14, chống look-ahead)
    logger.info("2. Đang tính và gắn ATR(H1, 14) không look-ahead...")
    m1_df = attach_h1_atr(m1_df)
    valid_atr_cnt = int(m1_df["atr"].notna().sum())
    logger.info("   -> Số nến M1 có ATR hợp lệ: %d / %d (%.2f%%)", valid_atr_cnt, len(m1_df), valid_atr_cnt / len(m1_df) * 100)

    # 3. Tính vùng Á
    logger.info("3. Đang tính vùng Á [00:00, 07:00) UTC...")
    asian_df = compute_asian_range(m1_df, mask_invalid=True)
    total_days = len(asian_df)
    valid_asian_days = int(asian_df["valid"].sum())
    invalid_asian_days = total_days - valid_asian_days
    logger.info("   -> Tổng số ngày giao dịch: %d", total_days)
    logger.info("   -> Số ngày vùng Á hợp lệ (n_bars >= 378): %d", valid_asian_days)
    logger.info("   -> Số ngày vùng Á KHÔNG hợp lệ (bị bỏ qua theo C2): %d", invalid_asian_days)

    # 4. Sinh tín hiệu phá vỡ
    logger.info("4. Đang sinh tín hiệu phá vỡ Breakout [07:00, 13:00) UTC...")
    signals_df, sig_summary = generate_breakout_signals(
        m1_df,
        asian_ranges=asian_df,
        return_summary=True,
    )
    logger.info("   -> Tổng số tín hiệu sinh ra: %d", len(signals_df))
    logger.info("   -> Chi tiết đếm tín hiệu:")
    for k, v in sig_summary.items():
        logger.info("      * %s: %s", k, v)

    # 5. Mô phỏng khớp lệnh chi tiết (C3)
    logger.info("5. Đang mô phỏng khớp lệnh M1 chi tiết (theo C3)...")
    trades_df, sim_summary = simulate_trades(
        signals_df,
        m1=m1_df,
        return_summary=True,
    )
    logger.info("   -> Tổng số lệnh đã mô phỏng: %d", len(trades_df))
    logger.info("   -> Chi tiết kết quả thoát lệnh:")
    for k, v in sim_summary.items():
        logger.info("      * %s: %s", k, v)

    # 6. Thống kê phân phối entry_delay
    delays = trades_df["entry_delay"].dropna().to_numpy()
    if len(delays) > 0:
        logger.info("6. Phân phối entry_delay (phút từ khi có tín hiệu đến khi vào lệnh):")
        logger.info("   * Số lệnh có delay: %d", len(delays))
        logger.info("   * Min: %.1f phút | Max: %.1f phút | Mean: %.2f phút", np.min(delays), np.max(delays), np.mean(delays))
        logger.info("   * Phân vị: p25=%.1f | p50 (median)=%.1f | p75=%.1f | p90=%.1f | p99=%.1f",
                    np.percentile(delays, 25), np.percentile(delays, 50),
                    np.percentile(delays, 75), np.percentile(delays, 90),
                    np.percentile(delays, 99))
    else:
        logger.warning("6. Không có số liệu entry_delay!")

    # 7. Lưu bảng lệnh phục vụ vẽ biểu đồ và kiểm tra
    out_dir = _REPO_ROOT / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_parquet = out_dir / "trades_development.parquet"
    out_csv = out_dir / "trades_development.csv"
    trades_df.to_parquet(out_parquet, index=False)
    trades_df.to_csv(out_csv, index=False)
    logger.info("7. Đã lưu bảng lệnh vào:")
    logger.info("   * %s", out_parquet)
    logger.info("   * %s", out_csv)

    return m1_df, signals_df, trades_df


if __name__ == "__main__":
    run_pipeline()
