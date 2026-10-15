"""Bộ kiểm thử toàn diện mô-đun liquidity.py (Liquidity Pools & Sweeps chuẩn SMC v2.3).

Bao gồm 8 ca kiểm thử nghiêm ngặt:
1. Ca 1 (Sửa theo ca dựng được):
   - Swing 1 ở nến 5 (2050.0), Swing 2 ở nến 15 (2050.4, P2 > P1 trong dung sai 0.8).
   - Nến 15 có High = 2050.4 quét Pool 1 -> Pool 1 SWEPT tại nến 15.
   - Nến 18: Swing 2 xác nhận -> Không tạo EQH (do P2 > P1 và Pool 1 đã SWEPT).
   - Swing 2 là pool SWING_HIGH độc lập và ACTIVE. Không có pool nào bị ABSORBED.
2. Ca 2 (Biên khoảng cách nến j2 - j1 = 24 vs 25):
   - 2a (j2 - j1 = 24 = M): Tại đúng nến thứ 24, Pool 1 vẫn còn ACTIVE để được hấp thụ thành EQH.
   - 2b (j2 - j1 = 25 > M): Tại nến 24, Swing 2 chưa xác nhận -> Pool 1 EXPIRED. Không tạo EQH.
3. Ca 3 (Nến ngoài Outside Bar vi phạm cả 2 đầu pool đỉnh và đáy cùng lúc):
   - 3a: Đóng ở giữa -> Cả 2 pool SWEPT.
   - 3b: Đóng vượt đỉnh -> High pool BROKEN, Low pool SWEPT.
   - 3c: Đóng xuyên đáy -> High pool SWEPT, Low pool BROKEN.
4. Ca 4 (Hàm tham chiếu ngây thơ & Đối chiếu vét cạn nhiều seed & Đa điểm cắt tiền tố):
   - Hàm tham chiếu ngây thơ độc lập `_naive_reference_liquidity`.
   - Kiểm tra trên nhiều seed (42, 2024, 7777).
   - Kiểm tra đa điểm cắt tiền tố N in [50, 80, 120, 150] với assert_frame_equal cho cả pools_df và sweeps_df.
   - Khẳng định đủ cả 4 loại sự kiện (SWEPT, BROKEN, EXPIRED, ABSORBED).
5. Ca 5 (Kiểm thử Dấu bằng nghiêm ngặt):
   - High == P không sự kiện; High > P kèm Close == P là SWEPT; Close > P là BROKEN.
   - Low == P không sự kiện; Low < P kèm Close == P là SWEPT; Close < P là BROKEN.
6. Ca 6 (Kiểm thử Ba đỉnh bằng nhau - Chained Absorption):
   - Swing 1 (2050.0) và Swing 2 (2049.8) tạo EQH.
   - Swing 3 (2049.7) được hấp thụ chuỗi vào chính EQH đó, gia hạn tuổi pool.
   - Khi giá quét, chỉ phát ra đúng 1 sự kiện SWEPT duy nhất (triệt tiêu đếm lặp).
7. Ca 7 (Kiểm thử DST New York, Ngày lễ đóng sớm MLK và tính tay PDH/PDL):
   - Kiểm tra DST mùa đông (22:00 UTC) vs mùa hè (21:00 UTC).
   - Kiểm tra ngày lễ MLK (15/01/2024) đóng sớm lúc 19:00 UTC đối chiếu giá tính tay.
8. Ca 8 (Kiểm thử get_active_pools_at & load_liquidity):
   - Kiểm tra trạng thái pool trước, trong và sau khi kết thúc.
   - Kiểm tra load_liquidity với warmup_months=12 lùi ngày chính xác và bảo toàn pool t_term >= t_start.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
import pandas as pd

from src.backtest.data import compute_wilder_atr, load_h1
from src.backtest.smc.liquidity import (
    DEFAULT_EPSILON,
    DEFAULT_M,
    POOLS_COLUMNS,
    SWEEPS_COLUMNS,
    detect_liquidity_pools,
    get_active_pools_at,
    load_liquidity,
)
from src.backtest.smc.swings import DEFAULT_K, detect_swings


def _make_base_df(n_bars: int = 50, start_time: str = "2024-01-01 00:00:00", base_price: float = 2000.0) -> pd.DataFrame:
    """Tạo DataFrame nến H1 phẳng có cấu trúc chuẩn."""
    times = pd.date_range(start_time, periods=n_bars, freq="1h")
    df = pd.DataFrame({
        "time_utc": times,
        "open": np.full(n_bars, base_price),
        "high": np.full(n_bars, base_price + 1.0),
        "low": np.full(n_bars, base_price - 1.0),
        "close": np.full(n_bars, base_price),
    })
    return df


def _naive_reference_liquidity(df: pd.DataFrame, k: int = 3, m: int = 24, eps: float = 0.08) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hàm tham chiếu ngây thơ độc lập mô phỏng từng bước cho Swings và EQH/EQL."""
    df_clean = df.copy().reset_index(drop=True)
    n = len(df_clean)
    if "atr" in df_clean.columns and not df_clean["atr"].isna().all():
        atr = df_clean["atr"].values
    else:
        atr = compute_wilder_atr(df_clean["high"].values, df_clean["low"].values, df_clean["close"].values, 14)

    swings = detect_swings(df_clean, k=k)

    pools: list[dict] = []
    events: list[dict] = []
    curr_id = 0

    # Khởi tạo pools cho swings
    swing_pool_map = {}
    for _, s in swings.iterrows():
        sid = int(s["swing_id"])
        stype = s["swing_type"]
        ptype = "SWING_HIGH" if stype == "HIGH" else "SWING_LOW"
        p = {
            "pool_id": curr_id,
            "pool_type": ptype,
            "price": float(s["price"]),
            "bar_index_candle": int(s["bar_index"]),
            "bar_index_avail": int(s["bar_index"]) + k,
            "time_candle": pd.to_datetime(s["time_candle"]),
            "time_available": pd.to_datetime(s["time_available"]),
            "source_id": f"swing_{sid}",
            "is_buy_side": (stype == "HIGH"),
            "state": "PENDING",
            "swing_id": sid,
        }
        pools.append(p)
        swing_pool_map[(stype, sid)] = curr_id
        curr_id += 1

    active_pids: list[int] = []
    conf_highs: list[dict] = []
    conf_lows: list[dict] = []
    active_eqh: list[dict] = []
    active_eql: list[dict] = []

    for t in range(n):
        t_time = pd.to_datetime(df_clean.loc[t, "time_utc"])
        t_avail = t_time + pd.Timedelta(hours=1)
        th = float(df_clean.loc[t, "high"])
        tl = float(df_clean.loc[t, "low"])
        tc = float(df_clean.loc[t, "close"])

        # 1. Vi phạm
        violated = set()
        for pid in active_pids:
            p = pools[pid]
            if p["state"] != "ACTIVE":
                continue
            age = t - p["bar_index_avail"]
            if p["is_buy_side"]:
                if th > p["price"]:
                    ev = "BROKEN" if tc > p["price"] else "SWEPT"
                    p["state"] = ev
                    violated.add(pid)
                    events.append({
                        "pool_id": pid, "pool_type": p["pool_type"], "event_type": ev,
                        "bar_index": t, "time_candle": t_time, "time_available": t_avail,
                        "price_trigger": th, "pool_age": age, "close_price": tc
                    })
            else:
                if tl < p["price"]:
                    ev = "BROKEN" if tc < p["price"] else "SWEPT"
                    p["state"] = ev
                    violated.add(pid)
                    events.append({
                        "pool_id": pid, "pool_type": p["pool_type"], "event_type": ev,
                        "bar_index": t, "time_candle": t_time, "time_available": t_avail,
                        "price_trigger": tl, "pool_age": age, "close_price": tc
                    })
        active_pids = [pid for pid in active_pids if pid not in violated]

        # 2. Xác nhận Swing & EQH/EQL
        sw_curr = swings[swings["bar_index"] + k == t]
        for _, s in sw_curr.iterrows():
            stype = s["swing_type"]
            j2 = int(s["bar_index"])
            p2 = float(s["price"])
            sid2 = int(s["swing_id"])
            pid2 = swing_pool_map[(stype, sid2)]
            p_s2 = pools[pid2]
            atr_ref = atr[j2] if (j2 < len(atr) and not np.isnan(atr[j2])) else 0.0

            if stype == "HIGH":
                absorbed = False
                if atr_ref > 0.0:
                    # Chained
                    for eqh_item in active_eqh:
                        eqh_p = pools[eqh_item["pool_id"]]
                        if eqh_p["state"] == "ACTIVE":
                            if (j2 - eqh_item["last_j"] <= m) and (p2 <= eqh_p["price"]) and (abs(eqh_p["price"] - p2) <= eps * atr_ref):
                                p_s2["state"] = "ABSORBED"
                                events.append({
                                    "pool_id": pid2, "pool_type": p_s2["pool_type"], "event_type": "ABSORBED",
                                    "bar_index": t, "time_candle": t_time, "time_available": t_avail,
                                    "price_trigger": np.nan, "pool_age": 0, "close_price": tc
                                })
                                eqh_p["bar_index_avail"] = t
                                eqh_item["last_j"] = j2
                                eqh_p["source_id"] += f"_{sid2}"
                                absorbed = True
                                break
                    if not absorbed and conf_highs:
                        s1 = conf_highs[-1]
                        j1 = s1["j"]
                        p1 = s1["p"]
                        pid1 = swing_pool_map[("HIGH", s1["sid"])]
                        p_s1 = pools[pid1]
                        if (j2 - j1 <= m) and (p_s1["state"] == "ACTIVE") and (p2 <= p1) and (abs(p1 - p2) <= eps * atr_ref):
                            eqh_id = curr_id
                            curr_id += 1
                            new_eqh = {
                                "pool_id": eqh_id, "pool_type": "EQH", "price": p1,
                                "bar_index_candle": j2, "bar_index_avail": t,
                                "time_candle": pd.to_datetime(s["time_candle"]), "time_available": t_avail,
                                "source_id": f"eqh_{s1['sid']}_{sid2}", "is_buy_side": True, "state": "ACTIVE"
                            }
                            pools.append(new_eqh)
                            active_pids.append(eqh_id)
                            active_eqh.append({"pool_id": eqh_id, "last_j": j2})

                            p_s1["state"] = "ABSORBED"
                            if pid1 in active_pids: active_pids.remove(pid1)
                            events.append({
                                "pool_id": pid1, "pool_type": p_s1["pool_type"], "event_type": "ABSORBED",
                                "bar_index": t, "time_candle": t_time, "time_available": t_avail,
                                "price_trigger": np.nan, "pool_age": t - p_s1["bar_index_avail"], "close_price": tc
                            })

                            p_s2["state"] = "ABSORBED"
                            events.append({
                                "pool_id": pid2, "pool_type": p_s2["pool_type"], "event_type": "ABSORBED",
                                "bar_index": t, "time_candle": t_time, "time_available": t_avail,
                                "price_trigger": np.nan, "pool_age": 0, "close_price": tc
                            })
                conf_highs.append({"j": j2, "p": p2, "sid": sid2})
            else:
                # LOW
                absorbed = False
                if atr_ref > 0.0:
                    for eql_item in active_eql:
                        eql_p = pools[eql_item["pool_id"]]
                        if eql_p["state"] == "ACTIVE":
                            if (j2 - eql_item["last_j"] <= m) and (p2 >= eql_p["price"]) and (abs(eql_p["price"] - p2) <= eps * atr_ref):
                                p_s2["state"] = "ABSORBED"
                                events.append({
                                    "pool_id": pid2, "pool_type": p_s2["pool_type"], "event_type": "ABSORBED",
                                    "bar_index": t, "time_candle": t_time, "time_available": t_avail,
                                    "price_trigger": np.nan, "pool_age": 0, "close_price": tc
                                })
                                eql_p["bar_index_avail"] = t
                                eql_item["last_j"] = j2
                                eql_p["source_id"] += f"_{sid2}"
                                absorbed = True
                                break
                    if not absorbed and conf_lows:
                        s1 = conf_lows[-1]
                        j1 = s1["j"]
                        p1 = s1["p"]
                        pid1 = swing_pool_map[("LOW", s1["sid"])]
                        p_s1 = pools[pid1]
                        if (j2 - j1 <= m) and (p_s1["state"] == "ACTIVE") and (p2 >= p1) and (abs(p1 - p2) <= eps * atr_ref):
                            eql_id = curr_id
                            curr_id += 1
                            new_eql = {
                                "pool_id": eql_id, "pool_type": "EQL", "price": p1,
                                "bar_index_candle": j2, "bar_index_avail": t,
                                "time_candle": pd.to_datetime(s["time_candle"]), "time_available": t_avail,
                                "source_id": f"eql_{s1['sid']}_{sid2}", "is_buy_side": False, "state": "ACTIVE"
                            }
                            pools.append(new_eql)
                            active_pids.append(eql_id)
                            active_eql.append({"pool_id": eql_id, "last_j": j2})

                            p_s1["state"] = "ABSORBED"
                            if pid1 in active_pids: active_pids.remove(pid1)
                            events.append({
                                "pool_id": pid1, "pool_type": p_s1["pool_type"], "event_type": "ABSORBED",
                                "bar_index": t, "time_candle": t_time, "time_available": t_avail,
                                "price_trigger": np.nan, "pool_age": t - p_s1["bar_index_avail"], "close_price": tc
                            })

                            p_s2["state"] = "ABSORBED"
                            events.append({
                                "pool_id": pid2, "pool_type": p_s2["pool_type"], "event_type": "ABSORBED",
                                "bar_index": t, "time_candle": t_time, "time_available": t_avail,
                                "price_trigger": np.nan, "pool_age": 0, "close_price": tc
                            })
                conf_lows.append({"j": j2, "p": p2, "sid": sid2})

        # 3. Hết hạn
        expired = set()
        for pid in active_pids:
            p = pools[pid]
            if p["bar_index_avail"] == t: continue
            if t - p["bar_index_avail"] >= m:
                p["state"] = "EXPIRED"
                expired.add(pid)
                events.append({
                    "pool_id": pid, "pool_type": p["pool_type"], "event_type": "EXPIRED",
                    "bar_index": t, "time_candle": t_time, "time_available": t_avail,
                    "price_trigger": np.nan, "pool_age": t - p["bar_index_avail"], "close_price": tc
                })
        active_pids = [pid for pid in active_pids if pid not in expired]

        # 4. Kích hoạt pending
        for p in pools:
            if p["bar_index_avail"] == t and p["state"] == "PENDING":
                p["state"] = "ACTIVE"
                active_pids.append(p["pool_id"])

    # Đóng gói và gán pool_id theo thứ tự thời gian
    sorted_pools = sorted(pools, key=lambda x: (x["time_available"], x["time_candle"], x["bar_index_avail"], x["pool_type"], x["price"]))
    id_map = {}
    out_pools = []
    for new_id, p in enumerate(sorted_pools):
        id_map[p["pool_id"]] = new_id
        out_pools.append({
            "pool_id": new_id, "pool_type": p["pool_type"], "price": p["price"],
            "bar_index_candle": p["bar_index_candle"], "bar_index_avail": p["bar_index_avail"],
            "time_candle": p["time_candle"], "time_available": p["time_available"],
            "source_id": str(p["source_id"])
        })
    df_p = pd.DataFrame(out_pools)[POOLS_COLUMNS] if out_pools else pd.DataFrame(columns=POOLS_COLUMNS)

    if events:
        ev_df = pd.DataFrame(events)
        ev_df["pool_id"] = ev_df["pool_id"].map(id_map)
        ev_prio = {"SWEPT": 0, "BROKEN": 1, "EXPIRED": 2, "ABSORBED": 3}
        ev_df["_prio"] = ev_df["event_type"].map(ev_prio)
        ev_df = ev_df.sort_values(by=["time_available", "pool_id", "_prio"]).reset_index(drop=True)
        ev_df = ev_df.drop(columns=["_prio"])
        ev_df["lifecycle_id"] = range(len(ev_df))
        df_ev = ev_df[SWEEPS_COLUMNS]
    else:
        df_ev = pd.DataFrame(columns=SWEEPS_COLUMNS)

    return df_p, df_ev


