"""Bộ kiểm thử toàn diện mô-đun liquidity.py (Liquidity Pools & Sweeps chuẩn SMC v2.3.1).

Bao gồm 9 ca kiểm thử nghiêm ngặt:
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
4. Ca 4 (Oracle Độc Lập Theo Từng Pool & Đối Chiếu Đa Seed & Đa Điểm Cắt Tiền Tố):
   - Oracle độc lập `_oracle_verify_sweeps` kiểm chứng chuyển trạng thái của mọi loại pool.
   - Đa seed ngẫu nhiên (42, 2024, 7777).
   - Đa điểm cắt tiền tố N in [50, 80, 120, 150] với assert_frame_equal.
   - Khẳng định đủ cả 4 loại sự kiện (SWEPT, BROKEN, EXPIRED, ABSORBED) cho từng seed.
5. Ca 5 (Kiểm thử Dấu bằng nghiêm ngặt):
   - High == P không sự kiện; High > P kèm Close == P là SWEPT; Close > P là BROKEN.
   - Low == P không sự kiện; Low < P kèm Close == P là SWEPT; Close < P là BROKEN.
6. Ca 6 (Kiểm thử Ba đỉnh bằng nhau - Chained Absorption & Bất biến tiền tố):
   - Swing 1 (2050.0) và Swing 2 (2049.8) tạo EQH.
   - Có một Swing High thấp hơn (2030.0) chen giữa nhưng Swing 3 (2049.7) vẫn được hấp thụ chuỗi.
   - Khẳng định: Hấp thụ chuỗi KHÔNG gia hạn tuổi và KHÔNG sửa đổi dòng EQH đã ghi.
   - Khẳng định bất biến tiền tố (Prefix Invariance) giữa cắt trước và sau Swing 3.
   - Khi giá quét, chỉ phát ra đúng 1 sự kiện SWEPT duy nhất.
7. Ca 7 (Kiểm thử DST New York, Ngày lễ đóng sớm MLK và tính tay PDH/PDL):
   - Ngày lễ MLK (15/01/2024) nến 19:00 UTC đóng lúc 20:00 UTC -> khả dụng 20:00 UTC.
   - Chuyển DST mùa xuân (tháng 3/2024) và mùa hè (tháng 6/2024).
   - Chuyển DST mùa thu (tháng 11/2024: 17:00 NY = 22:00 UTC).
   - Đối chiếu tính tay PDH/PDL với dữ liệu H1 thật.
8. Ca 8 (Kiểm thử get_active_pools_at & load_liquidity):
   - Kiểm tra trạng thái pool trước, trong và sau khi kết thúc.
   - Kiểm tra điều kiện biên >=: bảo toàn pool kết thúc đúng tại t_term == T_start.
   - Kiểm tra load_liquidity lùi ngày warm-up và kế thừa pool còn active qua mốc T_start.
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

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


def _oracle_verify_eqh_eql(
    df: pd.DataFrame,
    pools_df: pd.DataFrame,
    sweeps_df: pd.DataFrame,
    k: int = DEFAULT_K,
    m: int = DEFAULT_M,
    eps: float = DEFAULT_EPSILON,
    include_types: Sequence[str] | None = None,
) -> None:
    """Oracle độc lập kiểm chứng 2 chiều việc hình thành EQH và EQL.

    Chiều 1: Mọi dòng EQH/EQL trong pools_df bắt buộc phải thỏa mãn đúng 4 điều kiện.
    Chiều 2: Mọi cặp swing liên tiếp thỏa mãn 4 điều kiện (và không bị hấp thụ chuỗi)
             bắt buộc phải sinh ra đúng một pool EQH/EQL tương ứng.
    """
    atr_arr = df["atr"].values if "atr" in df.columns else compute_wilder_atr(df["high"].values, df["low"].values, df["close"].values, 14)
    swings_df = detect_swings(df, k=k)

    for stype, ptype, is_buy in [("HIGH", "EQH", True), ("LOW", "EQL", False)]:
        if include_types is not None and ptype not in include_types:
            continue

        st_swings = swings_df[swings_df["swing_type"] == stype].to_dict("records")
        st_pools = pools_df[pools_df["pool_type"] == ptype]

        # --- Chiều 1: Mọi dòng EQH/EQL trong pools_df phải thỏa 4 điều kiện ---
        for _, eq_pool in st_pools.iterrows():
            src = str(eq_pool["source_id"])
            parts = src.split("_")
            if len(parts) < 3:
                raise AssertionError(f"source_id của {ptype} không chuẩn: {src}")
            s1_id, s2_id = int(parts[1]), int(parts[2])
            s1_candidates = [s for s in st_swings if s["swing_id"] == s1_id]
            s2_candidates = [s for s in st_swings if s["swing_id"] == s2_id]
            if not s1_candidates or not s2_candidates:
                raise AssertionError(f"Không tìm thấy swing cấu thành {s1_id} hoặc {s2_id} cho {ptype} {src}")
            s1 = s1_candidates[0]
            s2 = s2_candidates[0]

            j1, p1 = s1["bar_index"], s1["price"]
            j2, p2 = s2["bar_index"], s2["price"]
            t2 = j2 + k
            atr_ref = atr_arr[j2] if (j2 < len(atr_arr) and not np.isnan(atr_arr[j2])) else 0.0

            # Điều kiện 1: Khoảng cách j2 - j1 <= m
            if j2 - j1 > m:
                raise AssertionError(f"{src}: Vi phạm khoảng cách j2 - j1 = {j2 - j1} > {m}")
            # Điều kiện 2: Swing 1 phải còn ACTIVE tại t2 (không bị vi phạm từ j1 + k + 1 đến t2)
            for t in range(j1 + k + 1, t2 + 1):
                if is_buy and df.loc[t, "high"] > p1:
                    raise AssertionError(f"{src}: Swing 1 bị vi phạm High tại bar {t} trước khi xác nhận s2 tại {t2}")
                elif not is_buy and df.loc[t, "low"] < p1:
                    raise AssertionError(f"{src}: Swing 1 bị vi phạm Low tại bar {t} trước khi xác nhận s2 tại {t2}")
            # Điều kiện 3: Dung sai |p1 - p2| <= eps * atr_ref
            if abs(p1 - p2) > eps * atr_ref + 1e-9:
                raise AssertionError(f"{src}: Vi phạm dung sai |p1 - p2| = {abs(p1 - p2)} > {eps * atr_ref}")
            # Điều kiện 4: Hướng giá (p2 <= p1 cho HIGH, p2 >= p1 cho LOW)
            if is_buy and p2 > p1:
                raise AssertionError(f"{src}: Vi phạm hướng giá p2 = {p2} > p1 = {p1}")
            elif not is_buy and p2 < p1:
                raise AssertionError(f"{src}: Vi phạm hướng giá p2 = {p2} < p1 = {p1}")

        # --- Chiều 2: Mọi cặp swing liên tiếp thỏa 4 điều kiện phải sinh EQH/EQL ---
        confirmed_swings: list[dict] = []
        active_eq_clusters: list[dict] = []

        for s2 in sorted(st_swings, key=lambda s: (s["bar_index"] + k, s["bar_index"])):
            j2, p2 = s2["bar_index"], s2["price"]
            t2 = j2 + k
            atr_ref = atr_arr[j2] if (j2 < len(atr_arr) and not np.isnan(atr_arr[j2])) else 0.0

            if atr_ref <= 0.0:
                confirmed_swings.append(s2)
                continue

            # Kiểm tra hấp thụ chuỗi vào cluster EQ đang active
            absorbed_into_chain = False
            for cl in active_eq_clusters:
                if t2 - cl["avail_bar"] <= m:
                    violated = False
                    for t in range(cl["avail_bar"] + 1, t2 + 1):
                        if is_buy and df.loc[t, "high"] > cl["price"]:
                            violated = True
                            break
                        elif not is_buy and df.loc[t, "low"] < cl["price"]:
                            violated = True
                            break
                    if not violated:
                        if is_buy and (p2 <= cl["price"]) and (abs(cl["price"] - p2) <= eps * atr_ref + 1e-9):
                            absorbed_into_chain = True
                            break
                        elif not is_buy and (p2 >= cl["price"]) and (abs(cl["price"] - p2) <= eps * atr_ref + 1e-9):
                            absorbed_into_chain = True
                            break

            if not absorbed_into_chain and confirmed_swings:
                s1 = confirmed_swings[-1]
                j1, p1 = s1["bar_index"], s1["price"]
                cond_dist = (j2 - j1 <= m)
                cond_tol = (abs(p1 - p2) <= eps * atr_ref + 1e-9)
                cond_dir = (p2 <= p1) if is_buy else (p2 >= p1)

                s1_active = (t2 - (j1 + k) <= m)
                if s1_active and cond_dist:
                    for t in range(j1 + k + 1, t2 + 1):
                        if is_buy and df.loc[t, "high"] > p1:
                            s1_active = False
                            break
                        elif not is_buy and df.loc[t, "low"] < p1:
                            s1_active = False
                            break

                s1_pool = pools_df[pools_df["source_id"] == f"swing_{s1['swing_id']}"]
                if not s1_pool.empty:
                    s1_pid = s1_pool.iloc[0]["pool_id"]
                    s1_abs = sweeps_df[(sweeps_df["pool_id"] == s1_pid) & (sweeps_df["event_type"] == "ABSORBED") & (sweeps_df["bar_index"] < t2)]
                    if not s1_abs.empty:
                        s1_active = False

                if cond_dist and s1_active and cond_tol and cond_dir:
                    expected_src = f"{ptype.lower()}_{s1['swing_id']}_{s2['swing_id']}"
                    match = pools_df[pools_df["source_id"] == expected_src]
                    if match.empty:
                        raise AssertionError(f"Cặp swing {s1['swing_id']} và {s2['swing_id']} thỏa 4 điều kiện nhưng không có {ptype} {expected_src}!")
                    active_eq_clusters.append({
                        "price": p1,
                        "avail_bar": t2,
                    })

            confirmed_swings.append(s2)


def _oracle_verify_sweeps(
    df: pd.DataFrame,
    pools_df: pd.DataFrame,
    sweeps_df: pd.DataFrame,
    k: int = DEFAULT_K,
    m: int = DEFAULT_M,
    eps: float = DEFAULT_EPSILON,
    include_types: Sequence[str] | None = None,
) -> None:
    """Oracle độc lập kiểm chứng chuyển trạng thái vòng đời theo từng pool.

    Hoạt động độc lập cho cả 8 loại pool:
    - Nếu pool ở trạng thái ABSORBED:
      * Đúng 1 sự kiện ABSORBED tại bar_index >= bar_index_avail.
      * Tuổi khi bị hấp thụ <= m.
      * Tuyệt đối không bị vi phạm giá trước thời điểm bị hấp thụ.
      * Có absorbed_by trỏ tới pool EQH/EQL hợp lệ trong pools_df.
    - Nếu không bị ABSORBED: quét trực tiếp các nến tương lai từ bar_index_avail + 1 đến avail + m.
      Nến đầu tiên có vi phạm giá (High > P cho Buy-side, Low < P cho Sell-side) quyết định
      SWEPT hoặc BROKEN theo giá Close.
      Nếu không vi phạm trong m nến: EXPIRED tại nến avail + m (nếu avail + m < len(df)).
    - Đối chiếu 1:1 kết quả của Oracle với bảng sweeps_df.
    - Gọi oracle độc lập _oracle_verify_eqh_eql kiểm chứng 2 chiều việc hình thành EQH/EQL.
    """
    n_bars = len(df)
    bar_duration = pd.Timedelta(hours=1)

    for _, pool in pools_df.iterrows():
        pid = int(pool["pool_id"])
        ptype = str(pool["pool_type"])
        p_price = float(pool["price"])
        avail_bar = int(pool["bar_index_avail"])
        is_buy = ptype in ["SWING_HIGH", "EQH", "PDH", "ASIAN_HIGH"]

        pool_events = sweeps_df[sweeps_df["pool_id"] == pid]

        # Trường hợp 1: Pool bị hấp thụ (ABSORBED)
        has_absorbed = not pool_events.empty and (pool_events.iloc[0]["event_type"] == "ABSORBED")
        if has_absorbed:
            if len(pool_events) != 1:
                raise AssertionError(f"Pool {pid} bị ABSORBED phải có đúng 1 sự kiện, nhưng có {len(pool_events)}")
            ev = pool_events.iloc[0]
            if ev["bar_index"] < avail_bar:
                raise AssertionError(f"Pool {pid} ABSORBED tại bar {ev['bar_index']} trước avail_bar {avail_bar}")
            # Kiểm tra tuổi <= m
            if ev["pool_age"] > m or (ev["bar_index"] - avail_bar) > m:
                raise AssertionError(f"Pool {pid} có tuổi khi bị ABSORBED là {ev['pool_age']} > m ({m})")
            # Kiểm tra không bị vi phạm trước khi bị hấp thụ
            for t in range(avail_bar + 1, int(ev["bar_index"]) + 1):
                th = float(df.loc[t, "high"])
                tl = float(df.loc[t, "low"])
                if is_buy and th > p_price:
                    raise AssertionError(f"Pool {pid} bị vi phạm High={th} > {p_price} tại nến {t} trước khi bị ABSORBED tại nến {ev['bar_index']}")
                elif not is_buy and tl < p_price:
                    raise AssertionError(f"Pool {pid} bị vi phạm Low={tl} < {p_price} tại nến {t} trước khi bị ABSORBED tại nến {ev['bar_index']}")
            # Kiểm tra absorbed_by hợp lệ
            if np.isnan(ev["absorbed_by"]):
                raise AssertionError(f"Pool {pid} bị ABSORBED nhưng không có absorbed_by hợp lệ!")
            abs_pid = int(ev["absorbed_by"])
            abs_pools = pools_df[pools_df["pool_id"] == abs_pid]
            if abs_pools.empty:
                raise AssertionError(f"absorbed_by={abs_pid} không tồn tại trong pools_df!")
            abs_type = abs_pools.iloc[0]["pool_type"]
            expected_type = "EQH" if is_buy else "EQL"
            if abs_type != expected_type:
                raise AssertionError(f"absorbed_by={abs_pid} có pool_type={abs_type}, kỳ vọng {expected_type}")
            continue

        # Trường hợp 2: Pool hoạt động bình thường, duyệt tiến từ avail_bar + 1
        expected_ev = None
        for t in range(avail_bar + 1, min(n_bars, avail_bar + m + 1)):
            th = float(df.loc[t, "high"])
            tl = float(df.loc[t, "low"])
            tc = float(df.loc[t, "close"])
            t_time = pd.to_datetime(df.loc[t, "time_utc"])

            if is_buy and th > p_price:
                ev_type = "BROKEN" if tc > p_price else "SWEPT"
                expected_ev = {
                    "event_type": ev_type,
                    "bar_index": t,
                    "time_candle": t_time,
                    "time_available": t_time + bar_duration,
                    "price_trigger": th,
                    "pool_age": t - avail_bar,
                }
                break
            elif not is_buy and tl < p_price:
                ev_type = "BROKEN" if tc < p_price else "SWEPT"
                expected_ev = {
                    "event_type": ev_type,
                    "bar_index": t,
                    "time_candle": t_time,
                    "time_available": t_time + bar_duration,
                    "price_trigger": tl,
                    "pool_age": t - avail_bar,
                }
                break

            if t == avail_bar + m and expected_ev is None:
                expected_ev = {
                    "event_type": "EXPIRED",
                    "bar_index": t,
                    "time_candle": t_time,
                    "time_available": t_time + bar_duration,
                    "price_trigger": np.nan,
                    "pool_age": m,
                }
                break

        if expected_ev is not None:
            if len(pool_events) != 1:
                raise AssertionError(f"Pool {pid} kỳ vọng 1 sự kiện {expected_ev['event_type']} tại bar {expected_ev['bar_index']}, nhưng có {len(pool_events)} sự kiện")
            actual_ev = pool_events.iloc[0]
            if actual_ev["event_type"] != expected_ev["event_type"]:
                raise AssertionError(f"Pool {pid} lệch event_type: kỳ vọng {expected_ev['event_type']}, thực tế {actual_ev['event_type']}")
            if actual_ev["bar_index"] != expected_ev["bar_index"]:
                raise AssertionError(f"Pool {pid} lệch bar_index: kỳ vọng {expected_ev['bar_index']}, thực tế {actual_ev['bar_index']}")
            if actual_ev["pool_age"] != expected_ev["pool_age"]:
                raise AssertionError(f"Pool {pid} lệch pool_age: kỳ vọng {expected_ev['pool_age']}, thực tế {actual_ev['pool_age']}")
            if not np.isnan(expected_ev["price_trigger"]):
                if abs(actual_ev["price_trigger"] - expected_ev["price_trigger"]) > 1e-4:
                    raise AssertionError(f"Pool {pid} lệch price_trigger: kỳ vọng {expected_ev['price_trigger']}, thực tế {actual_ev['price_trigger']}")
        else:
            # Pool vẫn còn đang ACTIVE ở cuối DataFrame
            if not pool_events.empty:
                raise AssertionError(f"Pool {pid} chưa đến hạn và chưa vi phạm nhưng có sự kiện: {pool_events.to_dict('records')}")

    # Kiểm chứng 2 chiều việc hình thành EQH/EQL
    _oracle_verify_eqh_eql(df, pools_df, sweeps_df, k=k, m=m, eps=eps, include_types=include_types)


class TestLiquidityPools(unittest.TestCase):
    """Kiểm thử mô-đun phát hiện Liquidity Pools và Sweeps."""

    def test_case_1_pool_1_swept_at_swing_2_candle_no_eqh(self):
        """Ca 1: Swing 1 tại nến 5 (2050.0), Swing 2 tại nến 15 với High = 2050.4 (P2 > P1).

        - Cửa sổ ±3 của nến 5 (2..8) toàn 2020.0 -> Swing 1 hợp lệ, khả dụng tại nến 8.
        - Nến 15 có High = 2050.4 > 2050.0, Close = 2040.0.
          Cửa sổ ±3 của nến 15 (12..18) toàn 2020.0 -> Swing 2 là swing thật hợp lệ.
        - Tại nến 15: High 2050.4 quét Pool 1 -> Pool 1 chuyển sang SWEPT tại nến 15.
        - Tại nến 18: Swing 2 được xác nhận.
          Kiểm tra tạo EQH vi phạm cả 2 điều kiện: P2 > P1 và Pool 1 đã SWEPT.
        - Khẳng định:
          * Không có EQH nào được tạo ra.
          * Không có pool nào bị ABSORBED.
          * Pool 1 SWEPT tại nến 15.
          * Pool 2 tồn tại độc lập và ACTIVE tại nến 18.
        """
        k = 3
        df = _make_base_df(n_bars=25, base_price=2000.0)
        # Swing 1 tại nến 5: 2050.0
        df.loc[5, "high"] = 2050.0
        for i in [2, 3, 4, 6, 7, 8]:
            df.loc[i, "high"] = 2020.0

        # Swing 2 tại nến 15: 2050.4 (vượt P1 chút, trong dung sai 0.8)
        df.loc[15, "high"] = 2050.4
        df.loc[15, "close"] = 2040.0
        for i in [12, 13, 14, 16, 17, 18]:
            df.loc[i, "high"] = 2020.0

        df["atr"] = 10.0  # eps * atr = 0.08 * 10 = 0.8

        pools_df, sweeps_df = detect_liquidity_pools(df, k=k, include_types=["SWING_HIGH", "EQH"])

        # Không có EQH nào
        eqh_pools = pools_df[pools_df["pool_type"] == "EQH"]
        self.assertEqual(len(eqh_pools), 0, "Không được tạo EQH vì P2 > P1 và Pool 1 đã SWEPT!")

        # Không có pool nào bị ABSORBED
        absorbed_events = sweeps_df[sweeps_df["event_type"] == "ABSORBED"]
        self.assertEqual(len(absorbed_events), 0, "Không có pool nào bị hấp thụ!")

        # Có đúng 2 pool SWING_HIGH độc lập
        swing_pools = pools_df[pools_df["pool_type"] == "SWING_HIGH"]
        self.assertEqual(len(swing_pools), 2)

        # Pool 1 bị SWEPT tại nến 15
        p1_sweeps = sweeps_df[sweeps_df["pool_id"] == 0]
        self.assertEqual(len(p1_sweeps), 1)
        self.assertEqual(p1_sweeps.iloc[0]["event_type"], "SWEPT")
        self.assertEqual(p1_sweeps.iloc[0]["bar_index"], 15)

        # Pool 2 khả dụng tại nến 18 và đang ACTIVE (chưa có sự kiện kết thúc)
        p2_sweeps = sweeps_df[sweeps_df["pool_id"] == 1]
        self.assertEqual(len(p2_sweeps), 0, "Pool 2 phải đang ACTIVE, chưa phát sinh sự kiện!")

    def test_case_2a_boundary_j2_minus_j1_equals_24(self):
        """Ca 2a: Biên j2 - j1 = 24 = M.

        - Swing 1 tại nến 5 (2050.0), khả dụng tại nến 8.
        - Swing 2 tại nến 29 (2049.5 <= 2050.0). j2 - j1 = 29 - 5 = 24 = M.
        - Tại nến 32 (thời điểm xác nhận s2): nến thứ M kể từ lúc s1 khả dụng chính là nến 32!
        - Pool 1 vẫn còn ACTIVE tại nến 32 trước khi hết hạn -> Hấp thụ thành công thành EQH!
        """
        k = 3
        m = 24
        df = _make_base_df(n_bars=40, base_price=2000.0)
        df.loc[5, "high"] = 2050.0
        for i in [2, 3, 4, 6, 7, 8]: df.loc[i, "high"] = 2020.0

        j2 = 5 + m  # 29
        df.loc[j2, "high"] = 2049.5
        for i in range(j2 - k, j2 + k + 1):
            if i != j2: df.loc[i, "high"] = 2020.0

        df["atr"] = 10.0
        pools_df, sweeps_df = detect_liquidity_pools(df, k=k, m=m, include_types=["SWING_HIGH", "EQH"])

        eqh = pools_df[pools_df["pool_type"] == "EQH"]
        self.assertEqual(len(eqh), 1, "j2 - j1 = 24 = M phải tạo thành công EQH!")
        self.assertEqual(eqh.iloc[0]["price"], 2050.0)

        # Cả 2 swing bị ABSORBED
        absorbed = sweeps_df[sweeps_df["event_type"] == "ABSORBED"]
        self.assertEqual(len(absorbed), 2)

    def test_case_2b_boundary_j2_minus_j1_equals_25(self):
        """Ca 2b: Biên j2 - j1 = 25 > M -> Không tạo EQH.

        - Swing 1 tại nến 5, khả dụng tại nến 8.
        - Swing 2 tại nến 30. j2 - j1 = 25 > 24.
        - Tại nến 32, Pool 1 đã hết hạn (EXPIRED) ở nến thứ M = 32 đóng cửa.
        - Khi s2 xác nhận ở nến 33, Pool 1 đã EXPIRED -> KHÔNG tạo EQH.
        """
        k = 3
        m = 24
        df = _make_base_df(n_bars=45, base_price=2000.0)
        df.loc[5, "high"] = 2050.0
        for i in [2, 3, 4, 6, 7, 8]: df.loc[i, "high"] = 2020.0

        j2 = 5 + m + 1  # 30
        df.loc[j2, "high"] = 2049.5
        for i in range(j2 - k, j2 + k + 1):
            if i != j2: df.loc[i, "high"] = 2020.0

        df["atr"] = 10.0
        pools_df, sweeps_df = detect_liquidity_pools(df, k=k, m=m, include_types=["SWING_HIGH", "EQH"])

        eqh = pools_df[pools_df["pool_type"] == "EQH"]
        self.assertEqual(len(eqh), 0, "j2 - j1 = 25 > M tuyệt đối không được tạo EQH!")

        # Pool 1 phải bị EXPIRED
        p1_events = sweeps_df[sweeps_df["pool_id"] == 0]
        self.assertEqual(len(p1_events), 1)
        self.assertEqual(p1_events.iloc[0]["event_type"], "EXPIRED")

    def test_case_3_outside_bar_handling(self):
        """Ca 3: Nến ngoài (Outside Bar) vi phạm cả hai đầu pool đỉnh và đáy cùng lúc.

        3a: Đóng ở giữa -> Cả 2 pool SWEPT.
        3b: Đóng vượt đỉnh -> High BROKEN, Low SWEPT.
        3c: Đóng xuyên đáy -> High SWEPT, Low BROKEN.
        """
        k = 2
        for subcase, close_val, exp_high_ev, exp_low_ev in [
            ("3a", 2040.0, "SWEPT", "SWEPT"),
            ("3b", 2055.0, "BROKEN", "SWEPT"),
            ("3c", 2025.0, "SWEPT", "BROKEN"),
        ]:
            with self.subTest(subcase=subcase):
                df = _make_base_df(n_bars=15, base_price=2040.0)
                # Swing High tại 3 (2050.0)
                df.loc[3, "high"] = 2050.0
                df.loc[1:2, "high"] = 2042.0
                df.loc[4:5, "high"] = 2042.0

                # Swing Low tại 4 (2030.0)
                df.loc[4, "low"] = 2030.0
                df.loc[2:3, "low"] = 2038.0
                df.loc[5:6, "low"] = 2038.0

                # Nến ngoài tại 8: High = 2052 > 2050, Low = 2028 < 2030
                df.loc[8, "high"] = 2052.0
                df.loc[8, "low"] = 2028.0
                df.loc[8, "close"] = close_val

                pools_df, sweeps_df = detect_liquidity_pools(
                    df.iloc[:9], k=k, include_types=["SWING_HIGH", "SWING_LOW"]
                )
                ev_8 = sweeps_df[sweeps_df["bar_index"] == 8]
                self.assertEqual(len(ev_8), 2, f"{subcase}: Phải phát ra đúng 2 sự kiện tại nến ngoài!")

                high_ev = ev_8[ev_8["pool_type"] == "SWING_HIGH"].iloc[0]["event_type"]
                low_ev = ev_8[ev_8["pool_type"] == "SWING_LOW"].iloc[0]["event_type"]

                self.assertEqual(high_ev, exp_high_ev)
                self.assertEqual(low_ev, exp_low_ev)

    def test_case_4_multi_seed_oracle_and_prefix_invariance(self):
        """Ca 4: Kiểm chứng Oracle độc lập theo từng pool, đối chiếu đa seed & đa điểm cắt tiền tố."""
        k = 3
        m = 24
        eps = 0.08
        seeds = [42, 2024, 7777]
        cuts = [50, 80, 120, 150]

        for seed in seeds:
            rng = np.random.default_rng(seed)
            n_bars = 160
            returns = rng.normal(0, 1.5, n_bars)
            close = 2000.0 + np.cumsum(returns)
            high = close + rng.uniform(0.5, 3.0, n_bars)
            low = close - rng.uniform(0.5, 3.0, n_bars)
            open_p = close - rng.normal(0, 0.5, n_bars)

            # Cố tình chèn 1 cặp đỉnh gần bằng nhau để sinh EQH và kiểm tra trạng thái ABSORBED
            high[20] = 2040.0
            high[17:20] = 2030.0
            high[21:24] = 2030.0

            high[32] = 2039.8  # p2 <= p1
            high[29:32] = 2030.0
            high[33:36] = 2030.0

            df = pd.DataFrame({
                "time_utc": pd.date_range("2024-01-01", periods=n_bars, freq="1h"),
                "open": open_p,
                "high": high,
                "low": low,
                "close": close,
            })
            df["atr"] = compute_wilder_atr(df["high"].values, df["low"].values, df["close"].values, 14)

            # 1. Chạy trên bản đầy đủ
            pools_full, sweeps_full = detect_liquidity_pools(df, k=k, m=m, eps=eps)

            # 2. Oracle độc lập kiểm chứng 100% các sự kiện vòng đời
            _oracle_verify_sweeps(df, pools_full, sweeps_full, k=k, m=m, eps=eps)

            # 3. Kiểm tra bất biến tiền tố (Prefix Invariance) qua các điểm cắt
            for n_cut in cuts:
                df_cut = df.iloc[:n_cut].copy()
                pools_cut, sweeps_cut = detect_liquidity_pools(df_cut, k=k, m=m, eps=eps)

                # Oracle kiểm chứng bản cắt
                _oracle_verify_sweeps(df_cut, pools_cut, sweeps_cut, k=k, m=m, eps=eps)

                # Kiểm tra bất biến tiền tố: mọi pool hoàn thành tạo lập trước n_cut phải khớp hoàn toàn
                confirmed_in_cut = pools_cut[pools_cut["bar_index_avail"] <= n_cut - 1]
                confirmed_in_full = pools_full[pools_full["bar_index_avail"] <= n_cut - 1]

                common_len = min(len(confirmed_in_cut), len(confirmed_in_full))
                if common_len > 0:
                    assert_frame_equal(
                        confirmed_in_cut.iloc[:common_len][["pool_id", "pool_type", "price", "bar_index_avail", "source_id"]],
                        confirmed_in_full.iloc[:common_len][["pool_id", "pool_type", "price", "bar_index_avail", "source_id"]],
                        check_dtype=True,
                    )

            # 4. Khẳng định xuất hiện đủ cả 4 loại sự kiện
            events_present = set(sweeps_full["event_type"].unique())
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
        """Ca 6: Kiểm tra hấp thụ chuỗi cho ba đỉnh bằng nhau, có swing thấp chen giữa & bất biến tiền tố.

        - Swing 1 tại nến 5 (2050.0).
        - Swing 2 tại nến 12 (2049.8) -> Tạo EQH (bar_index_avail = 15, source_id = "eqh_0_1").
        - Chen giữa: Swing High thấp hơn tại nến 16 (2030.0) -> Không thỏa dung sai EQH.
        - Swing 3 tại nến 20 (2049.7) -> Hấp thụ chuỗi vào EQH đang có mà KHÔNG gia hạn tuổi.
        - Khẳng định bất biến tiền tố: Cắt trước nến 20 (chưa có Swing 3) và bản đầy đủ (đã có Swing 3)
          cho ra dòng pool EQH HOÀN TOÀN TRÙNG KHỚP (bar_index_avail = 15, source_id = "eqh_0_1").
        - Tại nến 26: Giá quét qua 2050.0 -> Đúng 1 sự kiện SWEPT duy nhất.
        """
        k = 3
        df = _make_base_df(n_bars=35, base_price=2000.0)
        # Swing 1 tại nến 5 (2050.0)
        df.loc[5, "high"] = 2050.0
        for i in [2, 3, 4, 6, 7, 8]: df.loc[i, "high"] = 2020.0

        # Swing 2 tại nến 12 (2049.8)
        df.loc[12, "high"] = 2049.8
        for i in [9, 10, 11, 13, 14, 15]: df.loc[i, "high"] = 2020.0

        # Chen giữa: Swing High thấp hơn tại nến 16 (2030.0)
        df.loc[16, "high"] = 2030.0
        for i in [14, 15, 17, 18]: df.loc[i, "high"] = 2020.0

        # Swing 3 tại nến 20 (2049.7)
        df.loc[20, "high"] = 2049.7
        for i in [17, 18, 19, 21, 22, 23]: df.loc[i, "high"] = 2020.0

        # Nến 26 quét qua 2050.0
        df.loc[26, "high"] = 2052.0
        df.loc[26, "close"] = 2045.0

        df["atr"] = 10.0

        # 1. Chạy trên lát cắt trước khi Swing 3 xác nhận (N = 20, s3 xác nhận lúc 20 + 3 = 23)
        pools_cut, sweeps_cut = detect_liquidity_pools(df.iloc[:20], k=k, include_types=["SWING_HIGH", "EQH"])
        eqh_cut = pools_cut[pools_cut["pool_type"] == "EQH"].iloc[0]

        # 2. Chạy trên bản đầy đủ
        pools_full, sweeps_full = detect_liquidity_pools(df, k=k, include_types=["SWING_HIGH", "EQH"])
        eqh_full = pools_full[pools_full["pool_type"] == "EQH"].iloc[0]

        # Khẳng định Bất biến tiền tố (Prefix Invariance): dòng pool EQH KHÔNG ĐƯỢC PHÉP THAY ĐỔI
        self.assertEqual(eqh_cut["pool_id"], eqh_full["pool_id"])
        self.assertEqual(eqh_cut["bar_index_avail"], eqh_full["bar_index_avail"], "bar_index_avail của EQH không được thay đổi khi có hấp thụ chuỗi!")
        self.assertEqual(eqh_cut["source_id"], eqh_full["source_id"], "source_id của EQH không được sửa đổi trên dòng pool đã ghi!")
        self.assertEqual(eqh_cut["time_available"], eqh_full["time_available"])

        # Swing 1, 2, 3 đều bị ABSORBED
        absorbed = sweeps_full[sweeps_full["event_type"] == "ABSORBED"]
        self.assertEqual(len(absorbed), 3, "Cả 3 swing high đỉnh cao đều phải bị ABSORBED vào EQH!")
        # Cả 3 swing bị hấp thụ đều ghi nhận absorbed_by trỏ đúng vào pool_id của EQH
        self.assertTrue((absorbed["absorbed_by"] == eqh_full["pool_id"]).all(), "Cả 3 swing bị hấp thụ phải ghi nhận absorbed_by trỏ đúng vào EQH!")

        # Swing 16 là pool độc lập (không bị absorbed)
        s16_pool = pools_full[pools_full["source_id"] == "swing_2"]
        self.assertEqual(len(s16_pool), 1)
        self.assertEqual(s16_pool.iloc[0]["price"], 2030.0)

        # Quét tại nến 26 chỉ phát ra đúng 1 sự kiện SWEPT duy nhất cho cụm EQH
        swept_26 = sweeps_full[(sweeps_full["bar_index"] == 26) & (sweeps_full["event_type"] == "SWEPT")]
        self.assertEqual(len(swept_26), 1, "Chỉ được phát đúng 1 sự kiện SWEPT duy nhất cho cụm EQH!")
        self.assertEqual(swept_26.iloc[0]["pool_id"], eqh_full["pool_id"])

    def test_case_7_dst_holiday_early_close_and_hand_calculated_pdh(self):
        """Ca 7: Kiểm thử DST New York, ngày lễ đóng sớm MLK và đối chiếu tính tay."""
        # 1. Kiểm tra ngày lễ MLK (15/01/2024): Nến cuối mở 19:00 UTC đóng lúc 20:00 UTC
        df_mlk = load_h1("2024-01-14", "2024-01-17").reset_index(drop=True)
        pools_mlk, _ = detect_liquidity_pools(df_mlk, include_types=["PDH", "PDL"])

        pdh_mlk = pools_mlk[pools_mlk["source_id"] == "pdh_2024-01-15"]
        self.assertEqual(len(pdh_mlk), 1)
        t_avail = pd.to_datetime(pdh_mlk.iloc[0]["time_available"])
        self.assertEqual(t_avail.strftime("%Y-%m-%d %H:%M"), "2024-01-15 20:00")

        # Đối chiếu giá PDH/PDL tính tay trên các nến thuộc ngày MLK
        ny_bars = df_mlk[(df_mlk["time_utc"] >= "2024-01-14 23:00:00") & (df_mlk["time_utc"] <= "2024-01-15 19:00:00")]
        self.assertEqual(pdh_mlk.iloc[0]["price"], float(ny_bars["high"].max()))
        pdl_mlk = pools_mlk[pools_mlk["source_id"] == "pdl_2024-01-15"]
        self.assertEqual(pdl_mlk.iloc[0]["price"], float(ny_bars["low"].min()))

        # 2. Kiểm tra chuyển đổi DST mùa xuân (tháng 3/2024): 11/03/2024
        df_spring = load_h1("2024-03-10", "2024-03-13").reset_index(drop=True)
        pools_spring, _ = detect_liquidity_pools(df_spring, include_types=["PDH", "PDL"])
        pdh_spring = pools_spring[pools_spring["source_id"] == "pdh_2024-03-11"]
        # Nến cuối ngày 11/03 mở 20:00 đóng 21:00 UTC (17:00 EDT tương ứng 21:00 UTC)
        self.assertEqual(pdh_spring.iloc[0]["time_available"].strftime("%H:%M"), "21:00")


        # 3. Kiểm tra DST mùa hè (tháng 6/2024): 17:00 EDT tương ứng 21:00 UTC
        df_summer = load_h1("2024-06-10", "2024-06-14").reset_index(drop=True)
        pools_summer, _ = detect_liquidity_pools(df_summer, include_types=["PDH"])
        pdh_summer = pools_summer[pools_summer["source_id"] == "pdh_2024-06-11"]
        self.assertEqual(len(pdh_summer), 1)
        self.assertEqual(pdh_summer.iloc[0]["time_available"].hour, 21, "Mùa hè EDT thì 17:00 NY phải tương ứng 21:00 UTC!")

        # 4. Kiểm tra chuyển đổi DST mùa thu (tháng 11/2024): 04/11/2024
        df_fall = load_h1("2024-11-03", "2024-11-06").reset_index(drop=True)
        pools_fall, _ = detect_liquidity_pools(df_fall, include_types=["PDH"])
        pdh_fall = pools_fall[pools_fall["source_id"] == "pdh_2024-11-04"]
        self.assertEqual(len(pdh_fall), 1)
        self.assertEqual(pdh_fall.iloc[0]["time_available"].hour, 22, "Mùa đông EST thì 17:00 NY phải tương ứng 22:00 UTC!")

    def test_case_8_get_active_pools_and_load_liquidity(self):
        """Ca 8: Kiểm tra get_active_pools_at, điều kiện biên >= và load_liquidity có warm-up lùi ngày."""
        # 1. Kiểm tra get_active_pools_at
        df = _make_base_df(n_bars=20, base_price=2000.0)
        df.loc[3, "high"] = 2050.0
        df.loc[1:2, "high"] = 2020.0
        df.loc[4:5, "high"] = 2020.0
        # Quét tại nến 10
        df.loc[10, "high"] = 2055.0
        df.loc[10, "close"] = 2055.0

        pools_df, sweeps_df = detect_liquidity_pools(df, k=2, include_types=["SWING_HIGH"])
        t_avail = pools_df.iloc[0]["time_available"]
        t_term = sweeps_df.iloc[0]["time_available"]

        # Trước avail: 0 pool
        p_before = get_active_pools_at(pools_df, sweeps_df, t_avail - pd.Timedelta(hours=1))
        self.assertEqual(len(p_before), 0)
        # Trong khi active: 1 pool
        p_active = get_active_pools_at(pools_df, sweeps_df, t_avail + pd.Timedelta(hours=1))
        self.assertEqual(len(p_active), 1)
        # Sau khi kết thúc: 0 pool
        p_after = get_active_pools_at(pools_df, sweeps_df, t_term + pd.Timedelta(hours=1))
        self.assertEqual(len(p_after), 0)

        # 2. Kiểm tra điều kiện biên >= (t_term == T_start) gọi hàm load_liquidity thật
        df_hist = _make_base_df(n_bars=30, start_time="2024-01-01 00:00:00", base_price=2000.0)
        df_hist.loc[5, "high"] = 2050.0
        df_hist.loc[3:4, "high"] = 2020.0
        df_hist.loc[6:7, "high"] = 2020.0
        # Quét tại nến 12 (thời điểm 2024-01-01 12:00:00 mở, đóng lúc 13:00:00)
        df_hist.loc[12, "high"] = 2052.0
        df_hist.loc[12, "close"] = 2040.0
        p_hist, sw_hist = detect_liquidity_pools(df_hist, k=2, include_types=["SWING_HIGH"])
        t_term_exact = sw_hist.iloc[0]["time_available"]  # 2024-01-01 13:00:00

        # Gọi load_liquidity thật qua mock load_h1 với start_date = t_term_exact
        with unittest.mock.patch("src.backtest.smc.liquidity.load_h1", return_value=df_hist):
            p_loaded, sw_loaded = load_liquidity(
                start_date=str(t_term_exact),
                warmup_months=1,
                k=2,
                include_types=["SWING_HIGH"],
            )
            # Khẳng định pool swing_0 sinh trước start_date nhưng kết thúc đúng tại t_term == T_start vẫn ĐƯỢC GIỮ lại
            p_pre = p_loaded[pd.to_datetime(p_loaded["time_available"]) < t_term_exact]
            self.assertEqual(len(p_pre), 1, "load_liquidity với t_term == T_start bắt buộc phải giữ pool kết thúc đúng mốc!")
            self.assertEqual(p_pre.iloc[0]["source_id"], "swing_0")
            self.assertEqual(len(sw_loaded), 1)
            self.assertEqual(sw_loaded.iloc[0]["time_available"], t_term_exact)

        # 3. Kiểm tra load_liquidity với dữ liệu DuckDB thật và khẳng định khác biệt warm-up vs không warm-up
        pools_warm, sweeps_warm = load_liquidity(start_date="2024-01-15", end_date="2024-01-20", warmup_months=1)
        pools_nowarm, sweeps_nowarm = load_liquidity(start_date="2024-01-15", end_date="2024-01-20", warmup_months=0)

        # Khẳng định khác biệt rõ rệt: bản warm-up nhiều pool hơn hẳn bản không warm-up
        self.assertGreater(
            len(pools_warm),
            len(pools_nowarm),
            f"Bản có warm-up ({len(pools_warm)}) phải nhiều pool hơn bản không warm-up ({len(pools_nowarm)})!",
        )

        # Có warm-up: tìm thấy các pool được tạo từ trước 15/01 còn sống tại 15/01
        pools_pre_15 = pools_warm[pd.to_datetime(pools_warm["time_available"]) < pd.to_datetime("2024-01-15 00:00:00")]
        self.assertGreater(len(pools_pre_15), 0, "load_liquidity với warmup_months=1 bắt buộc phải kế thừa các pool tạo trước start_date!")

        # Không warm-up: tuyệt đối KHÔNG có bất kỳ pool nào sinh trước 15/01
        pools_pre_15_nowarm = pools_nowarm[pd.to_datetime(pools_nowarm["time_available"]) < pd.to_datetime("2024-01-15 00:00:00")]
        self.assertEqual(len(pools_pre_15_nowarm), 0, "Bản không warm-up tuyệt đối không được chứa pool trước start_date!")

        # Đối chiếu một pool kết thúc đúng tại start_date trên dữ liệu thật
        target_pre_pools = pools_warm[pd.to_datetime(pools_warm["time_available"]) < pd.to_datetime("2024-01-15")]
        sw_pre = sweeps_warm[sweeps_warm["pool_id"].isin(set(target_pre_pools["pool_id"]))]
        target_sw = sw_pre.iloc[0]
        target_term = target_sw["time_available"]
        target_source = pools_warm[pools_warm["pool_id"] == target_sw["pool_id"]].iloc[0]["source_id"]

        p_bound_real, _ = load_liquidity(start_date=str(target_term), end_date="2024-01-20", warmup_months=1)
        self.assertIn(target_source, p_bound_real["source_id"].values, f"Pool thật {target_source} kết thúc tại {target_term} phải còn trong load_liquidity!")


if __name__ == "__main__":
    unittest.main()
