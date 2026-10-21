"""Mô-đun signals.py - Nhận diện Sweep thanh khoản, Xác nhận Cấu trúc & FVG chuẩn SMC v1.2.

Đặc tả kỹ thuật đóng băng theo PROTOCOL.md (Mục 4 & 4.1, prereg-smc-v1.2):
1. Chuỗi xác nhận đơn trị (Deterministic Confirmation Chain):
   - Sweep tại nến H1 s:
     * High pool (SWING_HIGH, EQH, PDH, ASIAN_HIGH) bị SWEPT -> tiềm năng SELL.
     * Low pool (SWING_LOW, EQL, PDL, ASIAN_LOW) bị SWEPT -> tiềm năng BUY.
     * Xung đột tại s: nếu cả 2 phía cùng bị SWEPT tại s -> DROP_CONFLICT_SWEEP (hủy cả 2, không mở sweep).
     * Nhiều pool cùng phía: gộp thành 1 sweep duy nhất với price cực trị (max High / min Low).
   - Sự kiện cấu trúc tại nến H1 c (s < c <= s + 24 hàng H1):
     * SELL: BEARISH_CHOCH hoặc BEARISH_BOS (loại trừ *_INIT_BREAK).
     * BUY: BULLISH_CHOCH hoặc BULLISH_BOS (loại trừ *_INIT_BREAK).
     * Cố định c = t_struct.
   - Khối FVG hợp lưu tại nến c:
     * direction cùng chiều với tín hiệu.
     * spans_gap == False (loại trừ FVG bắc qua khoảng trống).
     * ACTIVE tại c (CREATED trước/tại c, chưa INVALIDATED, chưa EXPIRED; TESTED vẫn ACTIVE).
     * C9.2 (Amendment): Nến xung lực (nến giữa) i-1 >= s <=> nến thứ ba i >= s+1.
       (bar_index_fvg >= bar_index_s + 1 <=> time_candle_fvg >= time_utc[s+1]).
   - Cửa sổ hiệu lực: Đếm theo đúng 24 hàng dữ liệu H1 liên tiếp thực tế.
     Sweep chưa thỏa chuỗi vẫn GIỮ MỞ đến hết 24 hàng (C6.2).
     Nếu nhiều sweep thỏa chuỗi tại c, neo sweep gần nhất s = max(S_valid(c)) (C6.1).
     Khi chuỗi hoàn tất tại c, sweep s bị TIÊU THỤ VÔ ĐIỀU KIỆN (C6.3).

2. Quy trình Thác nước Hủy Tín hiệu (Waterfall Drop Priority — C11.3):
   Đánh giá tín hiệu tại c theo đúng thứ tự nghiêm ngặt:
   1. DROP_CONFLICT_SIGNAL: c thỏa đồng thời cả BUY và SELL.
   2. DROP_SPLIT_END: close_time_c >= split_end.
   3. DROP_MARKET_CLOSED: không có hàng H1 nào mở tại close_time_c trong dữ liệu.
   4. DROP_DAILY_BREAK: close_time_c rơi vào khung bảo trì danh định 17:00–18:00 NY.
   5. DROP_POSITION_BUSY: đang có vị thế mở khác chưa đóng tại close_time_c.
   6. DROP_INVALID_RDIST: R_dist vi phạm sàn (0.5*ATR hoặc 12.5*friction_year) hoặc trần (0.10*Entry).
   Mỗi tín hiệu bị hủy chỉ tăng đúng 1 bộ đếm duy nhất tại bước vi phạm đầu tiên.
   Vượt qua cả 6 bước -> ENTERED.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..data import (
    DEFAULT_SPLITS_PATH,
    compute_wilder_atr,
    get_development_end_date,
    load_h1,
    load_splits_config,
    normalize_end_date,
)
from .fvg import FVG_COLUMNS, LIFECYCLE_COLUMNS, get_active_fvg_at, load_fvg
from .liquidity import load_liquidity
from .structure import load_structure

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_SPREADS_PATH = _REPO_ROOT / "config" / "spreads.json"

HIGH_POOL_TYPES = {"SWING_HIGH", "EQH", "PDH", "ASIAN_HIGH"}
LOW_POOL_TYPES = {"SWING_LOW", "EQL", "PDL", "ASIAN_LOW"}

VALID_SELL_STRUCTURE = {"BEARISH_CHOCH", "BEARISH_BOS"}
VALID_BUY_STRUCTURE = {"BULLISH_CHOCH", "BULLISH_BOS"}


def load_spreads_config(spreads_path: Path | str | None = None) -> dict[str, Any]:
    """Đọc file cấu hình config/spreads.json."""
    path = Path(spreads_path) if spreads_path is not None else DEFAULT_SPREADS_PATH
    if not path.exists():
        # Fallback từ repo root
        alt = Path("config/spreads.json")
        if alt.exists():
            path = alt
        else:
            raise FileNotFoundError(f"Không tìm thấy file cấu hình spreads tại '{path}'")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_friction_year(
    year: int,
    spreads_cfg: dict[str, Any] | None = None,
) -> tuple[float, float, float]:
    """Tính (spread_price, slip_price, friction_year) cho một năm cụ thể.

    Đơn vị: USD (giá).
    """
    cfg = spreads_cfg or load_spreads_config()
    spreads_map = cfg["yearly_median_spreads_points"]
    year_str = str(year)
    if year_str not in spreads_map:
        raise KeyError(f"Năm {year} không có trong bảng spreads config: {list(spreads_map.keys())}")

    med_pts = float(spreads_map[year_str])
    mult = float(cfg.get("spread_multiplier", 1.25))
    point_val = float(cfg.get("point_value_usd", 0.01))
    slip_pts = float(cfg.get("slip_points", 5.0))

    spread_price = med_pts * mult * point_val
    slip_price = slip_pts * point_val
    friction_year = spread_price + 2.0 * slip_price
    return spread_price, slip_price, friction_year


def is_in_nominal_ny_break(t_utc: pd.Timestamp) -> bool:
    """Kiểm tra thời điểm có rơi vào khung giờ bảo trì danh định 17:00–18:00 New York hay không.

    Tự động xử lý giờ mùa hè (EDT: UTC-4 -> 21:00–22:00 UTC) và mùa đông (EST: UTC-5 -> 22:00–23:00 UTC).
    """
    ts = pd.to_datetime(t_utc)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")

    ts_ny = ts.tz_convert(ZoneInfo("America/New_York"))
    # Giờ bảo trì 17:00:00 <= t < 18:00:00 New York
    return bool(ts_ny.hour == 17)


@dataclass
class SweepItem:
    """Đối tượng lưu trữ trạng thái của một Liquidity Sweep."""
    direction: str  # 'SELL' (từ High pool) hoặc 'BUY' (từ Low pool)
    row_idx_s: int  # Chỉ số hàng H1 tại nến sweep s
    time_s: pd.Timestamp  # Thời gian mở nến s
    close_time_s: pd.Timestamp  # Thời gian đóng nến s
    reference_price: float  # Mức giá pool cực trị (max cho High, min cho Low)
    pool_ids: list[int]
    pool_types: list[str]
    window_end_row: int  # row_idx_s + 24
    status: str = "OPEN"  # 'OPEN', 'CONSUMED', 'EXPIRED_WINDOW'


def find_and_group_sweeps(
    h1_df: pd.DataFrame,
    sweeps_lifecycle_df: pd.DataFrame,
) -> tuple[list[SweepItem], int]:
    """Trích xuất và nhóm các sự kiện sweep thanh khoản theo từng nến H1 s.

    Xử lý:
    1. Lọc chỉ lấy event_type == 'SWEPT'.
    2. Nhóm theo time_candle s.
    3. Kiểm tra xung đột quét cả 2 đầu tại s (DROP_CONFLICT_SWEEP):
       Nếu nến s vừa có High pool bị SWEPT vừa có Low pool bị SWEPT -> HỦY CẢ 2.
    4. Nhiều pool cùng hướng tại s -> gộp thành 1 SweepItem duy nhất với reference_price cực trị.
    5. Đặt cửa sổ hiệu lực window_end_row = row_idx_s + 24 hàng H1.

    Trả về:
        (danh sách SweepItem hợp lệ, số lượng DROP_CONFLICT_SWEEP)
    """
    if sweeps_lifecycle_df.empty or h1_df.empty:
        return [], 0

    swept_events = sweeps_lifecycle_df[sweeps_lifecycle_df["event_type"] == "SWEPT"].copy()
    if swept_events.empty:
        return [], 0

    # Lập bản đồ time_utc -> row_idx trong h1_df
    time_to_row = {pd.to_datetime(t): idx for idx, t in enumerate(h1_df["time_utc"])}
    duration = h1_df["time_utc"].iloc[1] - h1_df["time_utc"].iloc[0] if len(h1_df) > 1 else pd.Timedelta(hours=1)

    swept_events["time_candle_ts"] = pd.to_datetime(swept_events["time_candle"])
    grouped = swept_events.groupby("time_candle_ts")

    valid_sweeps: list[SweepItem] = []
    conflict_sweep_count = 0

    for t_s, group in grouped:
        if t_s not in time_to_row:
            continue
        row_s = time_to_row[t_s]

        high_pools = group[group["pool_type"].isin(HIGH_POOL_TYPES)]
        low_pools = group[group["pool_type"].isin(LOW_POOL_TYPES)]

        has_high_sweep = not high_pools.empty
        has_low_sweep = not low_pools.empty

        # Kiểm tra xung đột hai đầu tại s
        if has_high_sweep and has_low_sweep:
            conflict_sweep_count += 1
            continue

        if has_high_sweep:
            ref_p = float(high_pools["price_trigger"].max())
            valid_sweeps.append(
                SweepItem(
                    direction="SELL",
                    row_idx_s=row_s,
                    time_s=t_s,
                    close_time_s=t_s + duration,
                    reference_price=ref_p,
                    pool_ids=high_pools["pool_id"].astype(int).tolist(),
                    pool_types=high_pools["pool_type"].astype(str).tolist(),
                    window_end_row=row_s + 24,
                )
            )

        if has_low_sweep:
            ref_p = float(low_pools["price_trigger"].min())
            valid_sweeps.append(
                SweepItem(
                    direction="BUY",
                    row_idx_s=row_s,
                    time_s=t_s,
                    close_time_s=t_s + duration,
                    reference_price=ref_p,
                    pool_ids=low_pools["pool_id"].astype(int).tolist(),
                    pool_types=low_pools["pool_type"].astype(str).tolist(),
                    window_end_row=row_s + 24,
                )
            )

    valid_sweeps.sort(key=lambda sw: sw.row_idx_s)
    return valid_sweeps, conflict_sweep_count


def scan_position_exit_m1(
    direction: str,
    entry_time: pd.Timestamp,
    entry_price: float,
    sl: float,
    tp: float,
    spread: float,
    slip: float,
    m1_df: pd.DataFrame,
    max_exit_time: pd.Timestamp,
    split_end: pd.Timestamp,
) -> tuple[pd.Timestamp, str, float]:
    """Quét nến M1 nội phiên để tìm điểm và lý do thoát lệnh chính xác.

    Thứ tự ưu tiên trong cùng 1 nến M1:
    SL / SL_GAP > TP > TIME_EXIT / DATA_END

    Trả về:
        (exit_time, exit_reason, exit_price)
    """
    dir_clean = direction.upper()
    times_series = m1_df["time_utc"]
    if not pd.api.types.is_datetime64_any_dtype(times_series):
        times_series = pd.to_datetime(times_series)
    times_arr = times_series.to_numpy()

    t_entry_dt = np.datetime64(pd.to_datetime(entry_time))
    start_idx = int(np.searchsorted(times_arr, t_entry_dt))
    if start_idx >= len(times_arr):
        return max_exit_time, "TIME_EXIT", entry_price

    t_max_dt = np.datetime64(pd.to_datetime(max_exit_time))
    end_idx = min(int(np.searchsorted(times_arr, t_max_dt, side="right")) + 2, len(times_arr))

    sub_m1 = m1_df.iloc[start_idx:end_idx].reset_index(drop=True)
    opens = sub_m1["open"].to_numpy(dtype=float)
    highs = sub_m1["high"].to_numpy(dtype=float)
    lows = sub_m1["low"].to_numpy(dtype=float)
    closes = sub_m1["close"].to_numpy(dtype=float)
    sub_times = pd.to_datetime(sub_m1["time_utc"])

    for idx, t in enumerate(sub_times):
        op = opens[idx]
        hi = highs[idx]
        lo = lows[idx]
        cl = closes[idx]

        # Kiểm tra nếu vượt split_end -> DATA_END
        if t >= split_end:
            if dir_clean == "BUY":
                return t, "DATA_END", cl - slip
            else:
                return t, "DATA_END", cl + spread + slip

        # Kiểm tra nếu vượt max_exit_time -> TIME_EXIT
        if t >= max_exit_time:
            if dir_clean == "BUY":
                return t, "TIME_EXIT", op - slip
            else:
                return t, "TIME_EXIT", op + spread + slip

        # 1. SL_GAP
        if dir_clean == "BUY" and op <= sl:
            return t, "SL_GAP", op - slip
        if dir_clean == "SELL" and (op + spread) >= sl:
            return t, "SL_GAP", op + spread + slip

        # 2. SL thông thường
        sl_hit = (lo <= sl) if dir_clean == "BUY" else ((hi + spread) >= sl)
        # 3. TP
        tp_hit = (hi >= tp) if dir_clean == "BUY" else ((lo + spread) <= tp)

        if sl_hit and tp_hit:
            # Ưu tiên SL
            return (t, "SL", sl - slip) if dir_clean == "BUY" else (t, "SL", sl + slip)
        if sl_hit:
            return (t, "SL", sl - slip) if dir_clean == "BUY" else (t, "SL", sl + slip)
        if tp_hit:
            # Gap qua TP chỉ lấy đúng mức TP theo C3
            return (t, "TP", tp - slip) if dir_clean == "BUY" else (t, "TP", tp + slip)

    # Nếu duyệt hết m1 mà chưa chạm
    last_t = sub_times.iloc[-1]
    if dir_clean == "BUY":
        return last_t, "TIME_EXIT", closes[-1] - slip
    else:
        return last_t, "TIME_EXIT", closes[-1] + spread + slip


def generate_smc_signals(
    h1_df: pd.DataFrame,
    sweeps_lifecycle_df: pd.DataFrame,
    structure_df: pd.DataFrame,
    fvg_df: pd.DataFrame,
    fvg_lifecycle_df: pd.DataFrame,
    m1_df: pd.DataFrame | None = None,
    spreads_cfg: dict[str, Any] | None = None,
    splits_cfg: dict[str, Any] | None = None,
    split_name: str = "development",
    enable_position_filter: bool = True,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Tạo tín hiệu SMC Sweep Confirmation v1 (chuẩn prereg-smc-v1.2).

    Tham số:
        h1_df: Dữ liệu nến H1 có các cột time_utc, open, high, low, close, atr_14.
        sweeps_lifecycle_df: Nhật ký vòng đời pool thanh khoản từ liquidity.py.
        structure_df: Bảng sự kiện cấu trúc thị trường từ structure.py.
        fvg_df: Bảng danh sách FVG từ fvg.py.
        fvg_lifecycle_df: Bảng nhật ký vòng đời FVG từ fvg.py.
        m1_df: Dữ liệu nến M1 (tùy chọn; dùng để quét thoát vị thế và lấy Open phút :00).
        spreads_cfg: Cấu hình spread theo năm (mặc định đọc config/spreads.json).
        splits_cfg: Cấu hình phân chia lớp (mặc định đọc config/splits.json).
        split_name: Tên phân lớp ('development', 'robustness', 'holdout').
        enable_position_filter: Bật/tắt bộ lọc DROP_POSITION_BUSY (mặc định True).

    Trả về:
        (signals_df, summary_dict)
    """
    if h1_df.empty:
        return pd.DataFrame(), {}

    # Đảm bảo H1 có ATR(14)
    if "atr_14" not in h1_df.columns:
        h1_df = h1_df.copy()
        h1_df["atr_14"] = compute_wilder_atr(
            h1_df["high"].to_numpy(),
            h1_df["low"].to_numpy(),
            h1_df["close"].to_numpy(),
            period=14,
        )

    spreads_cfg = spreads_cfg or load_spreads_config()
    splits_cfg = splits_cfg or load_splits_config()

    # Ranh giới kết thúc split
    split_dates = splits_cfg.get(split_name, ["2018-01-01", "2025-06-30"])
    split_end = pd.to_datetime(f"{split_dates[1]} 23:59:00")

    # Bản đồ thời gian H1
    h1_times = pd.to_datetime(h1_df["time_utc"])
    time_to_row = {t: idx for idx, t in enumerate(h1_times)}
    h1_open_times_set = set(h1_times)
    duration = h1_times.iloc[1] - h1_times.iloc[0] if len(h1_times) > 1 else pd.Timedelta(hours=1)

    # 1. Trích xuất và nhóm sweeps
    all_sweeps, conflict_sweep_count = find_and_group_sweeps(h1_df, sweeps_lifecycle_df)

    # Lập chỉ mục structure events theo time_candle c
    struct_events = structure_df.copy()
    struct_events["time_candle_ts"] = pd.to_datetime(struct_events["time_candle"])
    struct_by_time: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    for _, row in struct_events.iterrows():
        t_c = row["time_candle_ts"]
        struct_by_time.setdefault(t_c, []).append(row.to_dict())

    # Lập chỉ mục FVG active theo khoảng thời gian [t_created, t_term)
    fvg_df_clean = fvg_df.copy()
    if not fvg_df_clean.empty:
        fvg_df_clean["time_candle_ts"] = pd.to_datetime(fvg_df_clean["time_candle"])
        valid_fvgs = fvg_df_clean[~fvg_df_clean["spans_gap"]].copy()
    else:
        valid_fvgs = pd.DataFrame()

    fvg_intervals = []
    if not valid_fvgs.empty and not fvg_lifecycle_df.empty:
        lc_copy = fvg_lifecycle_df.copy()
        lc_copy["time_available_ts"] = pd.to_datetime(lc_copy["time_available"])

        created_events = lc_copy[lc_copy["event_type"] == "CREATED"]
        created_map = dict(zip(created_events["fvg_id"], created_events["time_available_ts"]))

        term_events = lc_copy[lc_copy["event_type"].isin(["INVALIDATED", "EXPIRED"])]
        term_map = dict(zip(term_events["fvg_id"], term_events["time_available_ts"]))

        for _, row in valid_fvgs.iterrows():
            fid = row["fvg_id"]
            t_created = created_map.get(fid)
            if t_created is None:
                continue
            t_term = term_map.get(fid, pd.Timestamp.max)
            fvg_intervals.append({
                "fvg_id": fid,
                "direction": row["direction"],
                "time_candle_ts": row["time_candle_ts"],
                "t_created": t_created,
                "t_term": t_term,
            })
    fvg_interval_df = pd.DataFrame(fvg_intervals)

    # Danh sách theo dõi
    open_sweeps: list[SweepItem] = []
    next_sweep_idx = 0
    total_sweeps_count = len(all_sweeps)

    # Bộ đếm thống kê thác nước
    counts = {
        "DROP_CONFLICT_SWEEP": conflict_sweep_count,
        "CANDIDATES_RAW": 0,
        "DROP_CONFLICT_SIGNAL": 0,
        "DROP_SPLIT_END": 0,
        "DROP_MARKET_CLOSED": 0,
        "DROP_DAILY_BREAK": 0,
        "DROP_POSITION_BUSY": 0,
        "DROP_INVALID_RDIST": 0,
        "ENTERED": 0,
        "n_sparse_m1_entry": 0,
    }

    # Theo dõi vị thế mở
    active_position: dict[str, Any] | None = None

    signals_records: list[dict[str, Any]] = []

    # M1 index nếu có
    m1_lookup = None
    if m1_df is not None and not m1_df.empty:
        m1_df_clean = m1_df.copy()
        m1_df_clean["time_utc_ts"] = pd.to_datetime(m1_df_clean["time_utc"])
        m1_lookup = m1_df_clean.set_index("time_utc_ts")

    # Duyệt tuần tự từng hàng H1 từ 0 đến N-1
    for c_idx in range(len(h1_df)):
        t_c = h1_times.iloc[c_idx]
        close_time_c = t_c + duration

        # Đóng các vị thế đã chạm exit trước hoặc tại close_time_c
        if active_position is not None:
            if close_time_c >= active_position["exit_time"]:
                active_position = None

        # Nạp các sweeps mới xảy ra tại nến c (để sẵn sàng cho các nến tương lai > c)
        while next_sweep_idx < total_sweeps_count and all_sweeps[next_sweep_idx].row_idx_s <= c_idx:
            sw = all_sweeps[next_sweep_idx]
            if sw.row_idx_s == c_idx:
                open_sweeps.append(sw)
            next_sweep_idx += 1

        # Cập nhật trạng thái hết hạn cho các sweeps vượt quá 24 hàng H1 (row_idx_s + 24 < c_idx)
        active_open_sweeps: list[SweepItem] = []
        for sw in open_sweeps:
            if sw.status != "OPEN":
                continue
            if c_idx > sw.window_end_row:
                sw.status = "EXPIRED_WINDOW"
            else:
                active_open_sweeps.append(sw)
        open_sweeps = active_open_sweeps

        # Kiểm tra xem tại nến c có sự kiện cấu trúc nào không
        curr_struct_list = struct_by_time.get(t_c, [])
        if not curr_struct_list:
            continue

        # Lấy FVG active tại close_time_c (spans_gap == False và t_created <= close_time_c < t_term)
        if not fvg_interval_df.empty:
            active_mask = (fvg_interval_df["t_created"] <= close_time_c) & (close_time_c < fvg_interval_df["t_term"])
            active_fvgs = fvg_interval_df[active_mask]
        else:
            active_fvgs = pd.DataFrame()

        # Kiểm tra chuỗi xác nhận cho cả SELL và BUY
        sell_valid_sweeps: list[SweepItem] = []
        buy_valid_sweeps: list[SweepItem] = []

        # 1. Chuỗi SELL
        has_sell_struct = any(ev["event_type"] in VALID_SELL_STRUCTURE for ev in curr_struct_list)
        if has_sell_struct:
            bearish_fvgs = active_fvgs[active_fvgs["direction"] == "BEARISH"] if not active_fvgs.empty else pd.DataFrame()
            if not bearish_fvgs.empty:
                for sw in open_sweeps:
                    if sw.direction != "SELL" or sw.status != "OPEN":
                        continue
                    # Cửa sổ: s < c <= s + 24
                    if not (sw.row_idx_s < c_idx <= sw.window_end_row):
                        continue
                    # C9.2 (Amendment): i >= s + 1 <=> time_candle >= time_utc[s+1]
                    t_s_plus_1 = h1_times.iloc[sw.row_idx_s + 1]
                    matching_fvg = bearish_fvgs[bearish_fvgs["time_candle_ts"] >= t_s_plus_1]
                    if not matching_fvg.empty:
                        sell_valid_sweeps.append(sw)

        # 2. Chuỗi BUY
        has_buy_struct = any(ev["event_type"] in VALID_BUY_STRUCTURE for ev in curr_struct_list)
        if has_buy_struct:
            bullish_fvgs = active_fvgs[active_fvgs["direction"] == "BULLISH"] if not active_fvgs.empty else pd.DataFrame()
            if not bullish_fvgs.empty:
                for sw in open_sweeps:
                    if sw.direction != "BUY" or sw.status != "OPEN":
                        continue
                    if not (sw.row_idx_s < c_idx <= sw.window_end_row):
                        continue
                    t_s_plus_1 = h1_times.iloc[sw.row_idx_s + 1]
                    matching_fvg = bullish_fvgs[bullish_fvgs["time_candle_ts"] >= t_s_plus_1]
                    if not matching_fvg.empty:
                        buy_valid_sweeps.append(sw)

        has_sell_candidate = len(sell_valid_sweeps) > 0
        has_buy_candidate = len(buy_valid_sweeps) > 0

        if not has_sell_candidate and not has_buy_candidate:
            # Chưa thỏa chuỗi xác nhận: C6.2 giữ mở toàn bộ sweeps
            continue

        counts["CANDIDATES_RAW"] += 1

        # =====================================================================
        # QUY TRÌNH THÁC NƯỚC HỦY TÍN HIỆU (WATERFALL DROP PRIORITY — C11.3)
        # =====================================================================

        # BƯỚC 1: DROP_CONFLICT_SIGNAL (c đồng thời thỏa mãn cả BUY và SELL)
        if has_sell_candidate and has_buy_candidate:
            counts["DROP_CONFLICT_SIGNAL"] += 1
            # Tiêu thụ cả 2 phía sweeps
            for sw in sell_valid_sweeps + buy_valid_sweeps:
                sw.status = "CONSUMED"
            continue

        # Chọn hướng và sweep gần nhất s = max(S_valid(c)) (C6.1)
        if has_sell_candidate:
            direction = "SELL"
            qualifying_sweeps = sell_valid_sweeps
        else:
            direction = "BUY"
            qualifying_sweeps = buy_valid_sweeps

        chosen_sweep = max(qualifying_sweeps, key=lambda sw: sw.row_idx_s)
        s_idx = chosen_sweep.row_idx_s

        # Hàm trợ giúp: Tiêu thụ sweep khi tín hiệu bị hủy
        def consume_qualifying_sweeps():
            for sw in qualifying_sweeps:
                if sw.row_idx_s <= s_idx:
                    sw.status = "CONSUMED"

        # BƯỚC 2: DROP_SPLIT_END (close_time_c >= split_end)
        if close_time_c >= split_end:
            counts["DROP_SPLIT_END"] += 1
            consume_qualifying_sweeps()
            continue

        # BƯỚC 3: DROP_MARKET_CLOSED (không có hàng H1 nào mở tại close_time_c)
        if close_time_c not in h1_open_times_set:
            counts["DROP_MARKET_CLOSED"] += 1
            consume_qualifying_sweeps()
            continue

        # BƯỚC 4: DROP_DAILY_BREAK (close_time_c rơi vào 17:00–18:00 New York)
        if is_in_nominal_ny_break(close_time_c):
            counts["DROP_DAILY_BREAK"] += 1
            consume_qualifying_sweeps()
            continue

        # BƯỚC 5: DROP_POSITION_BUSY (đang có vị thế mở khác)
        if enable_position_filter and active_position is not None:
            counts["DROP_POSITION_BUSY"] += 1
            consume_qualifying_sweeps()
            continue

        # Lấy giá mở cửa nến vào lệnh (C11.1)
        # Nếu có M1 tại đúng close_time_c -> lấy Open của M1
        # Nếu thiếu nến M1 phút :00 -> lấy Open của hàng H1 mở tại close_time_c và tăng n_sparse_m1_entry
        next_h1_row_idx = time_to_row[close_time_c]
        h1_open_price = float(h1_df["open"].iloc[next_h1_row_idx])
        entry_open = h1_open_price

        if m1_lookup is not None:
            if close_time_c in m1_lookup.index:
                m1_bar = m1_lookup.loc[close_time_c]
                entry_open = float(m1_bar["open"].iloc[0] if isinstance(m1_bar, pd.DataFrame) else m1_bar["open"])
            else:
                counts["n_sparse_m1_entry"] += 1
                entry_open = h1_open_price

        # BƯỚC 6: DROP_INVALID_RDIST
        year = close_time_c.year
        spread_price, slip_price, friction_year = get_friction_year(year, spreads_cfg)

        # Tính Stop Loss (C10.1)
        # BUY: SL = min Low[i] trong [s, c]
        # SELL: SL = max High[i] trong [s, c]
        sub_highs = h1_df["high"].iloc[s_idx : c_idx + 1].to_numpy(dtype=float)
        sub_lows = h1_df["low"].iloc[s_idx : c_idx + 1].to_numpy(dtype=float)

        if direction == "BUY":
            sl_price = float(np.min(sub_lows))
            entry_exec = entry_open + spread_price + slip_price
            r_dist = entry_exec - sl_price
            tp_price = entry_exec + 2.0 * r_dist
        else:
            sl_price = float(np.max(sub_highs))
            entry_exec = entry_open - slip_price
            r_dist = sl_price - entry_exec
            tp_price = entry_exec - 2.0 * r_dist

        atr_c = float(h1_df["atr_14"].iloc[c_idx])
        cost_floor = max(0.50 * atr_c, 12.5 * friction_year)
        safety_ceiling = 0.10 * entry_exec

        if (r_dist < cost_floor) or (r_dist > safety_ceiling) or (r_dist <= 0):
            counts["DROP_INVALID_RDIST"] += 1
            consume_qualifying_sweeps()
            continue

        # =====================================================================
        # VƯỢT QUA TOÀN BỘ 6 BƯỚC -> ENTERED
        # =====================================================================
        counts["ENTERED"] += 1
        consume_qualifying_sweeps()

        # Tính thời gian nắm giữ tối đa: 24 hàng H1 tiếp theo [next_h1_row_idx, next_h1_row_idx + 23]
        max_h1_idx = min(next_h1_row_idx + 23, len(h1_df) - 1)
        max_hold_time = h1_times.iloc[max_h1_idx] + duration

        # Quét thoát lệnh trên M1 nếu có
        exit_time = max_hold_time
        exit_reason = "TIME_EXIT"
        exit_price = entry_open - slip_price if direction == "BUY" else entry_open + spread_price + slip_price

        if m1_df is not None and not m1_df.empty:
            exit_time, exit_reason, exit_price = scan_position_exit_m1(
                direction=direction,
                entry_time=close_time_c,
                entry_price=entry_exec,
                sl=sl_price,
                tp=tp_price,
                spread=spread_price,
                slip=slip_price,
                m1_df=m1_df,
                max_exit_time=max_hold_time,
                split_end=split_end,
            )

        if enable_position_filter:
            active_position = {
                "direction": direction,
                "entry_time": close_time_c,
                "exit_time": exit_time,
                "exit_reason": exit_reason,
            }

        signals_records.append({
            "signal_id": len(signals_records),
            "direction": direction,
            "candle_c_time": t_c,
            "close_time_c": close_time_c,
            "sweep_s_time": chosen_sweep.time_s,
            "sweep_row_idx": s_idx,
            "signal_row_idx": c_idx,
            "entry_open": entry_open,
            "entry_exec": entry_exec,
            "sl": sl_price,
            "tp": tp_price,
            "r_dist": r_dist,
            "atr_c": atr_c,
            "friction_year": friction_year,
            "exit_time": exit_time,
            "exit_reason": exit_reason,
            "exit_price": exit_price,
        })

    signals_df = pd.DataFrame(signals_records)
    return signals_df, counts
