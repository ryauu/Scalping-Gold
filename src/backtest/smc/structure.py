"""Mô-đun structure.py - Phát hiện Cấu trúc thị trường SMC (BOS & CHoCH).

Định nghĩa toán học và quy tắc vận hành (theo SMC_SPEC.md v2 mục 3):
1. Trạng thái cấu trúc thị trường (Market Trend State):
   - 'NEUTRAL': Trạng thái khởi tạo ban đầu khi chưa ghi nhận bất kỳ sự kiện phá vỡ nào.
   - 'BULLISH': Cấu trúc tăng giá (sau khi có Bullish Init Break, Bullish BOS hoặc Bullish CHoCH).
   - 'BEARISH': Cấu trúc giảm giá (sau khi có Bearish Init Break, Bearish BOS hoặc Bearish CHoCH).

2. Mức đỉnh/đáy được bảo vệ (Protected Level - 0 bậc tự do):
   - Protected High: Swing High chưa bị phá gần nhất (last unbroken Swing High theo thời gian time_available,
     hòa thì lấy bar_index lớn hơn) có time_available <= open_time[t].
   - Protected Low: Swing Low chưa bị phá gần nhất (last unbroken Swing Low theo thời gian time_available,
     hòa thì lấy bar_index lớn hơn) có time_available <= open_time[t].

3. Phân định rạch ròi giữa INIT_BREAK, BOS và CHoCH:
   - INIT_BREAK: Phá vỡ mức swing đầu tiên từ trạng thái NEUTRAL ('BULLISH_INIT_BREAK' / 'BEARISH_INIT_BREAK').
     Chỉ dùng xác lập xu hướng ban đầu, TUYỆT ĐỐI KHÔNG dùng làm tín hiệu giao dịch tiếp diễn.
   - BOS: Phá vỡ CÙNG CHIỀU với xu hướng đã xác lập ('BULLISH_BOS' khi đang BULLISH phá đỉnh,
     'BEARISH_BOS' khi đang BEARISH phá đáy). Tín hiệu tiếp diễn xu hướng.
   - CHoCH: Phá vỡ NGƯỢC CHIỀU với xu hướng đã xác lập ('BEARISH_CHOCH' khi đang BULLISH phá đáy,
     'BULLISH_CHOCH' khi đang BEARISH phá đỉnh). Lập tức đảo chiều trạng thái xu hướng.

4. Quy tắc "Chỉ phá một lần" (First Break Only) & Xóa bỏ Swing bị che khuất (Shadowed Swings):
   - Khi một Swing High bị phá (Close[t] > P): Mức đó và MỌI Swing High chưa bị phá có giá <= max(P, Close[t])
     đều lập tức chuyển sang trạng thái BROKEN.
   - Khi một Swing Low bị phá (Close[t] < P): Mức đó và MỌI Swing Low chưa bị phá có giá >= min(P, Close[t])
     đều lập tức chuyển sang trạng thái BROKEN.

5. Thời điểm khả dụng (time_available):
   - time_available của sự kiện cấu trúc = close_time[t] = open_time[t] + bar_duration.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .swings import DEFAULT_H1_DURATION, DEFAULT_K, detect_swings


def detect_structure(
    df: pd.DataFrame,
    swings_df: pd.DataFrame | None = None,
    k: int = DEFAULT_K,
    bar_duration: pd.Timedelta = DEFAULT_H1_DURATION,
) -> pd.DataFrame:
    """Phát hiện toàn bộ các sự kiện BOS và CHoCH trên chuỗi nến H1.

    Tham số:
        df: DataFrame chứa các nến H1 với tối thiểu các cột 'time_utc', 'close', 'high', 'low'.
        swings_df: DataFrame đầu ra từ detect_swings (nếu None, tự động gọi detect_swings(df, k=k)).
        k: Tham số k dùng để phát hiện swing nếu swings_df chưa được cung cấp.
        bar_duration: Độ dài nến để tính time_available (mặc định 1 giờ đối với H1).

    Trả về:
        DataFrame chứa các sự kiện cấu trúc với các cột:
        - structure_id: int (tăng dần từ 0)
        - event_type: str ('BULLISH_INIT_BREAK', 'BEARISH_INIT_BREAK',
                           'BULLISH_BOS', 'BEARISH_BOS',
                           'BULLISH_CHOCH', 'BEARISH_CHOCH')
        - bar_index: int (chỉ số nến t phá vỡ cấu trúc)
        - time_candle: pd.Timestamp (giờ mở nến t)
        - time_available: pd.Timestamp (giờ đóng nến t: time_utc[t] + bar_duration)
        - price_broken: float (mức giá swing bị phá vỡ)
        - broken_swing_id: int (định danh swing_id của mức swing chính bị phá)
        - broken_swing_bar_index: int (chỉ số nến tạo ra swing bị phá)
        - close_price: float (giá đóng cửa của nến t)
        - prior_trend: str ('NEUTRAL', 'BULLISH', 'BEARISH')
        - new_trend: str ('BULLISH', 'BEARISH')

    LƯU Ý QUAN TRỌNG VỀ ĐỊNH DANH (IDENTIFIERS):
        bar_index, structure_id, broken_swing_id, broken_swing_bar_index chỉ có ý nghĩa
        cục bộ trong từng lần nạp dữ liệu (tương đối theo hàng đầu tiên được nạp).
        Các mô-đun hạ nguồn (FVG, Liquidity sweeps) TUYỆT ĐỐI KHÔNG dùng các ID/index
        cục bộ này để ghép nối, mà phải nối bảng theo mốc thời gian vật lý (time_candle, time_available).
    """
    columns_out = [
        "structure_id",
        "event_type",
        "bar_index",
        "time_candle",
        "time_available",
        "price_broken",
        "broken_swing_id",
        "broken_swing_bar_index",
        "close_price",
        "prior_trend",
        "new_trend",
    ]

    required_cols = {"time_utc", "close", "high", "low"}
    # 1. Kiểm tra đủ cột trước tiên (kể cả DataFrame rỗng, đồng nhất quy ước với swings.py)
    if not required_cols.issubset(df.columns):
        raise KeyError(f"DataFrame cần có đủ các cột: {required_cols}")

    if df.empty or len(df) == 0:
        return pd.DataFrame(columns=columns_out)

    # 2. Bảo vệ chống NaN
    if df["time_utc"].isna().any():
        raise ValueError("Cột 'time_utc' chứa giá trị NaN/NaT.")
    if df["close"].isna().any() or df["high"].isna().any() or df["low"].isna().any():
        raise ValueError("Cột giá ('close', 'high', hoặc 'low') chứa giá trị NaN.")

    # 3. Chuẩn hóa time_utc sang UTC naive nhất quán
    time_series = pd.to_datetime(df["time_utc"])
    if time_series.dt.tz is not None:
        time_series = time_series.dt.tz_convert("UTC").dt.tz_localize(None)

    sorted_df = df.copy()
    sorted_df["time_utc"] = time_series
    sorted_df = sorted_df.sort_values("time_utc", kind="stable").reset_index(drop=True)

    # 4. Kiểm tra thời gian không trùng lặp (kết hợp với sort_values đảm bảo tăng dần nghiêm ngặt)
    if sorted_df["time_utc"].duplicated().any():
        raise ValueError("Dữ liệu 'time_utc' chứa các mốc thời gian bị trùng lặp.")

    n_bars = len(sorted_df)

    # Phát hiện swings nếu chưa có
    if swings_df is None:
        swings = detect_swings(sorted_df, k=k, bar_duration=bar_duration)
    else:
        swings = swings_df.copy()

    if swings.empty:
        return pd.DataFrame(columns=columns_out)

    # Đảm bảo sắp xếp swings ổn định theo time_available, bar_index, swing_type
    swings = swings.sort_values(
        ["time_available", "bar_index", "swing_type"], kind="stable"
    ).reset_index(drop=True)

    times = sorted_df["time_utc"].tolist()
    times_np = sorted_df["time_utc"].to_numpy()
    closes = sorted_df["close"].to_numpy(dtype=np.float64)

    # Trích xuất mảng numpy từ swings để tối ưu hóa tốc độ vòng lặp (tránh gọi swings.iloc liên tục)
    sw_avails = pd.to_datetime(swings["time_available"]).to_numpy()
    sw_types = swings["swing_type"].to_numpy()
    sw_prices = swings["price"].to_numpy(dtype=np.float64)
    sw_ids = swings["swing_id"].to_numpy(dtype=np.int64)
    sw_bars = swings["bar_index"].to_numpy(dtype=np.int64)

    # Danh sách các swing chưa bị phá: lưu trữ dict
    # Mỗi phần tử: {'swing_id': int, 'price': float, 'bar_index': int, 'time_available': pd.Timestamp}
    unbroken_highs: list[dict[str, object]] = []
    unbroken_lows: list[dict[str, object]] = []

    trend_state = "NEUTRAL"
    records: list[dict[str, object]] = []

    # Con trỏ duyệt swings
    swing_idx = 0
    n_swings = len(swings)

    for t in range(n_bars):
        t_open = times[t]
        t_open_np = times_np[t]
        t_close = t_open + bar_duration
        close_t = closes[t]

        # Nạp tất cả các swing có time_available <= t_open (không look-ahead)
        while swing_idx < n_swings:
            if sw_avails[swing_idx] <= t_open_np:
                sw_item = {
                    "swing_id": int(sw_ids[swing_idx]),
                    "price": float(sw_prices[swing_idx]),
                    "bar_index": int(sw_bars[swing_idx]),
                    "time_available": pd.Timestamp(sw_avails[swing_idx]),
                }
                if sw_types[swing_idx] == "HIGH":
                    unbroken_highs.append(sw_item)
                elif sw_types[swing_idx] == "LOW":
                    unbroken_lows.append(sw_item)
                swing_idx += 1
            else:
                break

        # Xác định mức được bảo vệ (Protected Level - gần nhất theo thời gian time_available)
        protected_high = unbroken_highs[-1] if unbroken_highs else None
        protected_low = unbroken_lows[-1] if unbroken_lows else None

        event_type: str | None = None
        broken_target: dict[str, object] | None = None
        broken_side: str | None = None
        prior_trend = trend_state
        new_trend = trend_state

        # Phân định sự kiện theo ma trận trạng thái xu hướng (SMC_SPEC.md mục 3.2 v2)
        if trend_state == "BULLISH":
            # 1. Phá đỉnh được bảo vệ -> Tiếp diễn tăng (BULLISH_BOS)
            if protected_high is not None and close_t > float(protected_high["price"]):
                event_type = "BULLISH_BOS"
                broken_target = protected_high
                broken_side = "HIGH"
                new_trend = "BULLISH"
            # 2. Phá đáy được bảo vệ -> Đảo chiều giảm (BEARISH_CHOCH)
            elif protected_low is not None and close_t < float(protected_low["price"]):
                event_type = "BEARISH_CHOCH"
                broken_target = protected_low
                broken_side = "LOW"
                new_trend = "BEARISH"
                trend_state = "BEARISH"

        elif trend_state == "BEARISH":
            # 1. Phá đáy được bảo vệ -> Tiếp diễn giảm (BEARISH_BOS)
            if protected_low is not None and close_t < float(protected_low["price"]):
                event_type = "BEARISH_BOS"
                broken_target = protected_low
                broken_side = "LOW"
                new_trend = "BEARISH"
            # 2. Phá đỉnh được bảo vệ -> Đảo chiều tăng (BULLISH_CHOCH)
            elif protected_high is not None and close_t > float(protected_high["price"]):
                event_type = "BULLISH_CHOCH"
                broken_target = protected_high
                broken_side = "HIGH"
                new_trend = "BULLISH"
                trend_state = "BULLISH"

        elif trend_state == "NEUTRAL":
            # Khi NEUTRAL: Phá đỉnh -> Khởi tạo BULLISH_INIT_BREAK; Phá đáy -> Khởi tạo BEARISH_INIT_BREAK
            # Tuyệt đối không sinh CHoCH khi đang ở NEUTRAL, và không dùng làm tín hiệu BOS tiếp diễn
            if protected_high is not None and close_t > float(protected_high["price"]):
                event_type = "BULLISH_INIT_BREAK"
                broken_target = protected_high
                broken_side = "HIGH"
                new_trend = "BULLISH"
                trend_state = "BULLISH"
            elif protected_low is not None and close_t < float(protected_low["price"]):
                event_type = "BEARISH_INIT_BREAK"
                broken_target = protected_low
                broken_side = "LOW"
                new_trend = "BEARISH"
                trend_state = "BEARISH"

        # Nếu có sự kiện kích hoạt
        if event_type is not None and broken_target is not None and broken_side is not None:
            p_broken = float(broken_target["price"])

            records.append({
                "structure_id": len(records),
                "event_type": event_type,
                "bar_index": int(t),
                "time_candle": t_open,
                "time_available": t_close,
                "price_broken": p_broken,
                "broken_swing_id": int(broken_target["swing_id"]),
                "broken_swing_bar_index": int(broken_target["bar_index"]),
                "close_price": float(close_t),
                "prior_trend": prior_trend,
                "new_trend": new_trend,
            })

            # Quy tắc First Break & Vô hiệu hóa Swing cũ bị che khuất (Shadowed Swings Elimination):
            # Nếu phá High: Toàn bộ Swing High chưa bị phá có giá <= max(P, close_t) chuyển sang BROKEN
            if broken_side == "HIGH":
                threshold = max(p_broken, close_t)
                unbroken_highs = [h for h in unbroken_highs if float(h["price"]) > threshold]

            # Nếu phá Low: Toàn bộ Swing Low chưa bị phá có giá >= min(P, close_t) chuyển sang BROKEN
            elif broken_side == "LOW":
                threshold = min(p_broken, close_t)
                unbroken_lows = [l for l in unbroken_lows if float(l["price"]) < threshold]

    if not records:
        return pd.DataFrame(columns=columns_out)

    structure_df = pd.DataFrame(records)
    structure_df = structure_df.sort_values(
        ["time_available", "bar_index", "event_type"], kind="stable"
    ).reset_index(drop=True)
    structure_df["structure_id"] = structure_df.index
    return structure_df[columns_out]


def get_structure_available_at(
    structure_df: pd.DataFrame,
    current_time: pd.Timestamp | str,
    event_type: str | None = None,
) -> pd.DataFrame:
    """Truy xuất các sự kiện cấu trúc đã hoàn tất và khả dụng tại current_time (chống look-ahead).

    Tham số:
        structure_df: DataFrame đầu ra từ detect_structure.
        current_time: Thời điểm hiện tại cần lọc.
        event_type: Lọc theo loại sự kiện ('BULLISH_INIT_BREAK', 'BULLISH_BOS', v.v.), None lấy tất cả.

    Trả về:
        DataFrame các sự kiện có time_available <= current_time.
    """
    if structure_df.empty:
        return structure_df.copy()

    # Chuẩn hóa current_time sang UTC naive nhất quán
    ts = pd.to_datetime(current_time)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)

    avail_series = pd.to_datetime(structure_df["time_available"])
    if avail_series.dt.tz is not None:
        avail_series = avail_series.dt.tz_convert("UTC").dt.tz_localize(None)

    mask = avail_series <= ts
    if event_type is not None:
        mask = mask & (structure_df["event_type"] == event_type.strip().upper())

    return structure_df[mask].copy().reset_index(drop=True)


def load_structure(
    start_date: str,
    end_date: str,
    warmup_months: int | None = None,
    k: int = DEFAULT_K,
    bar_duration: pd.Timedelta = DEFAULT_H1_DURATION,
) -> pd.DataFrame:
    """Nạp dữ liệu H1 kèm giai đoạn khởi động (warm-up) và phát hiện cấu trúc chuẩn SMC.

    Tự động đảm bảo quy tắc warm-up để triệt tiêu ảnh hưởng của trạng thái ban đầu NEUTRAL:
    1. Nếu warmup_months=None (mặc định): Nạp toàn bộ lịch sử từ đầu kho dữ liệu DuckDB
       (từ 2014, ~100k nến H1, thời gian nạp <1s). Đây là quy tắc chuẩn hóa chính thức
       đảm bảo không còn bất kỳ giả định hay phân kỳ ký ức nào qua các tập Development, Robustness, Holdout.
    2. Nếu warmup_months là số nguyên: Nạp từ start_date lùi về trước warmup_months tháng (ví dụ 24 tháng).
    3. Tính toán liên tục Swings và Cấu trúc thị trường trên toàn bộ dải dữ liệu [warmup_start, end_date].
    4. Lọc lấy các sự kiện cấu trúc có time_available >= start_date (chuẩn UTC naive).

    Lưu ý về định danh:
        bar_index và structure_id là chỉ số cục bộ của lát cắt dữ liệu.
        Khi ghép nối với các bảng khác (FVG, sweeps), luôn ghép theo mốc thời gian vật lý
        (time_candle, time_available).

    Tham số:
        start_date: Ngày bắt đầu lấy tín hiệu (chuỗi 'YYYY-MM-DD').
        end_date: Ngày kết thúc (chuỗi 'YYYY-MM-DD').
        warmup_months: Số tháng khởi động nạp trước start_date (mặc định None: nạp từ đầu kho dữ liệu).
        k: Tham số k phát hiện swing (mặc định PRIMARY k=3).
        bar_duration: Độ dài nến (mặc định 1 giờ đối với H1).

    Trả về:
        DataFrame các sự kiện cấu trúc có time_available >= start_date.
    """
    from ..data import load_h1

    if warmup_months is None:
        df_h1 = load_h1(start_date=None, end_date=end_date)
    else:
        ts_start = pd.to_datetime(start_date)
        ts_warmup = ts_start - pd.DateOffset(months=warmup_months)
        warmup_start_str = ts_warmup.strftime("%Y-%m-%d")
        df_h1 = load_h1(warmup_start_str, end_date)

    struct_all = detect_structure(df_h1, k=k, bar_duration=bar_duration)

    if struct_all.empty:
        return struct_all

    start_utc = pd.to_datetime(start_date)
    if start_utc.tzinfo is not None:
        start_utc = start_utc.tz_convert("UTC").tz_localize(None)

    avail_series = pd.to_datetime(struct_all["time_available"])
    if avail_series.dt.tz is not None:
        avail_series = avail_series.dt.tz_convert("UTC").dt.tz_localize(None)

    mask = avail_series >= start_utc
    return struct_all[mask].copy().reset_index(drop=True)

