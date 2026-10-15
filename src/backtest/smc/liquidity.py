"""Mô-đun liquidity.py - Phát hiện Liquidity Pools và Nhật ký quét thanh khoản chuẩn SMC v2.3.

Theo đặc tả SMC_SPEC.md v2.3 (Mục 5):
1. Bốn nguồn Pool thanh khoản:
   - SWING_HIGH / SWING_LOW: Mọi swing high và swing low đã xác nhận (time_available <= t).
     High tạo Buy-side pool (price = High), Low tạo Sell-side pool (price = Low).
     Hạn tuổi tối đa M = 24 nến H1.
   - EQH / EQL (Equal Highs / Equal Lows):
     Hai Swing High (EQH) hoặc hai Swing Low (EQL) liên tiếp cùng loại thỏa mãn:
     * Khoảng cách nến: j2 - j1 <= M (PRIMARY M = 24 nến H1).
     * Bắt buộc có ATR hợp lệ (ATR > 0). Không tạo EQH/EQL khi chưa đủ dữ liệu khởi động ATR.
     * Dung sai giá: |Price1 - Price2| <= eps * ATR(H1, 14)[j2] (PRIMARY eps = 0.08).
     * Ràng buộc chiều giá bắt buộc: Price2 <= Price1 đối với EQH, Price2 >= Price1 đối với EQL.
     * Pool 1 còn ACTIVE tại time_available của Swing 2 (chưa bị SWEPT, BROKEN, hay EXPIRED).
     * Mức giá của Pool: Price_EQH = Price1, Price_EQL = Price1.
     * Quy tắc hấp thụ (Absorption Rule): Khi EQH/EQL hình thành, hai swing cấu thành lập tức
       chuyển sang trạng thái ABSORBED tại time_available của Swing 2.
     * Quy tắc chuỗi (Chained Absorption for Triple/Multi Highs/Lows): Nếu có swing thứ 3 tiếp tục
       thỏa mãn mức giá của EQH/EQL đang ACTIVE và cách swing trước <= M nến, swing 3 sẽ được hấp thụ
       vào chính pool EQH/EQL đó (gia hạn hạn tuổi pool thêm M nến), triệt tiêu hoàn toàn việc sinh
       pool đè gây đếm lặp sự kiện quét.
   - PDH / PDL (Previous Day High / Low):
     Chốt ngày giao dịch theo giờ đóng cửa New York lúc 17:00 NY (xử lý DST tự động qua zoneinfo):
     * Mùa đông (EST, UTC-5): 17:00 NY = 22:00 UTC (nến H1 nhãn 21:00 UTC, đóng lúc 22:00 UTC).
     * Mùa hè (EDT, UTC-4): 17:00 NY = 21:00 UTC (nến H1 nhãn 20:00 UTC, đóng lúc 21:00 UTC).
     * Ngày nghỉ lễ đóng sớm (Holiday Early Close): Khả dụng ngay khi nến cuối cùng trước giờ nghỉ
       đóng cửa (ví dụ MLK Day đóng sớm lúc 19:00 UTC).
     * Hạn tuổi thống nhất: Hết hạn sau M = 24 nến H1 kể từ time_available.
     * Định danh chuẩn: source_id = 'pdh_YYYY-MM-DD' và 'pdl_YYYY-MM-DD'.
   - ASIAN_HIGH / ASIAN_LOW (Biên vùng Á):
     Phiên Á gồm các nến H1 từ 00:00 đến 06:00 UTC (nến 06:00 UTC đóng lúc 07:00 UTC).
     Khả dụng từ 07:00 UTC. Hết hạn sau M = 24 nến H1 (đến 07:00 UTC hôm sau).
     Định danh chuẩn: source_id = 'asian_high_YYYY-MM-DD' và 'asian_low_YYYY-MM-DD'.

2. Máy trạng thái 5 trạng thái của Pool:
   ACTIVE -> SWEPT / BROKEN / EXPIRED / ABSORBED
   - High Pool (Buy-side):
     * Không sự kiện nếu High[t] <= Pool.price (dấu bằng không phát sinh sự kiện).
     * SWEPT: High[t] > Pool.price VÀ Close[t] <= Pool.price (dấu bằng tại Close là SWEPT).
     * BROKEN: Close[t] > Pool.price.
   - Low Pool (Sell-side):
     * Không sự kiện nếu Low[t] >= Pool.price (dấu bằng không phát sinh sự kiện).
     * SWEPT: Low[t] < Pool.price VÀ Close[t] >= Pool.price (dấu bằng tại Close là SWEPT).
     * BROKEN: Close[t] < Pool.price.
   - EXPIRED: Khi nến t đạt t - bar_index_avail >= M mà chưa bị vi phạm và chưa bị hấp thụ.
     Quy tắc biên: Tại đúng nến t = bar_index_avail + M (nến thứ 24), pool VẪN CÒN ACTIVE
     trong suốt cây nến để nhận diện vi phạm hoặc hấp thụ thành EQH/EQL; chỉ chuyển sang EXPIRED
     khi nến thứ M đóng cửa mà không bị chạm hay hấp thụ.
   - ABSORBED: Dành cho swing đơn lẻ khi được hợp nhất vào EQH/EQL.

3. Xử lý Nến ngoài (Outside Bar):
   Mỗi pool tự quyết định độc lập theo mức giá của chính nó. Cả hai sự kiện đều phát ra
   tại time_available = close_time[t].

LƯU Ý VỀ ĐỊNH DANH:
   pool_id, bar_index chỉ mang ý nghĩa cục bộ trong từng DataFrame nến.
   Mọi liên kết liên bảng bắt buộc dùng time_candle hoặc time_available.
"""

