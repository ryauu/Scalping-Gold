"""Mô-đun fvg.py - Phát hiện Fair Value Gap (FVG) và Nhật ký vòng đời bất biến SMC.

Theo đặc tả SMC_SPEC.md v2.2 (mục 4):
1. Định nghĩa hình học 3 nến liên tiếp [i-2, i-1, i]:
   - Bullish FVG: Low[i] > High[i-2] (nghiêm ngặt, dấu bằng loại bỏ).
     + near_edge = top = Low[i]
     + far_edge = bottom = High[i-2]
     + gap_size = Low[i] - High[i-2]
   - Bearish FVG: High[i] < Low[i-2] (nghiêm ngặt, dấu bằng loại bỏ).
     + near_edge = bottom = High[i]
     + far_edge = top = Low[i-2]
     + gap_size = Low[i-2] - High[i]

2. Bộ lọc kích thước tối thiểu & Chống look-ahead với ATR:
   - gap_size >= gamma * ATR(H1, 14)[i-1]
   - PRIMARY: gamma = 0.15. Dấu so sánh >= (bằng đúng ngưỡng vẫn được giữ).
   - ATR[i-1] là giá trị ATR tại hàng i-1, hoàn toàn chốt ở quá khứ trước khi nến i xuất hiện.
   - Bỏ qua các nến chưa có ATR (14 nến đầu).

3. Quyết định tiên nghiệm về khoảng trống cuối tuần (Spans Gap Rule):
   - delta_t1 = time_utc[i-1] - time_utc[i-2]
   - delta_t2 = time_utc[i] - time_utc[i-1]
   - spans_gap = True nếu delta_t1 > bar_duration hoặc delta_t2 > bar_duration.
   - PRIMARY: Chỉ các FVG có spans_gap == False mới được coi là hợp lệ cho tín hiệu giao dịch.

4. Nhật ký vòng đời bất biến (Lifecycle log):
   - CREATED: Ghi nhận tại nến i (time_available = time_utc[i] + bar_duration). Nến i KHÔNG tự test FVG của chính nó.
   - Quét tiến j = i+1, i+2, ...
     + Bước 1 - Invalidation: Close[j] xuyên thủng far_edge -> ghi INVALIDATED rồi dừng.
     + Bước 2 - Touch/Test: Chạm near_edge (và chưa từng TESTED) -> ghi TESTED đúng 1 lần duy nhất. FVG vẫn active.
     + Bước 3 - Expiration (j - i >= M, PRIMARY M = 24 nến):
       * Nếu tại nến j = i+M vừa ghi TESTED: EXPIRED ghi ở nến kế tiếp i+M+1.
       * Các trường hợp khác: EXPIRED ghi tại nến i+M rồi dừng.
   - Thứ tự ưu tiên giải quyết xung đột cùng nến: INVALIDATED > TESTED > EXPIRED.

LƯU Ý VỀ ĐỊNH DANH (IDENTIFIERS):
   fvg_id, bar_index chỉ có ý nghĩa cục bộ trong từng lần nạp dữ liệu.
   Mọi liên kết với các bảng khác (Structure, Sweeps) phải nối theo mốc thời gian vật lý
   (time_candle, time_available).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data import compute_wilder_atr
from .swings import DEFAULT_H1_DURATION

# Giá trị mặc định tham số PRIMARY SMC
DEFAULT_GAMMA = 0.15
DEFAULT_M = 24
DEFAULT_ATR_PERIOD = 14

FVG_COLUMNS = [
    "fvg_id",
    "direction",
    "bar_index",
    "time_candle",
    "time_available",
    "top",
    "bottom",
    "near_edge",
    "far_edge",
    "gap_size",
    "atr_ref",
    "spans_gap",
]

LIFECYCLE_COLUMNS = [
    "lifecycle_id",
    "fvg_id",
    "event_type",
    "bar_index",
    "time_candle",
    "time_available",
    "price_trigger",
]


def detect_fvg(
    df: pd.DataFrame,
    gamma: float = DEFAULT_GAMMA,
    m: int = DEFAULT_M,
    atr_period: int = DEFAULT_ATR_PERIOD,
    bar_duration: pd.Timedelta = DEFAULT_H1_DURATION,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Phát hiện toàn bộ Fair Value Gaps (FVG) và theo dõi nhật ký vòng đời trên nến H1.

    Tham số:
        df: DataFrame chứa nến H1 với tối thiểu các cột 'time_utc', 'open', 'high', 'low', 'close'.
        gamma: Hệ số lọc kích thước FVG theo ATR (PRIMARY: 0.15).
        m: Số nến tối đa trước khi FVG hết hạn (PRIMARY: 24 nến).
        atr_period: Chu kỳ tính Wilder ATR (mặc định 14).
        bar_duration: Độ dài nến (mặc định 1 giờ đối với H1).

    Trả về:
        tuple (fvg_df, lifecycle_df):
        - fvg_df: Bảng danh sách các FVG được phát hiện.
        - lifecycle_df: Bảng nhật ký vòng đời (CREATED, TESTED, INVALIDATED, EXPIRED).
    """
    required_cols = {"time_utc", "open", "high", "low", "close"}
    # 1. Kiểm tra đủ cột bắt buộc trước tiên (kể cả khi DataFrame rỗng)
    if not required_cols.issubset(df.columns):
        raise KeyError(f"DataFrame cần có đủ các cột: {required_cols}")

    if df.empty or len(df) < 3:
        return (
            pd.DataFrame(columns=FVG_COLUMNS),
            pd.DataFrame(columns=LIFECYCLE_COLUMNS),
        )

    # 2. Bảo vệ chống NaN/NaT
    if df["time_utc"].isna().any():
        raise ValueError("Cột 'time_utc' chứa giá trị NaN/NaT.")
    if (
        df["open"].isna().any()
        or df["high"].isna().any()
        or df["low"].isna().any()
        or df["close"].isna().any()
    ):
        raise ValueError("Cột giá ('open', 'high', 'low', hoặc 'close') chứa giá trị NaN.")

    # 3. Chuẩn hóa time_utc sang UTC naive nhất quán
    time_series = pd.to_datetime(df["time_utc"])
    if time_series.dt.tz is not None:
        time_series = time_series.dt.tz_convert("UTC").dt.tz_localize(None)

    sorted_df = df.copy()
    sorted_df["time_utc"] = time_series
    sorted_df = sorted_df.sort_values("time_utc", kind="stable").reset_index(drop=True)

    # 4. Kiểm tra thời gian không trùng lặp
    if sorted_df["time_utc"].duplicated().any():
        raise ValueError("Dữ liệu 'time_utc' chứa các mốc thời gian bị trùng lặp.")

    n_bars = len(sorted_df)

    # 5. Tính toán ATR chuẩn Wilder
    # QUY TẮC KHÔNG LOOK-AHEAD:
    # Tại nến i, atr_ref là ATR[i-1] (đã chốt khi nến i-1 đóng cửa).
    highs = sorted_df["high"].to_numpy(dtype=np.float64)
    lows = sorted_df["low"].to_numpy(dtype=np.float64)
    closes = sorted_df["close"].to_numpy(dtype=np.float64)
    times = sorted_df["time_utc"].to_numpy()

    atr_series = compute_wilder_atr(highs, lows, closes, period=atr_period)

    # 6. Phát hiện các ứng viên FVG
    fvg_records: list[dict[str, object]] = []
    # Lưu thông tin phục vụ quét vòng đời: list các tuple
    fvg_meta: list[dict[str, object]] = []

    time_dur_ns = bar_duration.to_numpy()

    for i in range(2, n_bars):
        # Nến i phải có ATR[i-1] hợp lệ (không NaN và > 0)
        # Bỏ qua nếu ATR chưa đủ nến khởi động (14 nến đầu) hoặc dữ liệu dị thường (ATR <= 0)
        atr_ref = atr_series[i - 1]
        if np.isnan(atr_ref) or atr_ref <= 0.0:
            continue

        min_gap = gamma * atr_ref

        # Kiểm tra bước thời gian vắt qua cuối tuần / ngày nghỉ
        # times[i] - times[i-1] và times[i-1] - times[i-2]
        dt1 = times[i - 1] - times[i - 2]
        dt2 = times[i] - times[i - 1]
        spans_gap = bool((dt1 > time_dur_ns) or (dt2 > time_dur_ns))

        # Kiểm tra Bullish FVG: Low[i] > High[i-2]
        if lows[i] > highs[i - 2]:
            top = float(lows[i])
            bottom = float(highs[i - 2])
            gap_size = top - bottom
            if gap_size >= min_gap:
                t_candle = pd.Timestamp(times[i])
                t_avail = t_candle + bar_duration
                fvg_id = len(fvg_records)
                fvg_item = {
                    "fvg_id": fvg_id,
                    "direction": "BULLISH",
                    "bar_index": i,
                    "time_candle": t_candle,
                    "time_available": t_avail,
                    "top": top,
                    "bottom": bottom,
                    "near_edge": top,
                    "far_edge": bottom,
                    "gap_size": gap_size,
                    "atr_ref": float(atr_ref),
                    "spans_gap": spans_gap,
                }
                fvg_records.append(fvg_item)
                fvg_meta.append(fvg_item)

        # Kiểm tra Bearish FVG: High[i] < Low[i-2]
        elif highs[i] < lows[i - 2]:
            top = float(lows[i - 2])
            bottom = float(highs[i])
            gap_size = top - bottom
            if gap_size >= min_gap:
                t_candle = pd.Timestamp(times[i])
                t_avail = t_candle + bar_duration
                fvg_id = len(fvg_records)
                fvg_item = {
                    "fvg_id": fvg_id,
                    "direction": "BEARISH",
                    "bar_index": i,
                    "time_candle": t_candle,
                    "time_available": t_avail,
                    "top": top,
                    "bottom": bottom,
                    "near_edge": bottom,
                    "far_edge": top,
                    "gap_size": gap_size,
                    "atr_ref": float(atr_ref),
                    "spans_gap": spans_gap,
                }
                fvg_records.append(fvg_item)
                fvg_meta.append(fvg_item)

    if not fvg_records:
        return (
            pd.DataFrame(columns=FVG_COLUMNS),
            pd.DataFrame(columns=LIFECYCLE_COLUMNS),
        )

    fvg_df = pd.DataFrame(fvg_records)[FVG_COLUMNS]

    # 7. Theo dõi Nhật ký vòng đời bất biến cho từng FVG
    lifecycle_records: list[dict[str, object]] = []

    for fvg in fvg_meta:
        fid = int(fvg["fvg_id"])
        direction = str(fvg["direction"])
        i = int(fvg["bar_index"])
        top = float(fvg["top"])
        bottom = float(fvg["bottom"])
        near_edge = float(fvg["near_edge"])
        far_edge = float(fvg["far_edge"])
        t_avail_i = fvg["time_available"]
        t_candle_i = fvg["time_candle"]

        # Sự kiện CREATED tại nến i
        lifecycle_records.append({
            "lifecycle_id": len(lifecycle_records),
            "fvg_id": fid,
            "event_type": "CREATED",
            "bar_index": i,
            "time_candle": t_candle_i,
            "time_available": t_avail_i,
            "price_trigger": near_edge,
        })

        # Quét tiến j = i+1, i+2, ...
        tested = False
        just_tested_at_m = False

        for j in range(i + 1, n_bars):
            t_candle_j = pd.Timestamp(times[j])
            t_avail_j = t_candle_j + bar_duration
            close_j = closes[j]
            high_j = highs[j]
            low_j = lows[j]

            # Xử lý trường hợp nến trước đó vừa ghi TESTED tại j - i == M:
            # Thì nến này (j == i+M+1) sẽ được ghi EXPIRED (trừ khi bị INVALIDATED trước)
            if just_tested_at_m:
                # Kiểm tra xem nến này có INVALIDATED không
                inv_now = (close_j < bottom) if direction == "BULLISH" else (close_j > top)
                if inv_now:
                    lifecycle_records.append({
                        "lifecycle_id": len(lifecycle_records),
                        "fvg_id": fid,
                        "event_type": "INVALIDATED",
                        "bar_index": j,
                        "time_candle": t_candle_j,
                        "time_available": t_avail_j,
                        "price_trigger": close_j,
                    })
                else:
                    lifecycle_records.append({
                        "lifecycle_id": len(lifecycle_records),
                        "fvg_id": fid,
                        "event_type": "EXPIRED",
                        "bar_index": j,
                        "time_candle": t_candle_j,
                        "time_available": t_avail_j,
                        "price_trigger": np.nan,
                    })
                break

            # Thứ tự kiểm tra tại mỗi nến j:
            # 1. Invalidation: Đóng cửa xuyên mép xa
            is_invalidated = (close_j < bottom) if direction == "BULLISH" else (close_j > top)
            if is_invalidated:
                lifecycle_records.append({
                    "lifecycle_id": len(lifecycle_records),
                    "fvg_id": fid,
                    "event_type": "INVALIDATED",
                    "bar_index": j,
                    "time_candle": t_candle_j,
                    "time_available": t_avail_j,
                    "price_trigger": close_j,
                })
                break

            # 2. Touch / Test: Chạm mép gần (chỉ ghi nhận 1 lần duy nhất)
            if not tested:
                touches_near = (low_j <= top) if direction == "BULLISH" else (high_j >= bottom)
                if touches_near:
                    lifecycle_records.append({
                        "lifecycle_id": len(lifecycle_records),
                        "fvg_id": fid,
                        "event_type": "TESTED",
                        "bar_index": j,
                        "time_candle": t_candle_j,
                        "time_available": t_avail_j,
                        "price_trigger": near_edge,
                    })
                    tested = True

                    # Nếu chạm mép gần đúng ở nến thứ M:
                    if (j - i) == m:
                        just_tested_at_m = True
                        continue

            # 3. Expiration: j - i >= M
            if (j - i) >= m:
                lifecycle_records.append({
                    "lifecycle_id": len(lifecycle_records),
                    "fvg_id": fid,
                    "event_type": "EXPIRED",
                    "bar_index": j,
                    "time_candle": t_candle_j,
                    "time_available": t_avail_j,
                    "price_trigger": np.nan,
                })
                break

    if not lifecycle_records:
        return fvg_df, pd.DataFrame(columns=LIFECYCLE_COLUMNS)

    lifecycle_df = pd.DataFrame(lifecycle_records)

    # Sắp xếp ổn định theo thời gian: time_available -> fvg_id -> thứ tự sự kiện
    # Thứ tự ưu tiên sự kiện cùng thời điểm: CREATED (0) -> TESTED (1) -> INVALIDATED (2) -> EXPIRED (3)
    event_order_map = {"CREATED": 0, "TESTED": 1, "INVALIDATED": 2, "EXPIRED": 3}
    lifecycle_df["_event_order"] = lifecycle_df["event_type"].map(event_order_map).fillna(99)
    lifecycle_df = lifecycle_df.sort_values(
        by=["time_available", "fvg_id", "_event_order"],
        kind="stable",
    ).reset_index(drop=True)

    # Gán lifecycle_id tuần tự từ 0 sau khi sắp xếp
    lifecycle_df["lifecycle_id"] = np.arange(len(lifecycle_df), dtype=int)
    lifecycle_df = lifecycle_df[LIFECYCLE_COLUMNS]

    return fvg_df, lifecycle_df


