"""Script chạy đánh giá P&L chính thức ĐÚNG 01 LẦN cho chiến lược SMC Sweep Confirmation v1.

Tuân thủ nghiêm ngặt PROTOCOL.md (Mục 4.3 & 4.4, prereg-smc-v1.5):
1. Phạm vi thực thi: Chỉ chạy duy nhất trên lớp Development (2018-01-01 -> 2025-06-30).
   Bảo vệ tuyệt đối Robustness (2014-07-01 -> 2017-12-31) và Holdout (2025-07-01 -> 2026-10-13).
2. Danh mục chỉ số bắt buộc:
   - Tổng P&L (R), Kỳ vọng ròng (E), Profit Factor (PF), Win Rate (%).
   - Cơ cấu 5 lý do thoát: TP, SL, SL_GAP, TIME_EXIT, DATA_END.
   - Bảng phân rã 8 năm lịch (2018–2025) theo năm vào lệnh (chuẩn C4) và năm thoát lệnh.
   - Max Drawdown (MDD in R) sắp xếp theo thời điểm thoát lệnh (exit_time).
   - Kiểm soát ngẫu nhiên Monte Carlo C4(c) (1.000 hoán vị, seed = 42, p <= 0.025).
3. Đánh giá 5 tiêu chí PASS (Gắn nhãn chính thức: Không thể PASS do thiếu mẫu 747/800).
4. Lưu toàn bộ kết quả vào artifacts/ và ghi log minh bạch vào logs/smc_pnl_run.log.
"""

from __future__ import annotations

import json
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
    attach_pnl_r,
    breakdown_by_year,
    compute_drawdown_series,
    generate_smc_signals,
    load_fvg,
    load_liquidity,
    load_structure,
    run_monte_carlo_permutation,
    simulate_c4c_pairs,
    summarize_trades,
)

# Thiết lập Logger
log_dir = _REPO_ROOT / "logs"
log_dir.mkdir(exist_ok=True)
log_file = log_dir / "smc_pnl_run.log"

logger = logging.getLogger("smc_pnl_run")
logger.setLevel(logging.INFO)
formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")

fh = logging.FileHandler(str(log_file), mode="w", encoding="utf-8")
fh.setFormatter(formatter)
logger.addHandler(fh)

sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(formatter)
logger.addHandler(sh)


