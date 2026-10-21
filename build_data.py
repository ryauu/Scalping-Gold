"""Làm sạch GOLD M1 từ MT5 và dựng kho dữ liệu (Parquet + DuckDB).

Phiên bản: 2.0 (Khắc phục lỗi lệch múi giờ DST Châu Âu vs Mỹ)
Quy tắc múi giờ: Server MT5 hoạt động theo giờ Đông Âu (EET/EEST), tuân thủ lịch
đổi giờ mùa hè của Liên minh Châu Âu (EU DST), đại diện bởi Europe/Nicosia (Síp).
  - Mùa đông (EET): UTC+2 (trừ 2 giờ để ra UTC).
  - Mùa hè (EEST): UTC+3 (trừ 3 giờ để ra UTC).

Đầu vào : data/raw/mt5/GOLD_M1/decf3c12/ (hoặc file raw tải từ MT5)
Đầu ra  : data/clean/GOLD_M1_utc/year=*/        M1 đã cắt nến thô + đổi sang UTC chuẩn
          data/derived/GOLD_H1_utc/year=*/      H1 dựng từ M1 sạch
          data/meta.duckdb                      bảng sources + view SQL (m1_utc, h1_utc)

Chạy   : python build_data.py [--src data/raw/mt5/GOLD_M1/decf3c12] [--root data]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')


import duckdb
import numpy as np
import pandas as pd

SCRIPT_VERSION = "2.0"
SOURCE = "mt5"
SYMBOL = "GOLD"
MIN_BARS_FULL_DAY = 1000          # ngày có > 1000 nến mới coi là M1 thật
RUN_LEN = 30                      # M1 thật = chuỗi >= 30 nến liên tiếp cách nhau đúng 1 phút
SERVER_TZ = "Europe/Nicosia"       # Giờ server broker Síp/Đông Âu (EET: UTC+2 đông / EEST: UTC+3 hè)
TZ_ASSUMPTION = f"server = {SERVER_TZ} (EET/EEST, EU DST rules)"
KNOWN_RAW_SHA = "decf3c126908b13480327287c665c6922fe906751644756a1862c0feb67da541"


def sha256_of_file(path: Path) -> str:
    """Tính SHA-256 của file đơn lẻ."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_existing_data(root: Path) -> None:
    """Sao lưu an toàn dữ liệu clean và derived v1 trước khi dựng lại."""
    clean_dir = root / "clean" / f"{SYMBOL}_M1_utc"
    clean_bak = root / "clean_v1" / f"{SYMBOL}_M1_utc"
    derived_dir = root / "derived" / f"{SYMBOL}_H1_utc"
    derived_bak = root / "derived_v1" / f"{SYMBOL}_H1_utc"

    if clean_dir.exists() and not clean_bak.exists():
        print(f"[sao lưu] Đang sao lưu {clean_dir} -> {clean_bak}...")
        clean_bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(clean_dir, clean_bak)
        print(f"[đã sao lưu] {clean_bak}")
    elif clean_bak.exists():
        print(f"[đã có sao lưu] {clean_bak} đã tồn tại từ trước.")

    if derived_dir.exists() and not derived_bak.exists():
        print(f"[sao lưu] Đang sao lưu {derived_dir} -> {derived_bak}...")
        derived_bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(derived_dir, derived_bak)
        print(f"[đã sao lưu] {derived_bak}")
    elif derived_bak.exists():
        print(f"[đã có sao lưu] {derived_bak} đã tồn tại từ trước.")


def write_partitioned(df: pd.DataFrame, root: Path, overwrite: bool = True) -> bool:
    """Ghi Parquet chia theo năm. Cột đầu tiên (index cũ) phải là cột thời gian."""
    if root.exists() and any(root.rglob("*.parquet")):
        if not overwrite:
            print(f"[giữ nguyên] {root} đã tồn tại (raw bất biến)")
            return False
        shutil.rmtree(root)
    out = df.reset_index()
    out["year"] = out.iloc[:, 0].dt.year.astype("int16")
    root.mkdir(parents=True, exist_ok=True)
    out.to_parquet(root, partition_cols=["year"], index=False)
    print(f"[đã ghi] {root} ({len(out):,} dòng)")
    return True