def get_active_fvg_at(
    fvg_df: pd.DataFrame,
    lifecycle_df: pd.DataFrame,
    current_time: pd.Timestamp | str,
    valid_only: bool = True,
) -> pd.DataFrame:
    """Truy xuất các FVG đang có hiệu lực (active) tại current_time.

    Điều kiện FVG đang active tại current_time:
    1. Đã được tạo: CREATED.time_available <= current_time.
    2. Chưa kết thúc: Không có sự kiện kết thúc (INVALIDATED hoặc EXPIRED)
       với time_available <= current_time.
    3. Nếu valid_only=True: Chỉ lấy các FVG có spans_gap == False (bộ lọc chuẩn PRIMARY).

    Tham số:
        fvg_df: DataFrame danh sách FVG từ detect_fvg.
        lifecycle_df: DataFrame nhật ký vòng đời từ detect_fvg.
        current_time: Thời điểm truy vấn (Timestamp hoặc chuỗi).
        valid_only: Nếu True, chỉ lấy các FVG liên tục không vắt qua cuối tuần/giờ nghỉ.

    Trả về:
        DataFrame chứa các FVG đang active kèm trạng thái đã test hay chưa.
    """
    if fvg_df.empty or lifecycle_df.empty:
        return pd.DataFrame(columns=FVG_COLUMNS + ["is_tested"])

    # Chuẩn hóa current_time sang UTC naive
    ts = pd.to_datetime(current_time)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)

    lc_avail = pd.to_datetime(lifecycle_df["time_available"])
    if lc_avail.dt.tz is not None:
        lc_avail = lc_avail.dt.tz_convert("UTC").dt.tz_localize(None)

    # 1. Các FVG đã CREATED trước hoặc tại current_time
    created_mask = (lifecycle_df["event_type"] == "CREATED") & (lc_avail <= ts)
    created_fvg_ids = set(lifecycle_df.loc[created_mask, "fvg_id"])

    # 2. Các FVG đã kết thúc (INVALIDATED hoặc EXPIRED) trước hoặc tại current_time
    term_mask = (lifecycle_df["event_type"].isin(["INVALIDATED", "EXPIRED"])) & (lc_avail <= ts)
    term_fvg_ids = set(lifecycle_df.loc[term_mask, "fvg_id"])

    # 3. Các FVG đã TESTED trước hoặc tại current_time
    tested_mask = (lifecycle_df["event_type"] == "TESTED") & (lc_avail <= ts)
    tested_fvg_ids = set(lifecycle_df.loc[tested_mask, "fvg_id"])

    active_ids = created_fvg_ids - term_fvg_ids
    if not active_ids:
        return pd.DataFrame(columns=FVG_COLUMNS + ["is_tested"])

    active_df = fvg_df[fvg_df["fvg_id"].isin(active_ids)].copy()

    if valid_only:
        active_df = active_df[~active_df["spans_gap"]].copy()

    active_df["is_tested"] = active_df["fvg_id"].isin(tested_fvg_ids)
    return active_df.reset_index(drop=True)


