import pandas as pd
from src.backtest.data import load_h1
from src.backtest.smc.swings import detect_swings

df = load_h1("2020-01-02", "2020-01-10")
sw = detect_swings(df, k=3)

print(f"Total swings in week 1 Jan 2020: {len(sw)}\n")

for idx in range(min(5, len(sw))):
    r = sw.iloc[idx]
    i = int(r["bar_index"])
    p = float(r["price"])
    st = r["swing_type"]
    tc = r["time_candle"]
    ta = r["time_available"]
    
    col = "high" if st == "HIGH" else "low"
    left_vals = [df.iloc[i - j][col] for j in [3, 2, 1]]
    right_vals = [df.iloc[i + j][col] for j in [1, 2, 3]]
    
    if st == "HIGH":
        left_ok = all(p > v for v in left_vals)
        right_ok = all(p > v for v in right_vals)
    else:
        left_ok = all(p < v for v in left_vals)
        right_ok = all(p < v for v in right_vals)
        
    c3_close = df.iloc[i + 3]["time_utc"] + pd.Timedelta(hours=1)
    
    print(f"=== Swing #{idx}: {st} at {tc} (bar {i}), Price = {p:.2f} ===")
    print(f"  Available at: {ta}")
    print(f"  Left 3 bars (i-3, i-2, i-1) {col}: {left_vals}")
    print(f"    -> All satisfied {st} condition? {left_ok}")
    print(f"  Right 3 bars (i+1, i+2, i+3) {col}: {right_vals}")
    print(f"    -> All satisfied {st} condition? {right_ok}")
    print(f"  Bar i+3 close time: {c3_close}")
    print(f"    -> Matches time_available? {c3_close == ta}\n")