from __future__ import annotations

import logging
from typing import Sequence
import zoneinfo

import numpy as np
import pandas as pd

from ..data import compute_wilder_atr, load_h1
from .swings import DEFAULT_H1_DURATION, DEFAULT_K, detect_swings

logger = logging.getLogger(__name__)

DEFAULT_M: int = 24
DEFAULT_EPSILON: float = 0.08
DEFAULT_ATR_PERIOD: int = 14

POOL_TYPES = (
    "SWING_HIGH",
    "SWING_LOW",
    "EQH",
    "EQL",
    "PDH",
    "PDL",
    "ASIAN_HIGH",
    "ASIAN_LOW",
)

EVENT_TYPES = (
    "SWEPT",
    "BROKEN",
    "EXPIRED",
    "ABSORBED",
)

POOLS_COLUMNS = [
    "pool_id",
    "pool_type",
    "price",
    "bar_index_candle",
    "bar_index_avail",
    "time_candle",
    "time_available",
    "source_id",
]

SWEEPS_COLUMNS = [
    "lifecycle_id",
    "pool_id",
    "pool_type",
    "event_type",
    "bar_index",
    "time_candle",
    "time_available",
    "price_trigger",
    "pool_age",
    "close_price",
]


def _validate_input_df(df: pd.DataFrame) -> pd.DataFrame:
    """Kiểm tra và chuẩn hóa DataFrame đầu vào."""
    req_cols = {"time_utc", "open", "high", "low", "close"}
    missing = req_cols - set(df.columns)
    if missing:
        raise ValueError(f"Thiếu các cột bắt buộc trong DataFrame: {missing}")

    if len(df) == 0:
        raise ValueError("DataFrame rỗng.")

    for col in ["open", "high", "low", "close"]:
        if df[col].isna().any():
            raise ValueError(f"Cột {col} chứa giá trị NaN.")
        if np.isinf(df[col]).any():
            raise ValueError(f"Cột {col} chứa giá trị vô cực.")

    if df["time_utc"].isna().any():
        raise ValueError("Cột time_utc chứa giá trị NaT/NaN.")

    df_clean = df.copy()
    if df_clean["time_utc"].dt.tz is not None:
        df_clean["time_utc"] = df_clean["time_utc"].dt.tz_convert("UTC").dt.tz_localize(None)

    # Kiểm tra tính đơn điệu tăng dần nghiêm ngặt
    time_diffs = df_clean["time_utc"].diff()
    if len(df_clean) > 1 and not (time_diffs.iloc[1:] > pd.Timedelta(0)).all():
        raise ValueError("Cột time_utc phải tăng dần nghiêm ngặt (không trùng lặp, không đảo lộn).")

    return df_clean