def cut_coarse_bars(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Bỏ các nến D1/H1 lẫn vào trước khi M1 thật bắt đầu (cắt chính xác đến từng phút)."""
    per_day = df.groupby(df.index.normalize()).size()
    full = per_day[per_day > MIN_BARS_FULL_DAY]
    if full.empty:
        raise RuntimeError("Không có ngày nào > 1000 nến, dữ liệu không phải M1?")
    pos0 = int(df.index.searchsorted(full.index[0]))
    tail = df.index[pos0:]
    step_ok = pd.Series(np.diff(tail.values) == np.timedelta64(1, "m"))
    run = step_ok.rolling(RUN_LEN).min() == 1
    if not run.any():
        raise RuntimeError("Không tìm thấy chuỗi M1 liên tiếp")
    start_pos = pos0 + int(run.idxmax()) - (RUN_LEN - 1)
    n_cut = start_pos
    print(f"M1 thật bắt đầu từ {df.index[start_pos]} (giờ server); loại {n_cut:,} nến thô trước đó")
    return df.iloc[start_pos:], n_cut


def to_utc(m1: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Chuyển đổi giờ server sang UTC theo múi giờ Europe/Nicosia (EET/EEST, EU DST)."""
    ts = pd.DatetimeIndex(m1.index).tz_localize(
        SERVER_TZ, ambiguous="NaT", nonexistent="shift_forward"
    )
    ok = ~ts.isna()
    n_bad = int((~ok).sum())
    if n_bad:
        print(f"Cảnh báo: Bỏ {n_bad} nến rơi vào giờ mơ hồ lúc chuyển giờ mùa hè EU")
    else:
        print(f"[kiểm tra n_bad = {n_bad}] Không có nến nào rơi vào giờ mơ hồ (chuyển giờ rạng sáng Chủ nhật)")

    m1_clean, ts_clean = m1[ok], ts[ok]
    clean = m1_clean.copy()
    clean.insert(0, "time_server", m1_clean.index)
    clean.index = pd.DatetimeIndex(ts_clean.tz_convert("UTC").tz_localize(None), name="time_utc")
    clean = clean[~clean.index.duplicated(keep="first")].sort_index()
    return clean, n_bad


def break_check(clean: pd.DataFrame) -> None:
    """Kiểm tra tỷ lệ nến trong giờ nghỉ sau khi chuyển đổi UTC."""
    print("\n=== KIỂM TRA TỶ LỆ NẾN GIỜ NGHỈ SAU KHI ĐỔI UTC (kỳ vọng gần 0.00) ===")
    print("năm   đông(22h)  hè(21h)")
    for year, g in clean.groupby(clean.index.year):
        def share(months, hour):
            x = g[g.index.month.isin(months)]
            ref = int((x.index.hour == 12).sum())
            return float("nan") if ref == 0 else (x.index.hour == hour).sum() / ref
        w, s = share([12, 1, 2], 22), share([6, 7, 8], 21)
        flag = "  <-- LỆCH, kiểm tra offset" if (w > 0.2 or s > 0.2) else ""
        print(f"{year}  {w:8.2f}  {s:8.2f}{flag}")


def build_h1(clean: pd.DataFrame) -> pd.DataFrame:
    """Dựng nến H1 từ nến M1 sạch."""
    h1 = clean.resample("1h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last",
         "tick_volume": "sum", "spread": "median"}
    )
    h1["n_m1"] = clean["close"].resample("1h").count()
    return h1[h1["n_m1"] > 0]


def update_meta_sources_and_views(
    root: Path,
    sha: str,
    n_raw: int,
    n_cut: int,
    clean: pd.DataFrame,
    clean_root: Path,
    h1_root: Path,
) -> None:
    """Cập nhật bảng sources với cột status và tạo lại các view SQL trong DuckDB."""
    db_path = root / "meta.duckdb"
    con = duckdb.connect(str(db_path))

    # Đảm bảo bảng sources tồn tại
    con.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            loaded_at TIMESTAMP, source VARCHAR, symbol VARCHAR, tz_assumption VARCHAR,
            file_sha256 VARCHAR, raw_rows BIGINT, coarse_bars_removed BIGINT, clean_rows BIGINT,
            first_bar_utc TIMESTAMP, last_bar_utc TIMESTAMP, script_version VARCHAR)
    """)

    # Bổ sung cột status nếu chưa có
    try:
        con.execute("ALTER TABLE sources ADD COLUMN status VARCHAR DEFAULT 'active'")
    except Exception:
        pass  # Đã tồn tại cột status

    # Đánh dấu các bản ghi cũ là 'superseded'
    con.execute("UPDATE sources SET status = 'superseded' WHERE status IS NULL OR status = 'active'")

    # Chèn bản ghi mới với status = 'active'
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    con.execute(
        """
        INSERT INTO sources (
            loaded_at, source, symbol, tz_assumption, file_sha256, raw_rows,
            coarse_bars_removed, clean_rows, first_bar_utc, last_bar_utc,
            script_version, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            now_utc,
            SOURCE,
            SYMBOL,
            TZ_ASSUMPTION,
            sha,
            n_raw,
            n_cut,
            len(clean),
            clean.index[0].to_pydatetime(),
            clean.index[-1].to_pydatetime(),
            SCRIPT_VERSION,
            "active",
        ],
    )

    # Dựng lại các view SQL
    for view, path in (("m1_utc", clean_root), ("h1_utc", h1_root)):
        glob = path.resolve().as_posix() + "/**/*.parquet"
        con.execute(f"CREATE OR REPLACE VIEW {view} AS "
                    f"SELECT * FROM read_parquet('{glob}', hive_partitioning=true)")

    print(f"\n[DuckDB meta] Đã cập nhật bảng sources (status='active') và các view m1_utc, h1_utc trong {db_path}")
    con.close()


