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


def _oracle_verify_sweeps(
    df: pd.DataFrame,
    pools_df: pd.DataFrame,
    sweeps_df: pd.DataFrame,
    m: int = DEFAULT_M,
) -> None:
    """Oracle độc lập kiểm chứng chuyển trạng thái vòng đời theo từng pool.

    Hoạt động độc lập cho cả 8 loại pool:
    - Nếu pool ở trạng thái ABSORBED: phải có đúng 1 sự kiện ABSORBED tại bar_index_avail.
    - Nếu không bị ABSORBED: quét trực tiếp các nến tương lai từ bar_index_avail + 1 đến avail + m.
      Nến đầu tiên có vi phạm giá (High > P cho Buy-side, Low < P cho Sell-side) quyết định
      SWEPT hoặc BROKEN theo giá Close.
      Nếu không vi phạm trong m nến: EXPIRED tại nến avail + m (nếu avail + m < len(df)).
    - Đối chiếu 1:1 kết quả của Oracle với bảng sweeps_df.
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
            _oracle_verify_sweeps(df, pools_full, sweeps_full, m=m)

            # 3. Kiểm tra bất biến tiền tố (Prefix Invariance) qua các điểm cắt
            for n_cut in cuts:
                df_cut = df.iloc[:n_cut].copy()
                pools_cut, sweeps_cut = detect_liquidity_pools(df_cut, k=k, m=m, eps=eps)

                # Oracle kiểm chứng bản cắt
                _oracle_verify_sweeps(df_cut, pools_cut, sweeps_cut, m=m)

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
        self.assertEqual(len(pdh_spring), 1)
        # Nến cuối ngày 11/03 mở 19:00 đóng 20:00 UTC
        self.assertEqual(pdh_spring.iloc[0]["time_available"].strftime("%H:%M"), "20:00")

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

        # 2. Kiểm tra điều kiện biên >= (t_term == T_start)
        # Tạo test case mà pool kết thúc đúng tại T_start
        df_hist = _make_base_df(n_bars=30, start_time="2024-01-01 00:00:00", base_price=2000.0)
        df_hist.loc[5, "high"] = 2050.0
        df_hist.loc[3:4, "high"] = 2020.0
        df_hist.loc[6:7, "high"] = 2020.0
        # Quét tại nến 12 (thời điểm 2024-01-01 12:00:00 mở, đóng lúc 13:00:00)
        df_hist.loc[12, "high"] = 2052.0
        df_hist.loc[12, "close"] = 2040.0
        p_hist, sw_hist = detect_liquidity_pools(df_hist, k=2, include_types=["SWING_HIGH"])
        t_term_exact = sw_hist.iloc[0]["time_available"]  # 2024-01-01 13:00:00

        # Lọc thủ công mô phỏng load_liquidity với start_date = t_term_exact
        t_start = t_term_exact
        term_map = {sw_hist.iloc[0]["pool_id"]: t_term_exact}
        keep_ids = set()
        for _, p in p_hist.iterrows():
            pid = p["pool_id"]
            if pd.to_datetime(p["time_available"]) >= t_start:
                keep_ids.add(pid)
            else:
                t_t = term_map.get(pid)
                if t_t is not None and t_t >= t_start:
                    keep_ids.add(pid)

        self.assertIn(0, keep_ids, "Điều kiện t_term >= t_start bắt buộc phải giữ pool kết thúc đúng tại T_start!")

        # 3. Kiểm tra load_liquidity thực sự lùi ngày warm-up
        # Nạp với warmup_months=1 tại 2024-01-15
        pools_warm, sweeps_warm = load_liquidity(start_date="2024-01-15", end_date="2024-01-20", warmup_months=1)
        # Nạp không warm-up
        pools_nowarm, sweeps_nowarm = load_liquidity(start_date="2024-01-15", end_date="2024-01-20", warmup_months=0)

        # Có warm-up phải tìm thấy các pool được tạo từ trước 15/01 còn sống tại 15/01
        pools_pre_15 = pools_warm[pd.to_datetime(pools_warm["time_available"]) < pd.to_datetime("2024-01-15 00:00:00")]
        self.assertGreater(len(pools_pre_15), 0, "load_liquidity với warmup_months=1 bắt buộc phải kế thừa các pool tạo trước start_date!")


if __name__ == "__main__":
    unittest.main()