def detect_liquidity_pools(
    df: pd.DataFrame,
    k: int = DEFAULT_K,
    m: int = DEFAULT_M,
    eps: float = DEFAULT_EPSILON,
    bar_duration: pd.Timedelta = DEFAULT_H1_DURATION,
    include_types: Sequence[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Phát hiện toàn bộ các Liquidity Pools và mô phỏng vòng đời máy trạng thái 5 trạng thái.

    Tham số:
        df: DataFrame nến H1 chứa 'time_utc', 'open', 'high', 'low', 'close', và tùy chọn 'atr'.
        k: Tham số số nến lân cận mỗi bên của Swing (PRIMARY k=3).
        m: Hạn tuổi tối đa của Pool và khoảng cách tối đa EQH (PRIMARY m=24).
        eps: Dung sai chênh lệch giá EQH theo ATR (PRIMARY eps=0.08).
        bar_duration: Độ dài nến (mặc định 1 giờ).
        include_types: Danh sách các loại pool cần theo dõi. Mặc định theo dõi cả 8 loại.

    Trả về:
        (pools_df, sweeps_df)
    """
    df_clean = _validate_input_df(df).reset_index(drop=True)
    n_bars = len(df_clean)

    if include_types is None:
        active_types = set(POOL_TYPES)
    else:
        active_types = set(include_types)

    # Tính Wilder ATR nếu chưa có sẵn
    if "atr" in df_clean.columns and not df_clean["atr"].isna().all():
        atr_arr = df_clean["atr"].values
    else:
        atr_arr = compute_wilder_atr(
            df_clean["high"].values,
            df_clean["low"].values,
            df_clean["close"].values,
            period=DEFAULT_ATR_PERIOD,
        )

    high_arr = df_clean["high"].values
    low_arr = df_clean["low"].values
    close_arr = df_clean["close"].values
    time_utc_arr = df_clean["time_utc"].values

    # 1. Phát hiện Swing High / Low
    swings_df = detect_swings(df_clean, k=k, bar_duration=bar_duration)

    swing_highs: list[dict] = []
    swing_lows: list[dict] = []
    for _, s in swings_df.iterrows():
        s_dict = {
            "swing_id": int(s["swing_id"]),
            "swing_type": s["swing_type"],
            "bar_index_candle": int(s["bar_index"]),
            "bar_index_avail": int(s["bar_index"]) + k,
            "time_candle": pd.to_datetime(s["time_candle"]),
            "time_available": pd.to_datetime(s["time_available"]),
            "price": float(s["price"]),
        }
        if s["swing_type"] == "HIGH":
            swing_highs.append(s_dict)
        else:
            swing_lows.append(s_dict)

    # 2. Phát hiện PDH / PDL (17:00 NY close & Holiday Early Close)
    pdh_pdl_pools: list[dict] = []
    if "PDH" in active_types or "PDL" in active_types:
        time_utc_series = pd.to_datetime(df_clean["time_utc"])
        time_ny_series = time_utc_series.dt.tz_localize("UTC").dt.tz_convert("America/New_York")
        # Trading day chốt lúc 17:00 NY, nến mở lúc 17:00 NY thuộc về ngày giao dịch tiếp theo
        trading_days = (time_ny_series + pd.Timedelta(hours=7)).dt.date

        day_groups = df_clean.groupby(trading_days)
        unique_days = sorted(day_groups.groups.keys())

        for idx_d, d in enumerate(unique_days):
            grp_indices = day_groups.groups[d]
            last_idx = grp_indices[-1]
            last_bar_time = pd.to_datetime(time_utc_arr[last_idx])
            avail_time = last_bar_time + bar_duration

            # Kiểm tra xem trading day này đã thực sự đóng cửa chưa:
            # 1. Nếu không phải ngày cuối trong df -> ngày kế tiếp đã xuất hiện, ngày d đã đóng.
            # 2. Nếu là ngày cuối trong df -> kiểm tra xem nến cuối có đóng lúc 17:00 NY không.
            is_last_day = (idx_d == len(unique_days) - 1)
            if is_last_day:
                ny_close = (last_bar_time + bar_duration).tz_localize("UTC").tz_convert("America/New_York")
                if not (ny_close.hour == 17 and ny_close.minute == 0):
                    # Chưa kết thúc ngày nếu không có nến tiếp theo và chưa đạt 17:00 NY
                    continue

            d_high = float(high_arr[grp_indices].max())
            d_low = float(low_arr[grp_indices].min())
            candle_idx = int(grp_indices[-1])

            if "PDH" in active_types:
                pdh_pdl_pools.append({
                    "pool_type": "PDH",
                    "price": d_high,
                    "bar_index_candle": candle_idx,
                    "bar_index_avail": candle_idx,
                    "time_candle": last_bar_time,
                    "time_available": avail_time,
                    "source_id": f"pdh_{d.isoformat()}",
                })
            if "PDL" in active_types:
                pdh_pdl_pools.append({
                    "pool_type": "PDL",
                    "price": d_low,
                    "bar_index_candle": candle_idx,
                    "bar_index_avail": candle_idx,
                    "time_candle": last_bar_time,
                    "time_available": avail_time,
                    "source_id": f"pdl_{d.isoformat()}",
                })

    # 3. Phát hiện Asian High / Low (00:00 - 06:00 UTC, đóng lúc 07:00 UTC)
    asian_pools: list[dict] = []
    if "ASIAN_HIGH" in active_types or "ASIAN_LOW" in active_types:
        time_utc_dt = pd.to_datetime(df_clean["time_utc"])
        asian_mask = (time_utc_dt.dt.hour >= 0) & (time_utc_dt.dt.hour < 7)
        asian_bars = df_clean[asian_mask]

        if not asian_bars.empty:
            for date_utc, grp in asian_bars.groupby(time_utc_dt[asian_mask].dt.date):
                last_idx = grp.index[-1]
                last_bar_time = pd.to_datetime(time_utc_arr[last_idx])
                avail_time = last_bar_time + bar_duration

                # Phiên Á hoàn chỉnh khi nến 06:00 UTC đóng cửa (avail_time >= 07:00 UTC),
                # hoặc có nến tiếp theo trong df
                session_complete = False
                if last_bar_time.hour == 6:
                    session_complete = True
                elif last_idx + 1 < n_bars:
                    next_time = pd.to_datetime(time_utc_arr[last_idx + 1])
                    if next_time.date() > date_utc or next_time.hour >= 7:
                        session_complete = True

                if not session_complete:
                    continue

                a_high = float(grp["high"].max())
                a_low = float(grp["low"].min())

                if "ASIAN_HIGH" in active_types:
                    asian_pools.append({
                        "pool_type": "ASIAN_HIGH",
                        "price": a_high,
                        "bar_index_candle": int(last_idx),
                        "bar_index_avail": int(last_idx),
                        "time_candle": last_bar_time,
                        "time_available": avail_time,
                        "source_id": f"asian_high_{date_utc.isoformat()}",
                    })
                if "ASIAN_LOW" in active_types:
                    asian_pools.append({
                        "pool_type": "ASIAN_LOW",
                        "price": a_low,
                        "bar_index_candle": int(last_idx),
                        "bar_index_avail": int(last_idx),
                        "time_candle": last_bar_time,
                        "time_available": avail_time,
                        "source_id": f"asian_low_{date_utc.isoformat()}",
                    })

    # 4. Mô phỏng tuần tự theo thời gian (Máy trạng thái hữu hạn tích hợp)
    raw_pools: list[dict] = []
    curr_pool_id = 0
    swing_pool_map: dict[tuple[str, int], int] = {}

    for s in swing_highs:
        if "SWING_HIGH" in active_types or "EQH" in active_types:
            p_dict = {
                "pool_id": curr_pool_id,
                "pool_type": "SWING_HIGH",
                "price": s["price"],
                "bar_index_candle": s["bar_index_candle"],
                "bar_index_avail": s["bar_index_avail"],
                "time_candle": s["time_candle"],
                "time_available": s["time_available"],
                "source_id": f"swing_{s['swing_id']}",
                "is_buy_side": True,
                "state": "PENDING",
                "swing_id": s["swing_id"],
            }
            raw_pools.append(p_dict)
            swing_pool_map[("HIGH", s["swing_id"])] = curr_pool_id
            curr_pool_id += 1

    for s in swing_lows:
        if "SWING_LOW" in active_types or "EQL" in active_types:
            p_dict = {
                "pool_id": curr_pool_id,
                "pool_type": "SWING_LOW",
                "price": s["price"],
                "bar_index_candle": s["bar_index_candle"],
                "bar_index_avail": s["bar_index_avail"],
                "time_candle": s["time_candle"],
                "time_available": s["time_available"],
                "source_id": f"swing_{s['swing_id']}",
                "is_buy_side": False,
                "state": "PENDING",
                "swing_id": s["swing_id"],
            }
            raw_pools.append(p_dict)
            swing_pool_map[("LOW", s["swing_id"])] = curr_pool_id
            curr_pool_id += 1

    for p in pdh_pdl_pools:
        p_dict = {
            "pool_id": curr_pool_id,
            "pool_type": p["pool_type"],
            "price": p["price"],
            "bar_index_candle": p["bar_index_candle"],
            "bar_index_avail": p["bar_index_avail"],
            "time_candle": p["time_candle"],
            "time_available": p["time_available"],
            "source_id": p["source_id"],
            "is_buy_side": (p["pool_type"] == "PDH"),
            "state": "PENDING",
        }
        raw_pools.append(p_dict)
        curr_pool_id += 1

    for p in asian_pools:
        p_dict = {
            "pool_id": curr_pool_id,
            "pool_type": p["pool_type"],
            "price": p["price"],
            "bar_index_candle": p["bar_index_candle"],
            "bar_index_avail": p["bar_index_avail"],
            "time_candle": p["time_candle"],
            "time_available": p["time_available"],
            "source_id": p["source_id"],
            "is_buy_side": (p["pool_type"] == "ASIAN_HIGH"),
            "state": "PENDING",
        }
        raw_pools.append(p_dict)
        curr_pool_id += 1

    pools_becoming_avail: dict[int, list[int]] = {}
    for p in raw_pools:
        avail_bar = p["bar_index_avail"]
        pools_becoming_avail.setdefault(avail_bar, []).append(p["pool_id"])

    swings_avail_at: dict[int, list[dict]] = {}
    for s in swing_highs + swing_lows:
        swings_avail_at.setdefault(s["bar_index_avail"], []).append(s)

    active_pool_ids: list[int] = []
    events_log: list[dict] = []

    # Lưu vết các EQH/EQL pool đang active để thực hiện hấp thụ chuỗi (Chained Absorption)
    active_eqh_pools: list[dict] = []
    active_eql_pools: list[dict] = []

    confirmed_swing_highs: list[dict] = []
    confirmed_swing_lows: list[dict] = []

    for t in range(n_bars):
        t_time_candle = pd.to_datetime(time_utc_arr[t])
        t_time_avail = t_time_candle + bar_duration
        t_high = float(high_arr[t])
        t_low = float(low_arr[t])
        t_close = float(close_arr[t])

        # -----------------------------------------------------------------
        # Bước 1 (Trong phiên nến t): Đánh giá vi phạm giá cho các pool đang ACTIVE
        # Các pool này đã active từ các nến trước (bar_index_avail < t).
        # -----------------------------------------------------------------
        violated_this_bar: set[int] = set()

        for pid in active_pool_ids:
            p = raw_pools[pid]
            if p["state"] != "ACTIVE":
                continue

            age = t - p["bar_index_avail"]
            price_lvl = p["price"]
            is_buy = p["is_buy_side"]

            violated = False
            ev_type = None
            price_trigger = np.nan

            if is_buy:
                # Dấu bằng không phát sinh sự kiện. Chỉ vi phạm khi High[t] > price_lvl
                if t_high > price_lvl:
                    violated = True
                    price_trigger = t_high
                    # Close[t] == price_lvl là SWEPT (chưa vượt hẳn)
                    ev_type = "BROKEN" if t_close > price_lvl else "SWEPT"
            else:
                # Dấu bằng không phát sinh sự kiện. Chỉ vi phạm khi Low[t] < price_lvl
                if t_low < price_lvl:
                    violated = True
                    price_trigger = t_low
                    # Close[t] == price_lvl là SWEPT (chưa vượt hẳn)
                    ev_type = "BROKEN" if t_close < price_lvl else "SWEPT"

            if violated:
                p["state"] = ev_type
                violated_this_bar.add(pid)
                events_log.append({
                    "pool_id": pid,
                    "pool_type": p["pool_type"],
                    "event_type": ev_type,
                    "bar_index": t,
                    "time_candle": t_time_candle,
                    "time_available": t_time_avail,
                    "price_trigger": price_trigger,
                    "pool_age": age,
                    "close_price": t_close,
                })

        active_pool_ids = [pid for pid in active_pool_ids if pid not in violated_this_bar]

        # -----------------------------------------------------------------
        # Bước 2 (Đóng nến t): Xác nhận Swing mới & Kiểm tra Hấp thụ EQH / EQL
        # -----------------------------------------------------------------
        if t in swings_avail_at:
            for s2 in swings_avail_at[t]:
                s2_type = s2["swing_type"]
                j2 = s2["bar_index_candle"]
                p2 = s2["price"]
                atr_ref = atr_arr[j2] if (j2 < len(atr_arr) and not np.isnan(atr_arr[j2])) else 0.0

                if s2_type == "HIGH":
                    if ("HIGH", s2["swing_id"]) not in swing_pool_map:
                        continue
                    absorbed_into_chain = False
                    pid2 = swing_pool_map[("HIGH", s2["swing_id"])]
                    pool2 = raw_pools[pid2]

                    # Bắt buộc có ATR hợp lệ để đánh giá EQH
                    if "EQH" in active_types and atr_ref > 0.0:
                        # 2.1: Kiểm tra Hấp thụ Chuỗi (Chained Absorption) vào pool EQH đang ACTIVE
                        # Tuyệt đối KHÔNG gia hạn tuổi và KHÔNG sửa đổi các trường của pool EQH ban đầu (bảo toàn Prefix Invariance & V2-11)
                        for eqh_cand in active_eqh_pools:
                            eqh_p = raw_pools[eqh_cand["pool_id"]]
                            if eqh_p["state"] == "ACTIVE":
                                p_eqh = eqh_p["price"]

                                if (p2 <= p_eqh) and (abs(p_eqh - p2) <= eps * atr_ref):
                                    # Hấp thụ s2 vào EQH đang có mà không sửa dòng pool EQH
                                    pool2["state"] = "ABSORBED"
                                    events_log.append({
                                        "pool_id": pid2,
                                        "pool_type": pool2["pool_type"],
                                        "event_type": "ABSORBED",
                                        "bar_index": t,
                                        "time_candle": t_time_candle,
                                        "time_available": t_time_avail,
                                        "price_trigger": np.nan,
                                        "pool_age": 0,
                                        "close_price": t_close,
                                    })
                                    absorbed_into_chain = True
                                    break

                        # 2.2: Nếu không hấp thụ chuỗi, kiểm tra tạo EQH mới với swing tiền nhiệm
                        if not absorbed_into_chain and confirmed_swing_highs:
                            s1 = confirmed_swing_highs[-1]
                            j1 = s1["bar_index_candle"]
                            p1 = s1["price"]
                            pid1 = swing_pool_map[("HIGH", s1["swing_id"])]
                            pool1 = raw_pools[pid1]

                            cond_dist = (j2 - j1 <= m)
                            cond_active = (pool1["state"] == "ACTIVE")
                            cond_tol = (abs(p1 - p2) <= eps * atr_ref)
                            cond_dir = (p2 <= p1)

                            if cond_dist and cond_active and cond_tol and cond_dir:
                                eqh_id = curr_pool_id
                                curr_pool_id += 1
                                eqh_pool = {
                                    "pool_id": eqh_id,
                                    "pool_type": "EQH",
                                    "price": p1,
                                    "bar_index_candle": j2,
                                    "bar_index_avail": t,
                                    "time_candle": s2["time_candle"],
                                    "time_available": t_time_avail,
                                    "source_id": f"eqh_{s1['swing_id']}_{s2['swing_id']}",
                                    "is_buy_side": True,
                                    "state": "ACTIVE",
                                }
                                raw_pools.append(eqh_pool)
                                active_pool_ids.append(eqh_id)
                                active_eqh_pools.append({
                                    "pool_id": eqh_id,
                                    "last_swing_candle": j2,
                                })

                                # Hấp thụ Pool 1
                                pool1["state"] = "ABSORBED"
                                if pid1 in active_pool_ids:
                                    active_pool_ids.remove(pid1)
                                events_log.append({
                                    "pool_id": pid1,
                                    "pool_type": pool1["pool_type"],
                                    "event_type": "ABSORBED",
                                    "bar_index": t,
                                    "time_candle": t_time_candle,
                                    "time_available": t_time_avail,
                                    "price_trigger": np.nan,
                                    "pool_age": t - pool1["bar_index_avail"],
                                    "close_price": t_close,
                                })

                                # Hấp thụ Pool 2
                                pool2["state"] = "ABSORBED"
                                events_log.append({
                                    "pool_id": pid2,
                                    "pool_type": pool2["pool_type"],
                                    "event_type": "ABSORBED",
                                    "bar_index": t,
                                    "time_candle": t_time_candle,
                                    "time_available": t_time_avail,
                                    "price_trigger": np.nan,
                                    "pool_age": 0,
                                    "close_price": t_close,
                                })

                    confirmed_swing_highs.append(s2)

                else:
                    # EQL
                    if ("LOW", s2["swing_id"]) not in swing_pool_map:
                        continue
                    absorbed_into_chain = False
                    pid2 = swing_pool_map[("LOW", s2["swing_id"])]
                    pool2 = raw_pools[pid2]

                    if "EQL" in active_types and atr_ref > 0.0:
                        # 2.1: Hấp thụ chuỗi vào EQL đang active
                        # Tuyệt đối KHÔNG gia hạn tuổi và KHÔNG sửa đổi các trường của pool EQL ban đầu (bảo toàn Prefix Invariance & V2-11)
                        for eql_cand in active_eql_pools:
                            eql_p = raw_pools[eql_cand["pool_id"]]
                            if eql_p["state"] == "ACTIVE":
                                p_eql = eql_p["price"]

                                if (p2 >= p_eql) and (abs(p_eql - p2) <= eps * atr_ref):
                                    pool2["state"] = "ABSORBED"
                                    events_log.append({
                                        "pool_id": pid2,
                                        "pool_type": pool2["pool_type"],
                                        "event_type": "ABSORBED",
                                        "bar_index": t,
                                        "time_candle": t_time_candle,
                                        "time_available": t_time_avail,
                                        "price_trigger": np.nan,
                                        "pool_age": 0,
                                        "close_price": t_close,
                                    })
                                    absorbed_into_chain = True
                                    break

                        # 2.2: Tạo EQL mới
                        if not absorbed_into_chain and confirmed_swing_lows:
                            s1 = confirmed_swing_lows[-1]
                            j1 = s1["bar_index_candle"]
                            p1 = s1["price"]
                            pid1 = swing_pool_map[("LOW", s1["swing_id"])]
                            pool1 = raw_pools[pid1]

                            cond_dist = (j2 - j1 <= m)
                            cond_active = (pool1["state"] == "ACTIVE")
                            cond_tol = (abs(p1 - p2) <= eps * atr_ref)
                            cond_dir = (p2 >= p1)

                            if cond_dist and cond_active and cond_tol and cond_dir:
                                eql_id = curr_pool_id
                                curr_pool_id += 1
                                eql_pool = {
                                    "pool_id": eql_id,
                                    "pool_type": "EQL",
                                    "price": p1,
                                    "bar_index_candle": j2,
                                    "bar_index_avail": t,
                                    "time_candle": s2["time_candle"],
                                    "time_available": t_time_avail,
                                    "source_id": f"eql_{s1['swing_id']}_{s2['swing_id']}",
                                    "is_buy_side": False,
                                    "state": "ACTIVE",
                                }
                                raw_pools.append(eql_pool)
                                active_pool_ids.append(eql_id)
                                active_eql_pools.append({
                                    "pool_id": eql_id,
                                    "last_swing_candle": j2,
                                })

                                pool1["state"] = "ABSORBED"
                                if pid1 in active_pool_ids:
                                    active_pool_ids.remove(pid1)
                                events_log.append({
                                    "pool_id": pid1,
                                    "pool_type": pool1["pool_type"],
                                    "event_type": "ABSORBED",
                                    "bar_index": t,
                                    "time_candle": t_time_candle,
                                    "time_available": t_time_avail,
                                    "price_trigger": np.nan,
                                    "pool_age": t - pool1["bar_index_avail"],
                                    "close_price": t_close,
                                })

                                pool2["state"] = "ABSORBED"
                                events_log.append({
                                    "pool_id": pid2,
                                    "pool_type": pool2["pool_type"],
                                    "event_type": "ABSORBED",
                                    "bar_index": t,
                                    "time_candle": t_time_candle,
                                    "time_available": t_time_avail,
                                    "price_trigger": np.nan,
                                    "pool_age": 0,
                                    "close_price": t_close,
                                })

                    confirmed_swing_lows.append(s2)

        # -----------------------------------------------------------------
        # Bước 3: Đánh giá Hết hạn (EXPIRED) cho các pool còn ACTIVE
        # Khi nến t đóng cửa, pool nào có age >= m mà KHÔNG bị vi phạm và KHÔNG bị hấp thụ
        # thì chuyển sang EXPIRED tại close_time[t].
        # -----------------------------------------------------------------
        expired_this_bar: set[int] = set()
        for pid in active_pool_ids:
            p = raw_pools[pid]
            if p["bar_index_avail"] == t:
                continue

            age = t - p["bar_index_avail"]
            if age >= m:
                p["state"] = "EXPIRED"
                expired_this_bar.add(pid)
                events_log.append({
                    "pool_id": pid,
                    "pool_type": p["pool_type"],
                    "event_type": "EXPIRED",
                    "bar_index": t,
                    "time_candle": t_time_candle,
                    "time_available": t_time_avail,
                    "price_trigger": np.nan,
                    "pool_age": age,
                    "close_price": t_close,
                })

        active_pool_ids = [pid for pid in active_pool_ids if pid not in expired_this_bar]

        # -----------------------------------------------------------------
        # Bước 4: Kích hoạt các pool mới có bar_index_avail == t (chưa bị absorbed)
        # -----------------------------------------------------------------
        if t in pools_becoming_avail:
            for pid in pools_becoming_avail[t]:
                p = raw_pools[pid]
                if p["state"] == "PENDING":
                    p["state"] = "ACTIVE"
                    active_pool_ids.append(pid)

    # 5. Lọc bảng pools theo include_types và đóng gói DataFrame chuẩn
    filtered_pools: list[dict] = []
    for p in raw_pools:
        if p["pool_type"] in active_types:
            filtered_pools.append({
                "pool_id": p["pool_id"],
                "pool_type": p["pool_type"],
                "price": p["price"],
                "bar_index_candle": p["bar_index_candle"],
                "bar_index_avail": p["bar_index_avail"],
                "time_candle": p["time_candle"],
                "time_available": p["time_available"],
                "source_id": str(p["source_id"]),
            })

    if filtered_pools:
        # Gán pool_id theo thứ tự thời gian chuẩn (chronological order)
        # đảm bảo tính bất biến tiền tố (prefix invariance) tuyệt đối
        filtered_pools = sorted(
            filtered_pools,
            key=lambda p: (p["time_available"], p["time_candle"], p["bar_index_avail"], p["pool_type"], p["price"])
        )
        old_to_new_pool_id = {}
        for new_id, p in enumerate(filtered_pools):
            old_id = p["pool_id"]
            old_to_new_pool_id[old_id] = new_id
            p["pool_id"] = new_id

        pools_df = pd.DataFrame(filtered_pools)
        pools_df = pools_df[POOLS_COLUMNS]
    else:
        pools_df = pd.DataFrame(columns=POOLS_COLUMNS)
        old_to_new_pool_id = {}

    # Đóng gói sweeps_df / lifecycle_df
    if events_log:
        sweeps_df = pd.DataFrame(events_log)
        sweeps_df = sweeps_df[sweeps_df["pool_id"].isin(old_to_new_pool_id)].copy()
        sweeps_df["pool_id"] = sweeps_df["pool_id"].map(old_to_new_pool_id)

        # Thứ tự ưu tiên sự kiện: SWEPT (0) -> BROKEN (1) -> EXPIRED (2) -> ABSORBED (3)
        ev_prio = {"SWEPT": 0, "BROKEN": 1, "EXPIRED": 2, "ABSORBED": 3}
        sweeps_df["_prio"] = sweeps_df["event_type"].map(ev_prio)

        sweeps_df = sweeps_df.sort_values(
            by=["time_available", "pool_id", "_prio"]
        ).reset_index(drop=True)
        sweeps_df = sweeps_df.drop(columns=["_prio"])
        sweeps_df["lifecycle_id"] = range(len(sweeps_df))
        sweeps_df = sweeps_df[SWEEPS_COLUMNS]
    else:
        sweeps_df = pd.DataFrame(columns=SWEEPS_COLUMNS)

    return pools_df, sweeps_df


def get_active_pools_at(
    pools_df: pd.DataFrame,
    sweeps_df: pd.DataFrame,
    time_now: pd.Timestamp | str,
    pool_type: str | Sequence[str] | None = None,
) -> pd.DataFrame:
    """Trả về danh sách các Liquidity Pools đang ở trạng thái ACTIVE tại thời điểm time_now.

    Một pool là ACTIVE tại time_now nếu:
    - time_available <= time_now
    - Chưa có bất kỳ sự kiện kết thúc nào (SWEPT, BROKEN, EXPIRED, ABSORBED) có time_available <= time_now.
    """
    if pools_df.empty:
        return pools_df.copy()

    t_now = pd.to_datetime(time_now)
    if t_now.tz is not None:
        t_now = t_now.tz_convert("UTC").tz_localize(None)

    # 1. Các pool đã khả dụng tại time_now
    t_avail = pd.to_datetime(pools_df["time_available"])
    if t_avail.dt.tz is not None:
        t_avail = t_avail.dt.tz_convert("UTC").dt.tz_localize(None)
    avail_mask = t_avail <= t_now

    active_candidates = pools_df[avail_mask].copy()
    if active_candidates.empty:
        return active_candidates

    # 2. Loại bỏ các pool đã kết thúc trước hoặc tại time_now
    if not sweeps_df.empty:
        sw_avail = pd.to_datetime(sweeps_df["time_available"])
        if sw_avail.dt.tz is not None:
            sw_avail = sw_avail.dt.tz_convert("UTC").dt.tz_localize(None)

        terminated_events = sweeps_df[sw_avail <= t_now]
        terminated_pool_ids = set(terminated_events["pool_id"])
        active_candidates = active_candidates[~active_candidates["pool_id"].isin(terminated_pool_ids)]

    # 3. Lọc theo pool_type nếu được yêu cầu
    if pool_type is not None:
        if isinstance(pool_type, str):
            active_candidates = active_candidates[active_candidates["pool_type"] == pool_type]
        else:
            active_candidates = active_candidates[active_candidates["pool_type"].isin(pool_type)]

    return active_candidates.reset_index(drop=True)


def load_liquidity(
    start_date: str | None = None,
    end_date: str | None = None,
    k: int = DEFAULT_K,
    m: int = DEFAULT_M,
    eps: float = DEFAULT_EPSILON,
    warmup_months: int | None = None,
    include_types: Sequence[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Nạp Liquidity Pools và Sweeps chuẩn SMC với warm-up tự động.

    Tương tự như load_structure và load_fvg:
    - Nếu warmup_months is None: nạp từ đầu cơ sở dữ liệu lịch sử.
    - Nếu warmup_months có giá trị: tính lùi start_date_with_warmup = start_date - warmup_months.
    - Bảo toàn mọi pool còn đang ACTIVE tại start_date (hoặc có sự kiện kết thúc tại t_term >= start_date).
    """
    if warmup_months is None:
        load_start = None
    elif start_date is None:
        load_start = None
    else:
        load_start = (pd.to_datetime(start_date) - pd.DateOffset(months=warmup_months)).strftime("%Y-%m-%d")

    df = load_h1(start_date=load_start, end_date=end_date)
    pools_df, sweeps_df = detect_liquidity_pools(
        df,
        k=k,
        m=m,
        eps=eps,
        include_types=include_types,
    )

    if start_date is not None:
        t_start = pd.to_datetime(start_date)
        if t_start.tz is not None:
            t_start = t_start.tz_convert("UTC").tz_localize(None)

        term_map = {}
        if not sweeps_df.empty:
            for _, sw in sweeps_df.iterrows():
                term_map[sw["pool_id"]] = pd.to_datetime(sw["time_available"])

        keep_pool_ids = set()
        for _, p in pools_df.iterrows():
            pid = p["pool_id"]
            p_avail = pd.to_datetime(p["time_available"])
            if p_avail >= t_start:
                keep_pool_ids.add(pid)
            else:
                # Nếu pool sinh trước start_date nhưng còn active hoặc kết thúc tại t_term >= t_start
                t_term = term_map.get(pid)
                if t_term is None or t_term >= t_start:
                    keep_pool_ids.add(pid)

        pools_df = pools_df[pools_df["pool_id"].isin(keep_pool_ids)].reset_index(drop=True)
        if not sweeps_df.empty:
            sweeps_df = sweeps_df[sweeps_df["pool_id"].isin(keep_pool_ids)].reset_index(drop=True)
            sweeps_df["lifecycle_id"] = range(len(sweeps_df))

    return pools_df, sweeps_df