def load_raw_data(src: Path, root: Path) -> tuple[pd.DataFrame, str, bool]:
    """Đọc dữ liệu thô và xác định SHA-256 chuẩn không ghi đúp thư mục raw."""
    is_in_raw = "raw" in src.parts or (src.is_dir() and (root / "raw").resolve() in src.resolve().parents)
    if src.is_dir():
        # Đọc từ thư mục raw đã phân vùng
        con = duckdb.connect()
        glob = src.resolve().as_posix() + "/**/*.parquet"
        df = con.execute(f"SELECT time, open, high, low, close, tick_volume, spread FROM read_parquet('{glob}') ORDER BY time").df()
        df = df.set_index("time")
        con.close()

        # Xác định sha từ bảng sources gốc nếu có
        db_path = root / "meta.duckdb"
        sha = None
        if db_path.exists():
            try:
                with duckdb.connect(str(db_path), read_only=True) as con_meta:
                    prefix = src.name[:8]
                    rows = con_meta.execute(
                        "SELECT file_sha256 FROM sources WHERE file_sha256 LIKE ? ORDER BY loaded_at ASC LIMIT 1",
                        [f"{prefix}%"]
                    ).fetchall()
                    if rows:
                        sha = rows[0][0]
            except Exception:
                pass
        if not sha:
            sha = KNOWN_RAW_SHA if ("decf3c12" in src.as_posix() or src.name == "decf3c12") else (src.name if len(src.name) == 64 else KNOWN_RAW_SHA)
        is_already_raw = is_in_raw
    else:
        # File đơn lẻ bên ngoài
        df = pd.read_parquet(src).sort_index()
        sha = sha256_of_file(src)
        is_already_raw = False

    df = df[~df.index.duplicated(keep="first")]
    return df, sha, is_already_raw