def load_fvg(
    start_date: str,
    end_date: str,
    warmup_months: int | None = None,
    gamma: float = DEFAULT_GAMMA,
    m: int = DEFAULT_M,
    atr_period: int = DEFAULT_ATR_PERIOD,
    bar_duration: pd.Timedelta = DEFAULT_H1_DURATION,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Nạp dữ liệu H1 và phát hiện FVG kèm nhật ký vòng đời chuẩn SMC.

    Tự động áp dụng warm-up để triệt tiêu ảnh hưởng của 14 nến khởi động ATR ban đầu
    và bảo toàn các FVG tạo trước start_date nhưng còn hiệu lực tại start_date:
    1. Nếu warmup_months=None (mặc định): Nạp toàn bộ lịch sử từ đầu kho dữ liệu DuckDB
       (thời gian nạp <1s), tính toán FVG liên tục.
    2. Nếu warmup_months là số nguyên: Nạp từ start_date lùi về trước warmup_months tháng.
    3. Lọc: Loại bỏ những FVG đã kết thúc (INVALIDATED hoặc EXPIRED) trước start_date.
       Giữ lại toàn bộ các FVG chưa kết thúc trước start_date (bao gồm cả dòng CREATED
       và toàn bộ nhật ký vòng đời của chúng).

    Tham số:
        start_date: Ngày bắt đầu lấy tín hiệu ('YYYY-MM-DD').
        end_date: Ngày kết thúc ('YYYY-MM-DD').
        warmup_months: Số tháng khởi động nạp trước start_date (mặc định None: nạp từ đầu kho dữ liệu).
        gamma: Hệ số lọc kích thước FVG (mặc định 0.15).
        m: Số nến tối đa trước khi FVG hết hạn (mặc định 24 nến).
        atr_period: Chu kỳ tính Wilder ATR (mặc định 14).
        bar_duration: Độ dài nến (mặc định 1 giờ đối với H1).

    Trả về:
        tuple (fvg_df, lifecycle_df) chứa các FVG còn hiệu lực hoặc hình thành trong giai đoạn.
    """
    from ..data import load_h1

    if warmup_months is None:
        df_h1 = load_h1(start_date=None, end_date=end_date)
    else:
        ts_start = pd.to_datetime(start_date)
        ts_warmup = ts_start - pd.DateOffset(months=warmup_months)
        warmup_start_str = ts_warmup.strftime("%Y-%m-%d")
        df_h1 = load_h1(warmup_start_str, end_date)

    fvg_all, lc_all = detect_fvg(
        df_h1,
        gamma=gamma,
        m=m,
        atr_period=atr_period,
        bar_duration=bar_duration,
    )

    if fvg_all.empty:
        return fvg_all, lc_all

    start_utc = pd.to_datetime(start_date)
    if start_utc.tzinfo is not None:
        start_utc = start_utc.tz_convert("UTC").tz_localize(None)

    lc_avail = pd.to_datetime(lc_all["time_available"])
    if lc_avail.dt.tz is not None:
        lc_avail = lc_avail.dt.tz_convert("UTC").dt.tz_localize(None)

    # 1. Các FVG đã kết thúc (INVALIDATED hoặc EXPIRED) trước mốc start_utc
    term_mask = (lc_all["event_type"].isin(["INVALIDATED", "EXPIRED"])) & (lc_avail < start_utc)
    terminated_before_start = set(lc_all.loc[term_mask, "fvg_id"])

    # 2. Giữ lại mọi FVG chưa kết thúc trước start_utc
    fvg_filtered = fvg_all[~fvg_all["fvg_id"].isin(terminated_before_start)].copy().reset_index(drop=True)
    kept_fvg_ids = set(fvg_filtered["fvg_id"])

    # 3. Lọc bảng vòng đời tương ứng với các FVG còn sống (bao gồm cả dòng CREATED của chúng)
    lc_filtered = lc_all[lc_all["fvg_id"].isin(kept_fvg_ids)].copy().reset_index(drop=True)

    return fvg_filtered, lc_filtered
