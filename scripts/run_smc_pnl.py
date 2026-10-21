"""Script chạy đánh giá P&L chính thức ĐÚNG 01 LẦN cho chiến lược SMC Sweep Confirmation v1.

Tuân thủ nghiêm ngặt PROTOCOL.md (Mục 4.3, 4.4, 4.5 & 4.6, prereg-smc-v1.7):
1. Chốt chặn Git phiên bản: Bắt buộc working tree sạch sẽ và HEAD khớp đúng tag đã đăng ký (mặc định prereg-smc-v1.7).
2. Quản lý Log & Artifact không ghi đè: Mỗi lần chạy có mã run_id (timestamp) riêng, lưu log riêng
   và ghi tiếp vào master cumulative log (mode="a"). Artifact lưu bản versioned kèm bản latest.
3. Phạm vi thực thi: Chỉ sinh tín hiệu và tính P&L trên lớp Development (2018-01-01 -> 2025-06-30).
   Nạp H1 từ 2014 thuần túy để warm-up ATR(14) và bộ nhớ cấu trúc/FVG. Bảo toàn tuyệt đối Holdout.
4. Thứ tự kiểm toán an toàn (Tránh rò rỉ P&L):
   Toàn bộ chốt chặn số lệnh theo năm và mô phỏng C4(c) identity BẮT BUỘC thực hiện và vượt qua
   TRƯỚC KHI in bất kỳ chỉ số hiệu năng P&L nào.
5. Hỗ trợ chế độ kiểm tra trước (--check-only):
   Xác minh toàn bộ các chốt chặn kỹ thuật mà không in và không lưu trữ bất kỳ chỉ số P&L nào.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime
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

DEFAULT_PREREG_TAG = "prereg-smc-v1.7"


def verify_git_clean_and_tagged(expected_tag: str = DEFAULT_PREREG_TAG) -> tuple[str, list[str]]:
    """Kiểm tra trạng thái Git: working tree phải sạch sẽ và HEAD phải có tag đăng ký."""
    # 1. Kiểm tra working tree
    status_proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    raw_uncommitted = status_proc.stdout.strip()
    # Bỏ qua các file sinh ra trong runtime logs/ và artifacts/
    uncommitted_lines = [
        line for line in raw_uncommitted.splitlines()
        if line.strip() and not any(
            line[3:].strip().replace("\\", "/").startswith(prefix)
            for prefix in ["logs/", "artifacts/"]
        )
    ]
    if uncommitted_lines:
        uncommitted_str = "\n".join(uncommitted_lines)
        raise RuntimeError(
            f"VI PHẠM QUY TẮC ĐÓNG BĂNG: Working tree chưa sạch sẽ trước khi chạy P&L!\n"
            f"Các tệp thay đổi chưa commit:\n{uncommitted_str}\n"
            f"Bắt buộc phải commit và gắn tag trước khi thực thi."
        )

    # 2. Lấy commit hash
    commit_proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    commit_hash = commit_proc.stdout.strip()

    # 3. Lấy tags tại HEAD
    tags_proc = subprocess.run(
        ["git", "tag", "--points-at", "HEAD"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    tags_at_head = [t.strip() for t in tags_proc.stdout.strip().splitlines() if t.strip()]

    if expected_tag not in tags_at_head:
        raise RuntimeError(
            f"VI PHẠM QUY TẮC ĐÓNG BĂNG: Commit hiện tại ({commit_hash}) chưa được gắn tag '{expected_tag}'!\n"
            f"Các tag hiện có tại HEAD: {tags_at_head}\n"
            f"Bắt buộc phải gắn đúng tag {expected_tag} trước khi thực thi."
        )

    return commit_hash, tags_at_head


def setup_execution_logger(run_id: str, check_only: bool = False) -> tuple[logging.Logger, Path]:
    """Khởi tạo logger lưu log riêng cho run_id và ghi tiếp vào master cumulative log."""
    log_dir = _REPO_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)

    prefix = "smc_check" if check_only else "smc_pnl"
    run_log_file = log_dir / f"{prefix}_run_{run_id}.log"
    master_log_file = log_dir / "smc_pnl_all_runs.log"

    logger = logging.getLogger(f"{prefix}_{run_id}")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")

    # 1. Run-specific log file
    fh_run = logging.FileHandler(str(run_log_file), mode="w", encoding="utf-8")
    fh_run.setFormatter(formatter)
    logger.addHandler(fh_run)

    # 2. Master cumulative log file (luôn mở mode append 'a' để không bao giờ ghi đè)
    if not check_only:
        fh_master = logging.FileHandler(str(master_log_file), mode="a", encoding="utf-8")
        fh_master.setFormatter(formatter)
        logger.addHandler(fh_master)

    # 3. Console stream
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    return logger, run_log_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chạy đánh giá P&L chính thức cho SMC v1 (PROTOCOL Mục 4).")
    parser.add_argument(
        "--tag",
        type=str,
        default=DEFAULT_PREREG_TAG,
        help=f"Tag Git đăng ký kỳ vọng tại HEAD (mặc định: {DEFAULT_PREREG_TAG})",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Chế độ kiểm tra chốt chặn git, assert số lệnh, C4(c) identity mà KHÔNG in và KHÔNG lưu P&L",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger, run_log_file = setup_execution_logger(run_id, check_only=args.check_only)

    try:
        logger.info("=" * 80)
        mode_str = "KIỂM TRA TRƯỚC (--check-only)" if args.check_only else "CHẠY CHÍNH THỨC (ĐÚNG 01 LẦN)"
        logger.info("BẮT ĐẦU ĐÁNH GIÁ STT 8: SMC SWEEP CONFIRMATION V1 [%s]", mode_str)
        logger.info("Mã phiên chạy (Run ID): %s", run_id)
        logger.info("Phạm vi: LỚP DEVELOPMENT (2018-01-01 -> 2025-06-30)")
        logger.info("=" * 80)

        # 1. Chốt chặn Git
        commit_hash, tags_at_head = verify_git_clean_and_tagged(expected_tag=args.tag)
        logger.info("CHỐT CHẶN GIT HOÀN TẤT:")
        logger.info("  * Commit Hash : %s", commit_hash)
        logger.info("  * Tags tại HEAD: %s (Khớp chuẩn %s)", tags_at_head, args.tag)
        logger.info("  * Working tree: SẠCH SẼ (Clean, 0 tệp chưa commit)")

        start_date = "2018-01-01"
        end_date = "2025-06-30"

        # 2. Nạp H1 Development (kèm warm-up từ 2014 cho ATR14 và bộ nhớ cấu trúc)
        logger.info("\n2. Nạp nến H1 kèm warm-up 2014 để tính ATR(14)...")
        h1_full = load_h1(start_date=None, end_date=end_date)
        h1_full["atr_14"] = compute_wilder_atr(
            h1_full["high"].to_numpy(),
            h1_full["low"].to_numpy(),
            h1_full["close"].to_numpy(),
            period=14,
        )
        h1_dev = h1_full[h1_full["time_utc"] >= pd.to_datetime(start_date)].reset_index(drop=True)
        logger.info("   -> Số hàng H1 Development: %d hàng", len(h1_dev))

        # Chốt chặn Holdout
        max_h1_time = pd.to_datetime(h1_dev["time_utc"].max())
        assert max_h1_time <= pd.to_datetime("2025-06-30 23:00:00"), f"Vi phạm chốt chặn Holdout: {max_h1_time}"

        # 3. Nạp Sweeps, Structure, FVG
        logger.info("3. Nạp các bảng SMC: Sweeps, Structure, FVG...")
        _, sweeps_lc = load_liquidity(start_date=start_date, end_date=end_date)
        struct_df = load_structure(start_date=start_date, end_date=end_date)
        fvg_df, fvg_lc = load_fvg(start_date=start_date, end_date=end_date)

        # 4. Nạp M1 Development
        logger.info("4. Nạp dữ liệu nến M1 Development...")
        m1_data = load_m1(start_date=start_date, end_date=end_date)
        m1_df = m1_data.to_dataframe()
        logger.info("   -> Số hàng nến M1: %d hàng", len(m1_df))

        # 5. Sinh tín hiệu SMC v1 (có lọc vị thế)
        logger.info("5. Khởi chạy generate_smc_signals trên Development...")
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

        total_entered = len(signals_df)
        logger.info("   -> Tổng số tín hiệu ENTERED: %d", total_entered)
        assert total_entered == 747, f"Số lượng lệnh ENTERED không khớp biên bản 747: {total_entered}"

        # 6. Tính PnL theo R và gắn vào bảng lệnh
        trades_df = attach_pnl_r(signals_df)

        # =====================================================================
        # CHỐT CHẶN KIỂM TOÁN AN TOÀN TRƯỚC KHI IN BẤT KỲ SỐ LIỆU P&L NÀO
        # =====================================================================
        logger.info("\n6. Thực hiện các chốt chặn kiểm toán độc lập...")

        # 6.1 Kiểm tra phân rã số lệnh theo năm vào
        by_entry_df = breakdown_by_year(trades_df, by_column="entry")
        expected_entry_counts = [83, 93, 85, 107, 108, 112, 108, 51]
        actual_entry_counts = by_entry_df["n_trades"].tolist()
        assert actual_entry_counts == expected_entry_counts, (
            f"Bảng số lệnh theo năm vào không khớp bảng kiểm toán: actual={actual_entry_counts}, expected={expected_entry_counts}"
        )
        logger.info("  * [KHỚP 100%] Số lệnh từng năm vào khớp chính xác bảng kiểm toán: %s", actual_entry_counts)

        # 6.2 Kiểm tra phân rã số lệnh theo năm thoát
        by_exit_df = breakdown_by_year(trades_df, by_column="exit")
        expected_exit_counts = [83, 92, 85, 108, 108, 111, 109, 51]
        actual_exit_counts = by_exit_df["n_trades"].tolist()
        assert actual_exit_counts == expected_exit_counts, (
            f"Bảng số lệnh theo năm thoát không khớp bảng kiểm toán: actual={actual_exit_counts}, expected={expected_exit_counts}"
        )
        logger.info("  * [KHỚP 100%] Số lệnh từng năm thoát khớp chính xác bảng kiểm toán: %s", actual_exit_counts)

        # 6.3 Mô phỏng hai nhánh C4(c) và assert đẳng thức bit-for-bit nhánh cùng hướng
        logger.info("  * Chạy simulate_c4c_pairs (kèm chốt chặn đẳng thức cùng hướng bit-for-bit)...")
        pnl_buys, pnl_sells = simulate_c4c_pairs(
            trades_df=trades_df,
            m1_df=m1_df,
            h1_df=h1_dev,
            split_end_str=f"{end_date} 23:59:00",
        )
        logger.info("  * [KHỚP 100%] Đẳng thức C4(c) nhánh cùng hướng: ĐÃ QUA TOÀN BỘ (Bit-for-bit).")

        # =====================================================================
        # NẾU Ở CHẾ ĐỘ CHECK-ONLY: KẾT THÚC SỚM KHÔNG IN VÀ KHÔNG LƯU P&L
        # =====================================================================
        if args.check_only:
            logger.info("\n" + "=" * 80)
            logger.info("KẾT QUẢ KIỂM TRA TRƯỚC (--check-only) HOÀN TẤT THÀNH CÔNG:")
            logger.info("  [PASS] Git working tree sạch sẽ (0 uncommitted)")
            logger.info("  [PASS] Git HEAD có tag chuẩn: %s", args.tag)
            logger.info("  [PASS] Tổng số lệnh: 747 lệnh (Khớp chính xác)")
            logger.info("  [PASS] Phân rã 8 năm theo năm vào: %s", actual_entry_counts)
            logger.info("  [PASS] Phân rã 8 năm theo năm thoát: %s", actual_exit_counts)
            logger.info("  [PASS] Mô phỏng C4(c) identity bit-for-bit: ĐẠT 100%")
            logger.info("  [AN TOÀN] Không in chỉ số hiệu năng và không lưu trữ P&L artifacts.")
            logger.info("Hệ thống hoàn toàn sẵn sàng cho lệnh chạy chính thức duy nhất!")
            logger.info("=" * 80)
            return

        # =====================================================================
        # CHẠY CHÍNH THỨC: IN VÀ LƯU TRỮ KẾT QUẢ P&L
        # =====================================================================
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

        # Tính Max Drawdown
        logger.info("\n7. Tính Max Drawdown theo thứ tự thời điểm thoát lệnh (exit_time)...")
        dd_res = compute_drawdown_series(trades_df, sort_by_exit=True)
        mdd_r = dd_res["mdd_r"]
        peak_r = dd_res["peak_r"]
        logger.info("  * Đỉnh vốn cao nhất (Peak R)      : %+.4f R", peak_r)
        logger.info("  * Max Drawdown theo R (MDD in R)  : %.4f R", mdd_r)

        # In 2 bảng phân rã
        logger.info("\n8. Bảng phân rã chi tiết 8 năm lịch (2018–2025):")
        logger.info("--- BẢNG 1: THEO NĂM VÀO LỆNH (CHÍNH THỨC THEO C4) ---")
        logger.info("\n%s", by_entry_df.to_string(index=False))
        n_pos_entry = int(by_entry_df.attrs["n_positive_years"])
        logger.info("  -> Số năm có kỳ vọng dương (theo năm vào): %d / 8 năm", n_pos_entry)

        logger.info("\n--- BẢNG 2: THEO NĂM THOÁT LỆNH (THAM CHIẾU MINH BẠCH) ---")
        logger.info("\n%s", by_exit_df.to_string(index=False))
        n_pos_exit = int(by_exit_df.attrs["n_positive_years"])
        logger.info("  -> Số năm có kỳ vọng dương (theo năm thoát): %d / 8 năm", n_pos_exit)

        # Chạy Monte Carlo permutation
        logger.info("\n9. Chạy 1.000 lần hoán vị Monte Carlo vector hóa (seed = 42)...")
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

        # Đánh giá 5 tiêu chí PASS
        logger.info("\n" + "=" * 80)
        logger.info("ĐỐI CHIẾU 5 TIÊU CHÍ PASS TRÊN LỚP DEVELOPMENT (PROTOCOL MỤC 5):")
        logger.info("=" * 80)

        c1_passed = bool(total_entered >= 800)
        c2_passed = bool(perf_summary["expectancy"] >= 0.05)
        c3_passed = bool(perf_summary["profit_factor"] >= 1.10)
        c4_passed = bool(n_pos_entry >= 5)
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

        # Lưu trữ kết quả artifacts
        artifacts_dir = _REPO_ROOT / "artifacts"
        artifacts_dir.mkdir(exist_ok=True)

        parquet_run = artifacts_dir / f"smc_trades_development_{run_id}.parquet"
        csv_run = artifacts_dir / f"smc_trades_development_{run_id}.csv"
        trades_df.to_parquet(parquet_run, index=False)
        trades_df.to_csv(csv_run, index=False)

        # Lưu bản latest
        trades_df.to_parquet(artifacts_dir / "smc_trades_development.parquet", index=False)
        trades_df.to_csv(artifacts_dir / "smc_trades_development.csv", index=False)
        logger.info("Đã lưu bảng lệnh chi tiết: %s & bản latest", parquet_run.name)

        summary_data = {
            "strategy": "SMC Sweep Confirmation v1",
            "run_id": run_id,
            "git_commit": commit_hash,
            "git_tags": tags_at_head,
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
            "yearly_entry_counts": dict(zip(by_entry_df["year"], by_entry_df["n_trades"])),
            "yearly_exit_counts": dict(zip(by_exit_df["year"], by_exit_df["n_trades"])),
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

        json_run = artifacts_dir / f"smc_pnl_summary_{run_id}.json"
        with open(json_run, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=2, ensure_ascii=False)
        with open(artifacts_dir / "smc_pnl_summary.json", "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=2, ensure_ascii=False)

        logger.info("Đã lưu tóm tắt P&L: %s & bản latest", json_run.name)
        logger.info("HOÀN TẤT ĐÁNH GIÁ P&L CHÍNH THỨC THÀNH CÔNG.")

    except Exception as exc:
        logger.exception("SỰ CỐ KỸ THUẬT XẢY RA TRONG QUÁ TRÌNH CHẠY P&L (Lưu log ngoại lệ): %s", exc)
        raise


if __name__ == "__main__":
    main()
