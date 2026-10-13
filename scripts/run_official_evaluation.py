"""Chạy đánh giá chính thức evaluate_pass ĐÚNG MỘT LẦN trên lớp Development (2018-01-01 -> 2025-06-30).

Tuân thủ nghiêm ngặt PROTOCOL.md:
- 5 điều kiện PASS của lớp Development.
- Bảng thống kê chi tiết theo 8 năm lịch (2018-2025).
- Kiểm soát ngẫu nhiên Monte Carlo 1.000 lần với seed = 42.
- 2 số tham chiếu EXPLORATORY: always_buy_expectancy và always_sell_expectancy.
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd
from src.backtest.data import load_m1
from src.backtest.metrics import evaluate_pass

def main():
    print("=================================================================")
    print("CHẠY ĐÁNH GIÁ CHÍNH THỨC EVALUATE_PASS TRÊN LỚP DEVELOPMENT")
    print("=================================================================")

    trades_path = _REPO_ROOT / "artifacts" / "trades_development.parquet"
    if not trades_path.exists():
        trades_path = _REPO_ROOT / "artifacts" / "trades_development.csv"
    trades = pd.read_parquet(trades_path) if trades_path.suffix == ".parquet" else pd.read_csv(trades_path)
    print(f"Tổng số lệnh đã nạp: {len(trades)}")

    print("Đang nạp nến M1 Development để chạy kiểm soát ngẫu nhiên...")
    m1_data = load_m1("2018-01-01", "2025-06-30")

    print("Đang chạy evaluate_pass (1.000 mô phỏng Monte Carlo, seed = 42)...")
    results = evaluate_pass(
        trades=trades,
        m1=m1_data,
        n_simulations=1000,
        seed=42,
    )

    print("\n-----------------------------------------------------------------")
    print("KẾT QUẢ 5 ĐIỀU KIỆN TIÊU CHÍ PASS:")
    print("-----------------------------------------------------------------")
    for key in ["c1_total_trades", "c2_expectancy", "c3_profit_factor", "c4_positive_years", "c5_random_control"]:
        cond = results[key]
        status = "PASS [V]" if cond["passed"] else "FAIL [X]"
        print(f"[{status}] {cond['description']}")
        if key == "c1_total_trades":
            print(f"       Thực tế: {cond['actual']} lệnh (Ngưỡng: >= {cond['threshold']})")
        elif key == "c2_expectancy":
            print(f"       Thực tế: {cond['actual']:+.4f}R (Ngưỡng: >= {cond['threshold']:+.2f}R)")
        elif key == "c3_profit_factor":
            print(f"       Thực tế: {cond['actual']:.4f} (Ngưỡng: >= {cond['threshold']:.2f})")
        elif key == "c4_positive_years":
            print(f"       Thực tế: {cond['actual']}/{cond['total_years']} năm dương (Ngưỡng: >= {cond['threshold']})")
        elif key == "c5_random_control":
            rc = cond["details"]
            print(f"       Thực tế: p-value = {cond['actual_p']:.4f} (Ngưỡng: <= {cond['threshold']:.2f})")
            print(f"       Số lần ngẫu nhiên >= thật: {rc['n_exceed']} / 1000")
            print(f"       Kỳ vọng mô phỏng: mean = {rc['sim_mean']:+.4f}R, std = {rc['sim_std']:.4f}")
            print(f"       [EXPLORATORY] Always BUY Expectancy:  {rc['always_buy_expectancy']:+.4f}R")
            print(f"       [EXPLORATORY] Always SELL Expectancy: {rc['always_sell_expectancy']:+.4f}R")

    print("\n-----------------------------------------------------------------")
    print("BẢNG THỐNG KÊ CHI TIẾT THEO 8 NĂM LỊCH (2018-2025):")
    print("-----------------------------------------------------------------")
    by_year_df = results["c4_positive_years"]["by_year"]
    print(by_year_df.to_string(index=False))

    print("\n-----------------------------------------------------------------")
    summary = results["summary"]
    print(f"TỔNG KẾT TOÀN BỘ: all_passed = {results['all_passed']}")
    print(f"Win rate: {summary['win_rate']*100:.2f}% | Loss rate: {summary['loss_rate']*100:.2f}%")
    print(f"Tổng PnL: {summary['sum_pnl_r']:+.2f}R | Số lệnh thắng: {summary['n_win']}, thua: {summary['n_loss']}, hòa: {summary['n_breakeven']}")
    print("=================================================================\n")

if __name__ == "__main__":
    main()