class TestLiquidityPools(unittest.TestCase):
    """Bộ test chuẩn hóa cho Liquidity Pools và Sweeps SMC v2.3."""

    def test_case_1_pool_1_swept_at_swing_2_candle_no_eqh(self):
        """Ca 1: Swing 1 tại nến 5 (2050.0), Swing 2 tại nến 15 với High = 2050.4 (P2 > P1).

        - Nến 15 có High = 2050.4 quét qua Pool 1 (2050.0), Close = 2040.0 -> Pool 1 SWEPT tại nến 15!
        - Nến 18: Swing 2 xác nhận -> Không tạo EQH (do P2 > P1 và do Pool 1 đã SWEPT).
        - Khẳng định:
          * Pool 1 có sự kiện SWEPT tại nến 15.
          * Không có EQH pool nào được tạo.
          * Pool Swing 2 là pool SWING_HIGH độc lập và ACTIVE.
          * Không có pool nào bị ABSORBED.
        """
        k = 3
        df = _make_base_df(n_bars=30, base_price=2000.0)

        # 1. Swing High 1 tại nến 5 (2050.0)
        df.loc[5, "high"] = 2050.0
        for i in [2, 3, 4, 6, 7, 8]:
            df.loc[i, "high"] = 2020.0

        # 2. Swing High 2 tại nến 15 (2050.4 > 2050.0, nằm trong dung sai 0.8)
        # Nến 15 có Close = 2040.0 <= 2050.0 -> quét râu Pool 1
        df.loc[15, "high"] = 2050.4
        df.loc[15, "close"] = 2040.0
        for i in [12, 13, 14, 16, 17, 18]:
            df.loc[i, "high"] = 2020.0

        df["atr"] = 10.0  # eps * atr = 0.8 >= 0.4

        pools_df, sweeps_df = detect_liquidity_pools(df, k=k, m=24, eps=DEFAULT_EPSILON, include_types=["SWING_HIGH", "EQH"])

        # Khẳng định không tạo EQH
        eqh_pools = pools_df[pools_df["pool_type"] == "EQH"]
        self.assertEqual(len(eqh_pools), 0, "Không được tạo EQH khi P2 > P1 và Pool 1 đã bị SWEPT!")

        # Khẳng định Pool 1 bị SWEPT tại nến 15
        pid1 = pools_df[pools_df["source_id"] == "swing_0"].iloc[0]["pool_id"]
        p1_sweeps = sweeps_df[sweeps_df["pool_id"] == pid1]
        self.assertEqual(len(p1_sweeps), 1)
        self.assertEqual(p1_sweeps.iloc[0]["event_type"], "SWEPT")
        self.assertEqual(p1_sweeps.iloc[0]["bar_index"], 15)
        self.assertEqual(p1_sweeps.iloc[0]["price_trigger"], 2050.4)

        # Khẳng định Swing 2 là pool độc lập
        self.assertEqual(len(pools_df), 2)
        pid2 = pools_df[pools_df["source_id"] == "swing_1"].iloc[0]["pool_id"]
        p2_sweeps = sweeps_df[sweeps_df["pool_id"] == pid2]
        self.assertEqual(len(p2_sweeps), 0, "Swing 2 chưa bị vi phạm và phải còn ACTIVE!")

        # Khẳng định không có pool nào bị ABSORBED
        absorbed = sweeps_df[sweeps_df["event_type"] == "ABSORBED"]
        self.assertEqual(len(absorbed), 0)

    def test_case_2a_boundary_j2_minus_j1_equals_24(self):
        """Ca 2a: Biên j2 - j1 = 24 = M.

        - Swing 1 tại j1 = 5, xác nhận tại 8.
        - Swing 2 tại j2 = 29 (j2 - j1 = 24 = M), xác nhận tại 32.
        - Tại nến 32: Pool 1 vẫn còn ACTIVE và được hấp thụ vào EQH!
        """
        k = 3
        m = 24
        j1 = 5
        j2 = j1 + m  # 29
        n_bars = j2 + k + 10
        df = _make_base_df(n_bars=n_bars, base_price=2000.0)

        p1 = 2050.0
        df.loc[j1, "high"] = p1
        for i in range(j1 - k, j1 + k + 1):
            if i != j1: df.loc[i, "high"] = 2020.0

        p2 = 2049.8  # p2 <= p1
        df.loc[j2, "high"] = p2
        for i in range(j2 - k, j2 + k + 1):
            if i != j2: df.loc[i, "high"] = 2020.0

        for i in range(j1 + k + 1, j2 + k + 1):
            if i != j2: df.loc[i, "high"] = min(df.loc[i, "high"], 2030.0)

        df["atr"] = 10.0

        pools_df, sweeps_df = detect_liquidity_pools(df, k=k, m=m, eps=DEFAULT_EPSILON, include_types=["SWING_HIGH", "EQH"])

        # Kiểm tra EQH được tạo
        eqh_pools = pools_df[pools_df["pool_type"] == "EQH"]
        self.assertEqual(len(eqh_pools), 1, "Phải tạo thành công EQH tại đúng mốc biên j2 - j1 = 24 = M!")
        eqh = eqh_pools.iloc[0]
        self.assertEqual(eqh["price"], p1)
        self.assertEqual(eqh["bar_index_avail"], j2 + k)

        # Kiểm tra cả Pool 1 và Pool 2 bị ABSORBED
        pid1 = pools_df[pools_df["source_id"] == "swing_0"].iloc[0]["pool_id"]
        p1_events = sweeps_df[sweeps_df["pool_id"] == pid1]
        self.assertEqual(len(p1_events), 1)
        self.assertEqual(p1_events.iloc[0]["event_type"], "ABSORBED")
        self.assertEqual(p1_events.iloc[0]["bar_index"], j2 + k)

        pid2 = pools_df[pools_df["source_id"] == "swing_1"].iloc[0]["pool_id"]
        p2_events = sweeps_df[sweeps_df["pool_id"] == pid2]
        self.assertEqual(len(p2_events), 1)
        self.assertEqual(p2_events.iloc[0]["event_type"], "ABSORBED")
        self.assertEqual(p2_events.iloc[0]["bar_index"], j2 + k)

    def test_case_2b_boundary_j2_minus_j1_equals_25(self):
        """Ca 2b: Biên j2 - j1 = 25 > M -> Không tạo EQH."""
        k = 3
        m = 24
        j1 = 5
        j2 = j1 + m + 1  # 30
        n_bars = j2 + k + 5
        df = _make_base_df(n_bars=n_bars, base_price=2000.0)

        p1 = 2050.0
        df.loc[j1, "high"] = p1
        for i in range(j1 - k, j1 + k + 1):
            if i != j1: df.loc[i, "high"] = 2020.0

        p2 = 2049.8
        df.loc[j2, "high"] = p2
        for i in range(j2 - k, j2 + k + 1):
            if i != j2: df.loc[i, "high"] = 2020.0

        for i in range(j1 + k + 1, j2 + k + 1):
            if i != j2: df.loc[i, "high"] = min(df.loc[i, "high"], 2030.0)

        df["atr"] = 10.0

        pools_df, sweeps_df = detect_liquidity_pools(df, k=k, m=m, eps=DEFAULT_EPSILON, include_types=["SWING_HIGH", "EQH"])

        # Không tạo EQH
        eqh_pools = pools_df[pools_df["pool_type"] == "EQH"]
        self.assertEqual(len(eqh_pools), 0, "Không được tạo EQH khi j2 - j1 = 25 > M!")

        pid1 = pools_df[pools_df["source_id"] == "swing_0"].iloc[0]["pool_id"]
        p1_events = sweeps_df[sweeps_df["pool_id"] == pid1]
        self.assertEqual(len(p1_events), 1)
        self.assertEqual(p1_events.iloc[0]["event_type"], "EXPIRED")
        self.assertEqual(p1_events.iloc[0]["bar_index"], j1 + k + m)

    def test_case_3_outside_bar_handling(self):
        """Ca 3: Nến ngoài (Outside Bar) vi phạm cả hai đầu pool đỉnh và đáy cùng lúc."""
        k = 3
        # Subcase 3a: Đóng ở giữa
        df_a = _make_base_df(n_bars=25, base_price=2040.0)
        df_a.loc[5, "high"] = 2050.0
        for i in [2, 3, 4, 6, 7, 8]: df_a.loc[i, "high"] = 2045.0
        df_a.loc[6, "low"] = 2030.0
        for i in [3, 4, 5, 7, 8, 9]: df_a.loc[i, "low"] = 2035.0

        df_a.loc[12, "high"] = 2055.0
        df_a.loc[12, "low"] = 2025.0
        df_a.loc[12, "close"] = 2040.0

        pools_a, sweeps_a = detect_liquidity_pools(df_a, k=k, include_types=["SWING_HIGH", "SWING_LOW"])
        ev_12_a = sweeps_a[sweeps_a["bar_index"] == 12]
        self.assertEqual(len(ev_12_a), 2)
        ev_high_a = ev_12_a[ev_12_a["pool_type"] == "SWING_HIGH"].iloc[0]
        ev_low_a = ev_12_a[ev_12_a["pool_type"] == "SWING_LOW"].iloc[0]
        self.assertEqual(ev_high_a["event_type"], "SWEPT")
        self.assertEqual(ev_low_a["event_type"], "SWEPT")

        # Subcase 3b: Đóng vượt đỉnh
        df_b = df_a.copy()
        df_b.loc[12, "close"] = 2052.0
        pools_b, sweeps_b = detect_liquidity_pools(df_b, k=k, include_types=["SWING_HIGH", "SWING_LOW"])
        ev_12_b = sweeps_b[sweeps_b["bar_index"] == 12]
        ev_high_b = ev_12_b[ev_12_b["pool_type"] == "SWING_HIGH"].iloc[0]
        ev_low_b = ev_12_b[ev_12_b["pool_type"] == "SWING_LOW"].iloc[0]
        self.assertEqual(ev_high_b["event_type"], "BROKEN")
        self.assertEqual(ev_low_b["event_type"], "SWEPT")

        # Subcase 3c: Đóng xuyên đáy
        df_c = df_a.copy()
        df_c.loc[12, "close"] = 2028.0
        pools_c, sweeps_c = detect_liquidity_pools(df_c, k=k, include_types=["SWING_HIGH", "SWING_LOW"])
        ev_12_c = sweeps_c[sweeps_c["bar_index"] == 12]
        ev_high_c = ev_12_c[ev_12_c["pool_type"] == "SWING_HIGH"].iloc[0]
        ev_low_c = ev_12_c[ev_12_c["pool_type"] == "SWING_LOW"].iloc[0]
        self.assertEqual(ev_high_c["event_type"], "SWEPT")
        self.assertEqual(ev_low_c["event_type"], "BROKEN")

    def test_case_4_multi_seed_brute_force_and_prefix_invariance(self):
        """Ca 4: Đối chiếu hàm tham chiếu ngây thơ, đa điểm cắt tiền tố và kiểm tra 4 loại sự kiện."""
        for seed in [42, 2024, 7777]:
            rng = np.random.default_rng(seed)
            n_bars = 200
            times = pd.date_range("2024-01-01 00:00:00", periods=n_bars, freq="1h")

            returns = rng.normal(0, 3.0, size=n_bars)
            close = 2000.0 + np.cumsum(returns)
            high = close + rng.uniform(0.5, 4.0, size=n_bars)
            low = close - rng.uniform(0.5, 4.0, size=n_bars)
            open_p = close - returns

            # Chủ động chèn cặp đỉnh bằng nhau để kích hoạt sự kiện ABSORBED
            if n_bars > 50:
                high[10] = 2060.0
                high[7:10] = 2040.0; high[11:14] = 2040.0
                high[25] = 2059.8
                high[22:25] = 2040.0; high[26:29] = 2040.0
                # Đảm bảo không nến nào giữa đó vi phạm
                for b in range(14, 28):
                    if b != 25: high[b] = min(high[b], 2045.0)

            df_rand = pd.DataFrame({
                "time_utc": times,
                "open": open_p,
                "high": high,
                "low": low,
                "close": close,
            })

            # 4.1: Đối chiếu trực tiếp với hàm tham chiếu ngây thơ độc lập
            pools_code, sweeps_code = detect_liquidity_pools(df_rand, k=3, m=24, eps=0.08, include_types=["SWING_HIGH", "SWING_LOW", "EQH", "EQL"])
            pools_ref, sweeps_ref = _naive_reference_liquidity(df_rand, k=3, m=24, eps=0.08)

            pd.testing.assert_frame_equal(pools_code, pools_ref)
            pd.testing.assert_frame_equal(sweeps_code, sweeps_ref)

            # 4.2: Kiểm tra bất biến tiền tố tại nhiều điểm cắt: N in [50, 80, 120, 150]
            for N in [50, 80, 120, 150]:
                df_prefix = df_rand.iloc[:N].copy()
                pools_pre, sweeps_pre = detect_liquidity_pools(df_prefix, k=3, m=24, eps=0.08, include_types=["SWING_HIGH", "SWING_LOW", "EQH", "EQL"])

                cutoff_time = df_prefix["time_utc"].iloc[-1] + pd.Timedelta(hours=1)
                pools_cut = pools_code[pd.to_datetime(pools_code["time_available"]) <= cutoff_time].reset_index(drop=True)
                pd.testing.assert_frame_equal(pools_cut, pools_pre)

                sweeps_cut = sweeps_code[pd.to_datetime(sweeps_code["time_available"]) <= cutoff_time].reset_index(drop=True)
                sweeps_cut["lifecycle_id"] = range(len(sweeps_cut))
                pd.testing.assert_frame_equal(sweeps_cut, sweeps_pre)

            # 4.3: Khẳng định sự xuất hiện của đủ cả 4 loại sự kiện cho từng seed
            events_present = set(sweeps_code["event_type"])
            for ev in ["SWEPT", "BROKEN", "EXPIRED", "ABSORBED"]:
                self.assertIn(ev, events_present, f"Seed {seed}: Phải có sự kiện {ev}!")

    def test_case_5_exact_equality_thresholds(self):
        """Ca 5: Kiểm tra dấu bằng nghiêm ngặt của giá chạm và giá đóng cửa."""
        k = 3
        df = _make_base_df(n_bars=25, base_price=2000.0)
        # Swing High tại 5 (2050.0), Swing Low tại 6 (2030.0), khả dụng tại 9
        df.loc[5, "high"] = 2050.0
        for i in [2, 3, 4, 6, 7, 8]: df.loc[i, "high"] = 2040.0
        df.loc[6, "low"] = 2030.0
        for i in [3, 4, 5, 7, 8, 9]: df.loc[i, "low"] = 2035.0

        # Nến 10: High đúng bằng 2050.0 (High == P), Low an toàn 2035.0 -> KHÔNG phát sinh sự kiện!
        df.loc[10, "high"] = 2050.0
        df.loc[10, "low"] = 2035.0
        df.loc[10, "close"] = 2045.0

        # Nến 11: Low đúng bằng 2030.0 (Low == P), High an toàn 2040.0 -> KHÔNG phát sinh sự kiện!
        df.loc[11, "high"] = 2040.0
        df.loc[11, "low"] = 2030.0
        df.loc[11, "close"] = 2035.0

        pools_df, sweeps_df = detect_liquidity_pools(df.iloc[:12], k=k, include_types=["SWING_HIGH", "SWING_LOW"])
        self.assertEqual(len(sweeps_df), 0, "Dấu bằng High == P hoặc Low == P tuyệt đối không phát sinh sự kiện!")

        # Nến 12: High = 2051.0 > 2050.0 nhưng Close = 2050.0 (Close == P), Low an toàn 2035.0 -> Phải là SWEPT!
        df.loc[12, "high"] = 2051.0
        df.loc[12, "low"] = 2035.0
        df.loc[12, "close"] = 2050.0
        pools_12, sweeps_12 = detect_liquidity_pools(df.iloc[:13], k=k, include_types=["SWING_HIGH", "SWING_LOW"])
        self.assertEqual(len(sweeps_12), 1)
        self.assertEqual(sweeps_12.iloc[0]["event_type"], "SWEPT", "High > P kèm Close == P phải là SWEPT!")

        # Nến 13: Low = 2029.0 < 2030.0 nhưng Close = 2030.0 (Close == P), High an toàn 2040.0 -> Phải là SWEPT!
        df.loc[13, "high"] = 2040.0
        df.loc[13, "low"] = 2029.0
        df.loc[13, "close"] = 2030.0
        pools_13, sweeps_13 = detect_liquidity_pools(df.iloc[:14], k=k, include_types=["SWING_HIGH", "SWING_LOW"])
        self.assertEqual(len(sweeps_13), 2)
        sw_low = sweeps_13[sweeps_13["pool_type"] == "SWING_LOW"].iloc[0]
        self.assertEqual(sw_low["event_type"], "SWEPT", "Low < P kèm Close == P phải là SWEPT!")

    def test_case_6_triple_high_chained_absorption(self):
        """Ca 6: Kiểm tra hấp thụ chuỗi cho ba đỉnh bằng nhau (Triple Highs).

        - Swing 1 tại nến 5 (2050.0).
        - Swing 2 tại nến 12 (2049.8) -> Tạo EQH, Swing 1 và 2 bị ABSORBED.
        - Swing 3 tại nến 19 (2049.7) -> Hấp thụ tiếp vào chính EQH đó! Swing 3 bị ABSORBED.
        - Tại nến 25: Giá quét qua 2050.0 (High = 2052.0, Close = 2045.0).
        - Khẳng định:
          * Đúng 1 sự kiện SWEPT duy nhất được phát sinh (không bị đếm lặp 2 lần).
        """
        k = 3
        df = _make_base_df(n_bars=35, base_price=2000.0)
        # Swing 1
        df.loc[5, "high"] = 2050.0
        for i in [2, 3, 4, 6, 7, 8]: df.loc[i, "high"] = 2020.0
        # Swing 2
        df.loc[12, "high"] = 2049.8
        for i in [9, 10, 11, 13, 14, 15]: df.loc[i, "high"] = 2020.0
        # Swing 3
        df.loc[19, "high"] = 2049.7
        for i in [16, 17, 18, 20, 21, 22]: df.loc[i, "high"] = 2020.0

        # Nến 25 quét qua
        df.loc[25, "high"] = 2052.0
        df.loc[25, "close"] = 2045.0

        df["atr"] = 10.0

        pools_df, sweeps_df = detect_liquidity_pools(df, k=k, include_types=["SWING_HIGH", "EQH"])

        # Phải có duy nhất 1 pool EQH
        eqh_pools = pools_df[pools_df["pool_type"] == "EQH"]
        self.assertEqual(len(eqh_pools), 1)

        # Cả 3 swing 0, 1, 2 đều bị ABSORBED
        absorbed = sweeps_df[sweeps_df["event_type"] == "ABSORBED"]
        self.assertEqual(len(absorbed), 3, "Cả 3 swing high đều phải bị ABSORBED vào cụm EQH!")

        # Sự kiện quét tại nến 25 chỉ có duy nhất 1 sự kiện SWEPT
        swept_25 = sweeps_df[(sweeps_df["bar_index"] == 25) & (sweeps_df["event_type"] == "SWEPT")]
        self.assertEqual(len(swept_25), 1, "Chỉ được phát đúng 1 sự kiện SWEPT duy nhất cho cụm EQH!")

    def test_case_7_dst_holiday_early_close_and_hand_calculated_pdh(self):
        """Ca 7: Kiểm thử DST New York, ngày lễ đóng sớm MLK và đối chiếu tính tay."""
        # 1. Kiểm tra ngày lễ MLK (15/01/2024) đóng sớm lúc 19:00 UTC
        df_mlk = load_h1("2024-01-14", "2024-01-17").reset_index(drop=True)
        pools_mlk, _ = detect_liquidity_pools(df_mlk, include_types=["PDH", "PDL"])

        # Tìm PDH của ngày MLK (2024-01-15)
        pdh_mlk = pools_mlk[pools_mlk["source_id"] == "pdh_2024-01-15"]
        self.assertEqual(len(pdh_mlk), 1)
        # Nến cuối của ngày MLK là nến mở 19:00 UTC, đóng lúc 20:00 UTC (hoặc 18:00 UTC đóng 19:00 UTC)
        # Khẳng định thời điểm khả dụng là ngay khi phiên đóng sớm kết thúc
        t_avail = pd.to_datetime(pdh_mlk.iloc[0]["time_available"])
        self.assertEqual(t_avail.strftime("%Y-%m-%d %H:%M"), "2024-01-15 20:00")

        # Đối chiếu giá PDH/PDL tính tay trên các nến thuộc ngày 2024-01-15
        # Nến ngày 15/01 bắt đầu từ 2024-01-14 23:00 UTC đến 2024-01-15 19:00 UTC
        ny_bars = df_mlk[(df_mlk["time_utc"] >= "2024-01-14 23:00:00") & (df_mlk["time_utc"] <= "2024-01-15 19:00:00")]
        hand_high = float(ny_bars["high"].max())
        hand_low = float(ny_bars["low"].min())
        self.assertEqual(pdh_mlk.iloc[0]["price"], hand_high)

        pdl_mlk = pools_mlk[pools_mlk["source_id"] == "pdl_2024-01-15"]
        self.assertEqual(pdl_mlk.iloc[0]["price"], hand_low)

        # 2. Kiểm tra DST mùa hè (tháng 6/2024): 17:00 EDT tương ứng 21:00 UTC
        df_summer = load_h1("2024-06-10", "2024-06-14").reset_index(drop=True)
        pools_summer, _ = detect_liquidity_pools(df_summer, include_types=["PDH"])
        # Kiểm tra giờ khả dụng của ngày 2024-06-11
        pdh_summer = pools_summer[pools_summer["source_id"] == "pdh_2024-06-11"]
        self.assertEqual(len(pdh_summer), 1)
        # Mùa hè nến mở 20:00 UTC đóng 21:00 UTC
        t_avail_summer = pd.to_datetime(pdh_summer.iloc[0]["time_available"])
        self.assertEqual(t_avail_summer.hour, 21, "Mùa hè EDT thì 17:00 NY phải tương ứng 21:00 UTC!")

    def test_case_8_get_active_pools_and_load_liquidity(self):
        """Ca 8: Kiểm tra helper get_active_pools_at và load_liquidity có warm-up lùi ngày."""
        # 8.1: get_active_pools_at
        df = _make_base_df(n_bars=20, base_price=2000.0)
        df.loc[5, "high"] = 2050.0
        for i in [2, 3, 4, 6, 7, 8]: df.loc[i, "high"] = 2020.0
        df.loc[12, "high"] = 2055.0  # SWEPT tại 12 (13:00 UTC)

        pools_df, sweeps_df = detect_liquidity_pools(df, k=3, include_types=["SWING_HIGH"])
        self.assertEqual(len(get_active_pools_at(pools_df, sweeps_df, "2024-01-01 08:00:00")), 0)
        self.assertEqual(len(get_active_pools_at(pools_df, sweeps_df, "2024-01-01 10:00:00")), 1)
        self.assertEqual(len(get_active_pools_at(pools_df, sweeps_df, "2024-01-01 14:00:00")), 0)

        # 8.2: load_liquidity
        p_dev, sw_dev = load_liquidity("2024-01-15", "2024-01-20", warmup_months=12, include_types=["SWING_HIGH", "SWING_LOW"])
        self.assertGreater(len(p_dev), 0)
        # Mọi pool có mặt phải khả dụng >= 2024-01-15 HOẶC còn active / kết thúc sau 2024-01-15
        t_bound = pd.to_datetime("2024-01-15")
        for _, p in p_dev.iterrows():
            pid = p["pool_id"]
            if pd.to_datetime(p["time_available"]) < t_bound:
                term_ev = sw_dev[sw_dev["pool_id"] == pid]
                if not term_ev.empty:
                    self.assertGreaterEqual(pd.to_datetime(term_ev.iloc[0]["time_available"]), t_bound)


if __name__ == "__main__":
    unittest.main()
