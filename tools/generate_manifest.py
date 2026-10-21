import hashlib
import sys
from pathlib import Path
import duckdb
import pandas as pd

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

con = duckdb.connect()

def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

print("=== MANIFEST DATA V2 (CLEAN M1 & DERIVED H1) ===")

print("\n1. CLEAN M1 (data/clean/GOLD_M1_utc):")
clean_base = Path("data/clean/GOLD_M1_utc")
clean_rows = con.execute("""
    SELECT year, count(*) as count_m1
    FROM 'data/clean/GOLD_M1_utc/**/*.parquet'
    GROUP BY year
    ORDER BY year
""").df()

clean_records = []
for idx, row in clean_rows.iterrows():
    yr = int(row["year"])
    cnt = int(row["count_m1"])
    files = list((clean_base / f"year={yr}").glob("*.parquet"))
    for f in files:
        sha = file_sha256(f)
        size = f.stat().st_size
        clean_records.append({
            "year": yr,
            "rows": cnt,
            "file": f.name,
            "size_bytes": size,
            "sha256": sha
        })

df_clean_manifest = pd.DataFrame(clean_records)
print(df_clean_manifest.to_string(index=False))

print("\n2. DERIVED H1 (data/derived/GOLD_H1_utc):")
derived_base = Path("data/derived/GOLD_H1_utc")
derived_rows = con.execute("""
    SELECT year, count(*) as count_h1
    FROM 'data/derived/GOLD_H1_utc/**/*.parquet'
    GROUP BY year
    ORDER BY year
""").df()

derived_records = []
for idx, row in derived_rows.iterrows():
    yr = int(row["year"])
    cnt = int(row["count_h1"])
    files = list((derived_base / f"year={yr}").glob("*.parquet"))
    for f in files:
        sha = file_sha256(f)
        size = f.stat().st_size
        derived_records.append({
            "year": yr,
            "rows": cnt,
            "file": f.name,
            "size_bytes": size,
            "sha256": sha
        })

df_derived_manifest = pd.DataFrame(derived_records)
print(df_derived_manifest.to_string(index=False))