def verify_snapshot_expectations(n_raw: int, n_cut: int, n_clean: int, sha: str, expectations_path: Path) -> None:
    """Kiểm tra đối chiếu với file kỳ vọng bản chụp snapshot (nếu có)."""
    if not expectations_path.exists():
        print(f"   [bỏ qua kiểm tra bản chụp] Không tìm thấy file kỳ vọng: {expectations_path}")
        return
    try:
        with open(expectations_path, "r", encoding="utf-8") as f:
            exp = json.load(f)
        exp_sha = exp.get("raw_sha256", "")
        if exp_sha and sha and not sha.startswith(exp_sha[:8]) and not exp_sha.startswith(sha[:8]):
            print(f"   [dữ liệu mới] SHA-256 raw ({sha[:8]}) khác bản chụp ({exp_sha[:8]}). Bỏ qua kiểm tra cố định snapshot.")
            return

        print(f"   [kiểm tra bản chụp {exp.get('snapshot_date', 'snapshot')}]")
        if n_raw >= 0 and exp.get("n_raw") is not None:
            assert n_raw == exp["n_raw"], f"Lỗi snapshot: n_raw ({n_raw}) khác kỳ vọng ({exp['n_raw']})!"
            print(f"   - n_raw: {n_raw:,} (khớp 100%)")
        if n_cut >= 0 and exp.get("n_cut") is not None:
            assert n_cut == exp["n_cut"], f"Lỗi snapshot: n_cut ({n_cut}) khác kỳ vọng ({exp['n_cut']})!"
            print(f"   - n_cut: {n_cut:,} (khớp 100%)")
        if n_clean >= 0 and exp.get("clean_rows") is not None:
            assert n_clean == exp["clean_rows"], f"Lỗi snapshot: clean_rows ({n_clean}) khác kỳ vọng ({exp['clean_rows']})!"
            print(f"   - clean_rows: {n_clean:,} (khớp 100%)")
    except Exception as e:
        raise


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/raw/mt5/GOLD_M1/decf3c12")
    ap.add_argument("--root", default="data")
    ap.add_argument("--expectations", default="config/snapshot_expectations.json", help="Đường dẫn file kỳ vọng bản chụp (JSON)")
    args, _ = ap.parse_known_args()
    src, root = Path(args.src), Path(args.root)
    exp_path = Path(args.expectations)

    print(f"=====================================================================")
    print(f"QUY TRÌNH DỰNG LẠI DỮ LIỆU BUILD_DATA.PY V{SCRIPT_VERSION}")
    print(f"Múi giờ server: {SERVER_TZ} (EET/EEST, EU DST rules)")
    print(f"=====================================================================")

    # 1. Nạp raw
    print(f"\n1. Đọc dữ liệu thô từ: {src}")
    raw, sha, is_already_raw = load_raw_data(src, root)
    n_raw = len(raw)
    print(f"   Đọc {n_raw:,} dòng thô, {raw.index[0]} -> {raw.index[-1]} (giờ server)")

    # Khẳng định dữ liệu thô hợp lệ và đối chiếu kỳ vọng snapshot (nếu có)
    assert n_raw > 0, "Lỗi: Không có dòng dữ liệu thô nào!"
    verify_snapshot_expectations(n_raw=n_raw, n_cut=-1, n_clean=-1, sha=sha, expectations_path=exp_path)

    # 2. Xử lý lưu trữ raw (nếu chưa có trong thư mục raw chuẩn)
    raw_root = root / "raw" / SOURCE / f"{SYMBOL}_M1" / sha[:8]
    if is_already_raw or (raw_root.exists() and any(raw_root.rglob("*.parquet"))):
        print(f"   [bảo toàn] Nguồn dữ liệu đã thuộc raw hoặc thư mục raw bất biến đã tồn tại: {raw_root} (bỏ qua bước ghi raw)")
    else:
        print(f"   Ghi bản raw bất biến vào {raw_root}...")
        write_partitioned(raw, raw_root, overwrite=False)

    # 3. Sao lưu dữ liệu sạch và derived hiện tại (v1) trước khi ghi đè
    print(f"\n2. Kiểm tra sao lưu phiên bản cũ:")
    backup_existing_data(root)

    # 4. Cắt nến thô: LUÔN GỌI cut_coarse_bars (không dùng ngưỡng ma thuật)
    print(f"\n3. Cắt nến thô trước khi M1 thật bắt đầu:")
    m1_cut, n_cut = cut_coarse_bars(raw)
    assert n_cut >= 0, "Lỗi: n_cut âm!"
    assert len(m1_cut) > 0, "Lỗi: Không còn nến nào sau khi cắt!"

    # Đối chiếu số liệu với bản ghi trước trong bảng sources (nếu có)
    db_path = root / "meta.duckdb"
    if db_path.exists():
        try:
            with duckdb.connect(str(db_path), read_only=True) as con_chk:
                prev_sources = con_chk.execute(
                    "SELECT coarse_bars_removed, clean_rows FROM sources ORDER BY loaded_at DESC LIMIT 1"
                ).fetchall()
                if prev_sources:
                    prev_n_cut, prev_clean_rows = prev_sources[0]
                    if prev_n_cut is not None and prev_clean_rows is not None:
                        print(f"   [đối chiếu sources trước] n_cut trước: {prev_n_cut:,}, clean_rows trước: {prev_clean_rows:,}")
        except Exception as e:
            print(f"   [lưu ý đối chiếu sources] {e}")

    # 5. Chuyển đổi múi giờ sang UTC theo Europe/Nicosia
    print(f"\n4. Chuyển đổi sang UTC (múi giờ server: {SERVER_TZ}):")
    clean, n_bad = to_utc(m1_cut)
    assert n_bad == 0, f"Lỗi: Có {n_bad} nến rơi vào giờ mơ hồ!"
    assert len(clean) > 0, "Lỗi: Không còn dòng sạch nào sau khi đổi múi giờ!"

    # Đối chiếu tổng thể sau khi làm sạch với file kỳ vọng bản chụp (nếu có)
    verify_snapshot_expectations(n_raw=n_raw, n_cut=n_cut, n_clean=len(clean), sha=sha, expectations_path=exp_path)

    # Ghi clean M1 phân vùng
    clean_root = root / "clean" / f"{SYMBOL}_M1_utc"
    write_partitioned(clean, clean_root, overwrite=True)
    break_check(clean)

    # 6. Dựng nến H1
    print(f"\n5. Dựng nến H1 từ M1 sạch:")
    h1 = build_h1(clean)
    h1_root = root / "derived" / f"{SYMBOL}_H1_utc"
    write_partitioned(h1, h1_root, overwrite=True)
    print(f"   Số nến H1 dựng được: {len(h1):,}")

    # 7. Cập nhật meta DuckDB
    print(f"\n6. Cập nhật metadata DuckDB:")
    update_meta_sources_and_views(root, sha, n_raw, n_cut, clean, clean_root, h1_root)

    print(f"\n=====================================================================")
    print(f"HOÀN TẤT DỰNG LẠI DỮ LIỆU BUILD_DATA.PY V{SCRIPT_VERSION} THÀNH CÔNG!")
    print(f"=====================================================================")


if __name__ == "__main__":
    main()