def main():
    logger.info("=" * 80)
    logger.info("BẮT ĐẦU CHẠY ĐÁNH GIÁ P&L CHÍNH THỨC STT 8: SMC SWEEP CONFIRMATION V1")
    logger.info("Trạng thái kiểm toán: ĐÃ ĐÓNG BĂNG TẠI TAG prereg-smc-v1.5")
    logger.info("Phạm vi: LỚP DEVELOPMENT (2018-01-01 -> 2025-06-30)")
    logger.info("=" * 80)

    start_date = "2018-01-01"
    end_date = "2025-06-30"

    # 1. Nạp H1 Development (kèm warm-up cho ATR14)
    logger.info("1. Nạp nến H1 kèm warm-up để tính ATR(14)...")
    h1_full = load_h1(start_date=None, end_date=end_date)
    h1_full["atr_14"] = compute_wilder_atr(
        h1_full["high"].to_numpy(),
        h1_full["low"].to_numpy(),
        h1_full["close"].to_numpy(),
        period=14,
    )
    h1_dev = h1_full[h1_full["time_utc"] >= pd.to_datetime(start_date)].reset_index(drop=True)
    logger.info("   -> Số hàng H1 Development: %d hàng", len(h1_dev))

    # Chốt chặn an toàn: Tuyệt đối không chứa ngày thuộc Holdout (> 2025-06-30)
    max_h1_time = pd.to_datetime(h1_dev["time_utc"].max())
    assert max_h1_time <= pd.to_datetime("2025-06-30 23:00:00"), f"Vi phạm chốt chặn Holdout: {max_h1_time}"

    # 2. Nạp Sweeps, Structure, FVG
    logger.info("2. Nạp các bảng SMC: Sweeps, Structure, FVG...")
    _, sweeps_lc = load_liquidity(start_date=start_date, end_date=end_date)
    struct_df = load_structure(start_date=start_date, end_date=end_date)
    fvg_df, fvg_lc = load_fvg(start_date=start_date, end_date=end_date)

    # 3. Nạp M1 Development
    logger.info("3. Nạp dữ liệu nến M1 Development...")
    m1_data = load_m1(start_date=start_date, end_date=end_date)
    m1_df = m1_data.to_dataframe()
    logger.info("   -> Số hàng nến M1: %d hàng", len(m1_df))

    # 4. Sinh tín hiệu SMC v1 (có lọc vị thế)
    logger.info("4. Khởi chạy generate_smc_signals trên Development...")
    signals_df, summary_counts = generate_smc_signals(
        h1_df=h1_dev,
        sweeps_lifecycle_df=sweeps_lc,
        structure_df=struct_df,
        fvg_df=fvg_df,
        fvg_lifecycle_df=fvg_lc,
        m1_df=m1_df,
        split_name="development",
        enable_position_filter=True,
    )

    # Kiểm tra tính tự nhất quán của số lượng tín hiệu
    total_entered = len(signals_df)
    logger.info("   -> Tổng số tín hiệu ENTERED: %d", total_entered)
    assert total_entered == 747, f"Số lượng lệnh ENTERED không khớp biên bản 747: {total_entered}"

    # 5. Tính PnL theo R và gắn vào bảng lệnh
    logger.info("5. Tính toán chỉ số P&L (R-multiple) cho từng lệnh...")
    trades_df = attach_pnl_r(signals_df)

    # 6. Tổng kết hiệu năng cốt lõi
    perf_summary = summarize_trades(trades_df)
    logger.info("\n" + "=" * 80)
    logger.info("KẾT QUẢ TỔNG HỢP HIỆU SUẤT CỐT LÕI (DEVELOPMENT 2018-2025):")
    logger.info("=" * 80)
    logger.info("  * Tổng số lệnh thực thi (N)       : %d lệnh", perf_summary["total_trades"])
    logger.info("  * Tổng P&L ròng (Sum R)           : %+.4f R", perf_summary["sum_pnl_r"])
    logger.info("  * Kỳ vọng ròng (Expectancy E)     : %+.4f R/lệnh", perf_summary["expectancy"])
    logger.info("  * Profit Factor (PF)              : %.4f", perf_summary["profit_factor"])
    logger.info("  * Tỷ lệ thắng (Win Rate)          : %.2f%% (%d thắng, %d thua, %d hòa)",
                perf_summary["win_rate"] * 100, perf_summary["n_win"], perf_summary["n_loss"], perf_summary["n_breakeven"])

    logger.info("\n--- CƠ CẤU PHÂN BỐ LÝ DO THOÁT LỆNH ---")
    for r, c in perf_summary["exit_counts"].items():
        pct = (c / total_entered) * 100
        logger.info("  * %-12s: %4d lệnh (%5.2f%%)", r, c, pct)

    # 7. Tính Max Drawdown sắp xếp theo thời điểm thoát lệnh (exit_time)
    logger.info("\n7. Tính Max Drawdown theo thứ tự thời điểm thoát lệnh...")
    dd_res = compute_drawdown_series(trades_df, sort_by_exit=True)
    mdd_r = dd_res["mdd_r"]
    peak_r = dd_res["peak_r"]
    logger.info("  * Đỉnh vốn cao nhất (Peak R)      : %+.4f R", peak_r)
    logger.info("  * Max Drawdown theo R (MDD in R)  : %.4f R", mdd_r)

    # 8. Bảng phân rã 8 năm lịch chi tiết (2018–2025)
    logger.info("\n8. Bảng phân rã chi tiết 8 năm lịch (2018–2025):")
    logger.info("--- BẢNG 1: THEO NĂM VÀO LỆNH (CHÍNH THỨC THEO C4) ---")
    by_entry_df = breakdown_by_year(trades_df, by_column="entry")
    logger.info("\n%s", by_entry_df.to_string(index=False))
    n_pos_entry = int(by_entry_df.attrs["n_positive_years"])
    logger.info("  -> Số năm có kỳ vọng dương (theo năm vào): %d / 8 năm", n_pos_entry)

    logger.info("\n--- BẢNG 2: THEO NĂM THOÁT LỆNH (THAM CHIẾU MINH BẠCH) ---")
    by_exit_df = breakdown_by_year(trades_df, by_column="exit")
    logger.info("\n%s", by_exit_df.to_string(index=False))
    n_pos_exit = int(by_exit_df.attrs["n_positive_years"])
    logger.info("  -> Số năm có kỳ vọng dương (theo năm thoát): %d / 8 năm", n_pos_exit)

    # 9. Kiểm soát ngẫu nhiên Monte Carlo C4(c) (1.000 mô phỏng, seed = 42)
    logger.info("\n9. Chạy kiểm soát ngẫu nhiên Monte Carlo C4(c) (1.000 mô phỏng, seed = 42)...")
    logger.info("   Đang mô phỏng hai nhánh BUY và SELL cho từng lệnh trên M1...")
    pnl_buys, pnl_sells = simulate_c4c_pairs(
        trades_df=trades_df,
        m1_df=m1_df,
        h1_df=h1_dev,
        split_end_str=f"{end_date} 23:59:00",
    )
    logger.info("   Đã hoàn tất mô phỏng hai nhánh. Chạy hoán vị Monte Carlo 1.000 lần...")
    mc_res = run_monte_carlo_permutation(
        pnl_buys=pnl_buys,
        pnl_sells=pnl_sells,
        actual_expectancy=perf_summary["expectancy"],
        n_simulations=1000,
        seed=42,
    )
    p_value = mc_res["p_value"]
    logger.info("  * Kết quả Monte Carlo C4(c)       : p-value = %.4f (vượt: %d / 1000)", p_value, mc_res["n_exceed"])
    logger.info("  * Kỳ vọng trung bình ngẫu nhiên   : %+.4f R (Std: %.4f)", mc_res["sim_mean"], mc_res["sim_std"])
    logger.info("  * [THAM CHIẾU] Always BUY Exp     : %+.4f R", mc_res["always_buy_expectancy"])
    logger.info("  * [THAM CHIẾU] Always SELL Exp    : %+.4f R", mc_res["always_sell_expectancy"])

    # 10. Đánh giá toàn bộ 5 tiêu chí PASS theo PROTOCOL Mục 5
    logger.info("\n" + "=" * 80)
    logger.info("ĐỐI CHIẾU 5 TIÊU CHÍ PASS TRÊN LỚP DEVELOPMENT (PROTOCOL MỤC 5):")
    logger.info("=" * 80)

    c1_passed = bool(total_entered >= 800)
    c2_passed = bool(perf_summary["expectancy"] >= 0.05)
    c3_passed = bool(perf_summary["profit_factor"] >= 1.10)
    c4_passed = bool(n_pos_entry >= 5)
    # FWER threshold: N_trials = 2 => p <= 0.025
    c5_passed = bool(p_value <= 0.025)

    all_passed = bool(c1_passed and c2_passed and c3_passed and c4_passed and c5_passed)

    logger.info("[%-7s] Điều kiện 1 (Cỡ mẫu)     : %d lệnh (Ngưỡng: >= 800) -> %s",
                "PASS" if c1_passed else "FAIL", total_entered, "ĐẠT" if c1_passed else "KHÔNG ĐẠT (THIẾU MẪU)")
    logger.info("[%-7s] Điều kiện 2 (Kỳ vọng)    : %+.4f R/lệnh (Ngưỡng: >= +0.05R) -> %s",
                "PASS" if c2_passed else "FAIL", perf_summary["expectancy"], "ĐẠT" if c2_passed else "KHÔNG ĐẠT")
    logger.info("[%-7s] Điều kiện 3 (Profit Factor): %.4f (Ngưỡng: >= 1.10) -> %s",
                "PASS" if c3_passed else "FAIL", perf_summary["profit_factor"], "ĐẠT" if c3_passed else "KHÔNG ĐẠT")
    logger.info("[%-7s] Điều kiện 4 (Tính ổn định): %d/8 năm dương (Ngưỡng: >= 5/8 năm) -> %s",
                "PASS" if c4_passed else "FAIL", n_pos_entry, "ĐẠT" if c4_passed else "KHÔNG ĐẠT")
    logger.info("[%-7s] Điều kiện 5 (Kiểm soát MC): p = %.4f (Ngưỡng: <= 0.025, N=2) -> %s",
                "PASS" if c5_passed else "FAIL", p_value, "ĐẠT" if c5_passed else "KHÔNG ĐẠT")

    logger.info("-" * 80)
    logger.info("TỔNG KẾT ĐÁNH GIÁ CHÍNH THỨC: %s", "PASS TOÀN BỘ" if all_passed else "FAIL (KHÔNG ĐẠT)")
    logger.info("Ghi chú kiểm toán: STT 8 chính thức FAIL Điều kiện 1 (thiếu mẫu 747/800).")
    logger.info("Kết quả P&L là phân tích mô tả thuần túy, không dùng để tinh chỉnh tham số.")
    logger.info("Số thử nghiệm tích lũy cho bản tiếp theo: N_trials = 3 (FWER p <= 0.0167).")
    logger.info("=" * 80 + "\n")

    # 11. Lưu trữ kết quả artifacts
    artifacts_dir = _REPO_ROOT / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)

    parquet_out = artifacts_dir / "smc_trades_development.parquet"
    csv_out = artifacts_dir / "smc_trades_development.csv"
    trades_df.to_parquet(parquet_out, index=False)
    trades_df.to_csv(csv_out, index=False)
    logger.info("Đã lưu bảng lệnh chi tiết: %s & %s", parquet_out.name, csv_out.name)

    # Lưu json summary
    summary_data = {
        "strategy": "SMC Sweep Confirmation v1",
        "split": "development",
        "start_date": start_date,
        "end_date": end_date,
        "total_trades": total_entered,
        "sum_pnl_r": perf_summary["sum_pnl_r"],
        "expectancy": perf_summary["expectancy"],
        "profit_factor": perf_summary["profit_factor"],
        "win_rate": perf_summary["win_rate"],
        "loss_rate": perf_summary["loss_rate"],
        "mdd_r": mdd_r,
        "peak_r": peak_r,
        "exit_counts": perf_summary["exit_counts"],
        "positive_years_entry": n_pos_entry,
        "positive_years_exit": n_pos_exit,
        "monte_carlo": {
            "p_value": p_value,
            "n_exceed": mc_res["n_exceed"],
            "sim_mean": mc_res["sim_mean"],
            "sim_std": mc_res["sim_std"],
            "always_buy_expectancy": mc_res["always_buy_expectancy"],
            "always_sell_expectancy": mc_res["always_sell_expectancy"],
        },
        "pass_criteria": {
            "all_passed": all_passed,
            "c1_total_trades": {"actual": total_entered, "threshold": 800, "passed": c1_passed},
            "c2_expectancy": {"actual": perf_summary["expectancy"], "threshold": 0.05, "passed": c2_passed},
            "c3_profit_factor": {"actual": perf_summary["profit_factor"], "threshold": 1.10, "passed": c3_passed},
            "c4_positive_years": {"actual": n_pos_entry, "threshold": 5, "passed": c4_passed},
            "c5_random_control": {"actual_p": p_value, "threshold": 0.025, "passed": c5_passed},
        },
        "audit_note": "FAIL Điều kiện 1 (thiếu mẫu 747/800); Phân tích mô tả thuần túy, N_trials tăng lên 3.",
    }

    json_out = artifacts_dir / "smc_pnl_summary.json"
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2, ensure_ascii=False)
    logger.info("Đã lưu tóm tắt P&L: %s", json_out.name)
    logger.info("HOÀN TẤT ĐÁNH GIÁ P&L THÀNH CÔNG.")


if __name__ == "__main__":
    main()
