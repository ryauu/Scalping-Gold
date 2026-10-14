import pandas as pd
from src.backtest.data import load_h1
from src.backtest.smc.structure import detect_structure

df_2014 = load_h1("2014-06-01", "2020-01-01")
df_2016 = load_h1("2016-01-01", "2020-01-01")
df_2017_01 = load_h1("2017-01-01", "2020-01-01")
df_2017_06 = load_h1("2017-06-01", "2020-01-01")

s_2014 = detect_structure(df_2014)
s_2016 = detect_structure(df_2016)
s_2017_01 = detect_structure(df_2017_01)
s_2017_06 = detect_structure(df_2017_06)

ev_2014 = s_2014[s_2014["time_available"] >= "2018-01-01"].reset_index(drop=True)
ev_2016 = s_2016[s_2016["time_available"] >= "2018-01-01"].reset_index(drop=True)
ev_2017_01 = s_2017_01[s_2017_01["time_available"] >= "2018-01-01"].reset_index(drop=True)
ev_2017_06 = s_2017_06[s_2017_06["time_available"] >= "2018-01-01"].reset_index(drop=True)

print("Event counts from 2018-01-01 to 2020-01-01:")
print(f"  Warmup from 2014-06 (42m): {len(ev_2014)}")
print(f"  Warmup from 2016-01 (24m): {len(ev_2016)}")
print(f"  Warmup from 2017-01 (12m): {len(ev_2017_01)}")
print(f"  Warmup from 2017-06  (6m): {len(ev_2017_06)}")

# Check 2014 vs 2016
print("\nComparing 2014-06 vs 2016-01:")
if len(ev_2014) == len(ev_2016) and (ev_2014["time_candle"].values == ev_2016["time_candle"].values).all():
    print("  -> 2014-06 and 2016-01 are 100% IDENTICAL!")
else:
    print(f"  -> Count diff: {len(ev_2014)} vs {len(ev_2016)}")

# Print first 10 events of each to see where divergence starts
print("\nFirst 10 events comparison:")
for i in range(10):
    t_14 = ev_2014.iloc[i] if i < len(ev_2014) else None
    t_16 = ev_2016.iloc[i] if i < len(ev_2016) else None
    t_01 = ev_2017_01.iloc[i] if i < len(ev_2017_01) else None
    t_06 = ev_2017_06.iloc[i] if i < len(ev_2017_06) else None
    print(f"Ev #{i}:")
    print(f"  2014-06: {t_14['time_candle']} | {t_14['event_type']} | P_broken={t_14['price_broken']} | Close={t_14['close_price']}")
    print(f"  2016-01: {t_16['time_candle']} | {t_16['event_type']} | P_broken={t_16['price_broken']} | Close={t_16['close_price']}")
    print(f"  2017-01: {t_01['time_candle']} | {t_01['event_type']} | P_broken={t_01['price_broken']} | Close={t_01['close_price']}")
    print(f"  2017-06: {t_06['time_candle']} | {t_06['event_type']} | P_broken={t_06['price_broken']} | Close={t_06['close_price']}")

# Find convergence: check from what date all remaining events match bit for bit
for dt_str in ["2018-01-15", "2018-02-01", "2018-03-01", "2018-04-01", "2018-06-01", "2018-07-01"]:
    sub_14 = ev_2014[ev_2014["time_candle"] >= dt_str].reset_index(drop=True)
    sub_16 = ev_2016[ev_2016["time_candle"] >= dt_str].reset_index(drop=True)
    sub_01 = ev_2017_01[ev_2017_01["time_candle"] >= dt_str].reset_index(drop=True)
    sub_06 = ev_2017_06[ev_2017_06["time_candle"] >= dt_str].reset_index(drop=True)
    
    match_14_16 = (len(sub_14) == len(sub_16)) and (sub_14["time_candle"].values == sub_16["time_candle"].values).all() and (sub_14["event_type"].values == sub_16["event_type"].values).all()
    match_16_01 = (len(sub_16) == len(sub_01)) and (sub_16["time_candle"].values == sub_01["time_candle"].values).all() and (sub_16["event_type"].values == sub_01["event_type"].values).all()
    match_01_06 = (len(sub_01) == len(sub_06)) and (sub_01["time_candle"].values == sub_06["time_candle"].values).all() and (sub_01["event_type"].values == sub_06["event_type"].values).all()
    
    print(f"\nConvergence check from {dt_str}:")
    print(f"  2014 vs 2016 match: {match_14_16} ({len(sub_14)} vs {len(sub_16)})")
    print(f"  2016 vs 2017-01 match: {match_16_01} ({len(sub_16)} vs {len(sub_01)})")
    print(f"  2017-01 vs 2017-06 match: {match_01_06} ({len(sub_01)} vs {len(sub_06)})")
