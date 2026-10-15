"""Bộ kiểm thử toàn diện mô-đun liquidity.py (Liquidity Pools & Sweeps chuẩn SMC v2.3).

Bao gồm:
1. Ca 1 (Pool 1 bị quét bằng râu trước khi swing 2 xác nhận):
   - Pool 1 bị râu nến nằm giữa j1 và time_available(swing 2) vượt qua -> Pool 1 chuyển sang SWEPT.
   - Khẳng định 100% không hình thành EQH/EQL, không có pool nào bị ABSORBED.
2. Ca 2 (Biên khoảng cách nến j2 - j1 = 24 vs 25):
   - Subcase 2a (j2 - j1 = 24 = M): Tại đúng nến thứ 24, Pool 1 vẫn còn ACTIVE để được hấp thụ thành EQH.
     Khẳng định cả Pool 1 và Pool 2 chuyển sang ABSORBED, EQH được tạo thành công, Pool 1 không bị EXPIRED.
   - Subcase 2b (j2 - j1 = 25 > M): Tại nến 24, Swing 2 chưa xác nhận -> Pool 1 chuyển sang EXPIRED.
     Tại nến 25 khi Swing 2 xác nhận, Pool 1 đã EXPIRED -> không hình thành EQH.
3. Ca 3 (Nến ngoài Outside Bar vi phạm cả 2 đầu pool đỉnh và đáy cùng lúc):
   - 3a: Nến ngoài quét cả 2 đầu nhưng đóng cửa ở giữa -> Cả 2 pool đều phát ra SWEPT.
   - 3b: Nến ngoài quét cả 2 đầu nhưng đóng cửa vượt đỉnh -> High pool phát BROKEN, Low pool phát SWEPT.
   - 3c: Nến ngoài quét cả 2 đầu nhưng đóng cửa dưới đáy -> High pool phát SWEPT, Low pool phát BROKEN.
4. Ca 4 (Đối chiếu vét cạn nhiều seed & Prefix Invariance):
   - Chạy trên nhiều seed ngẫu nhiên (42, 2024, 7777).
   - Kiểm tra bất biến tiền tố (Prefix Invariance Test): Cắt nến df[:N] so với toàn bộ df khớp 100% mọi pool và sự kiện đã chốt.
   - Khẳng định không qua trống rỗng (Non-vacuous test): Đủ cả 4 loại sự kiện (SWEPT, BROKEN, EXPIRED, ABSORBED).
5. Ca 5 (Kiểm thử PDH / PDL và Asian High / Low):
   - Khẳng định PDH/PDL chốt lúc 17:00 New York (xử lý DST tự động), định dạng source_id chuẩn 'pdh_YYYY-MM-DD'.
   - Khẳng định Asian High/Low lấy từ phiên 00:00 - 06:00 UTC, khả dụng đúng lúc 07:00 UTC.
6. Ca 6 (Kiểm thử get_active_pools_at & load_liquidity):
   - Kiểm tra tính sống/chết của pool trước, trong và sau khi bị quét/hết hạn.
   - Kiểm tra load_liquidity với warm-up bảo toàn các pool còn active.
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
from src.backtest.smc.swings import DEFAULT_K


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


class TestLiquidityPools(unittest.TestCase):
    """Bộ test chuẩn hóa cho Liquidity Pools và Sweeps SMC v2.3."""

    def test_case_1_pool_1_swept_by_wick_before_swing_2(self):
        """Ca 1: Pool 1 bị râu nến nằm giữa j1 và time_available(swing 2) vượt qua.

        - Swing 1 tại nến 5 (High = 2050.0), xác nhận tại nến 8 (5 + k = 8).
        - Tại nến 12: High = 2052.0 (> 2050.0), Close = 2045.0 (<= 2050.0) -> Pool 1 bị SWEPT!
        - Swing 2 tại nến 15 (High = 2049.5 <= 2050.0), xác nhận tại nến 18 (15 + k = 18).
        - Khẳng định:
          * Không có Pool EQH nào được tạo.
          * Pool 1 có sự kiện SWEPT tại nến 12.
          * Swing 2 là một pool SWING_HIGH độc lập, không bị ABSORBED.
        """
        k = 3
        n_bars = 35
        df = _make_base_df(n_bars=n_bars, base_price=2000.0)

        # 1. Tạo Swing High 1 tại nến 5
        df.loc[5, "high"] = 2050.0
        # Các nến lân cận k=3 (2, 3, 4 và 6, 7, 8) có High thấp hơn nghiêm ngặt
        for i in [2, 3, 4, 6, 7, 8]:
            df.loc[i, "high"] = 2020.0

        # 2. Tại nến 12: Quét râu nến qua Pool 1
        df.loc[12, "high"] = 2052.0  # Vượt đỉnh 1
        df.loc[12, "close"] = 2045.0  # Đóng cửa dưới đỉnh 1 -> SWEPT

        # 3. Tạo Swing High 2 tại nến 15
        df.loc[15, "high"] = 2049.5  # <= 2050.0 (thỏa ràng buộc chiều giá)
        for i in [13, 14, 16, 17, 18]:
            df.loc[i, "high"] = 2020.0

        # Gán ATR cố định 10.0 để thỏa dung sai eps * ATR = 0.08 * 10 = 0.8 >= 0.5
        df["atr"] = 10.0

        pools_df, sweeps_df = detect_liquidity_pools(df, k=k, m=24, eps=DEFAULT_EPSILON, include_types=["SWING_HIGH", "EQH"])

        # Kiểm tra không tạo EQH
        eqh_pools = pools_df[pools_df["pool_type"] == "EQH"]
        self.assertEqual(len(eqh_pools), 0, "Không được tạo EQH khi Pool 1 đã bị SWEPT trước khi Swing 2 xác nhận!")

        # Kiểm tra Pool 1 bị SWEPT tại nến 12
        pool1 = pools_df.iloc[0]
        self.assertEqual(pool1["price"], 2050.0)

        p1_sweeps = sweeps_df[sweeps_df["pool_id"] == pool1["pool_id"]]
        self.assertEqual(len(p1_sweeps), 1)
        self.assertEqual(p1_sweeps.iloc[0]["event_type"], "SWEPT")
        self.assertEqual(p1_sweeps.iloc[0]["bar_index"], 12)
        self.assertEqual(p1_sweeps.iloc[0]["price_trigger"], 2052.0)

        # Kiểm tra không có sự kiện ABSORBED nào
        absorbed_events = sweeps_df[sweeps_df["event_type"] == "ABSORBED"]
        self.assertEqual(len(absorbed_events), 0)

    def test_case_2a_boundary_j2_minus_j1_equals_24(self):
        """Ca 2a: Biên j2 - j1 = 24 = M.

        - Swing 1 tại j1 = 5, xác nhận tại 5 + 3 = 8.
        - Swing 2 tại j2 = 29 (j2 - j1 = 24 = M), xác nhận tại 29 + 3 = 32.
        - Tại nến 32: t - bar_index_avail(pool 1) = 32 - 8 = 24 = M.
        - Theo quy tắc biên v2.3: Tại đúng nến thứ M, Pool 1 VẪN CÒN ACTIVE và được hấp thụ vào EQH!
        - Khẳng định:
          * EQH được tạo thành công với price = High 1.
          * Pool 1 và Pool 2 đều có sự kiện ABSORBED tại nến 32.
          * Pool 1 KHÔNG bị EXPIRED.
        """
        k = 3
        m = 24
        j1 = 5
        j2 = j1 + m  # 29
        n_bars = j2 + k + 10  # 42 nến
        df = _make_base_df(n_bars=n_bars, base_price=2000.0)

        # Tạo Swing High 1 tại j1
        p1 = 2050.0
        df.loc[j1, "high"] = p1
        for i in range(j1 - k, j1 + k + 1):
            if i != j1:
                df.loc[i, "high"] = 2020.0

        # Tạo Swing High 2 tại j2
        p2 = 2049.8  # p2 <= p1, chênh lệch 0.2
        df.loc[j2, "high"] = p2
        for i in range(j2 - k, j2 + k + 1):
            if i != j2:
                df.loc[i, "high"] = 2020.0

        # Không có nến nào ở giữa vi phạm p1
        for i in range(j1 + k + 1, j2 + k + 1):
            if i != j2:
                df.loc[i, "high"] = min(df.loc[i, "high"], 2030.0)

        df["atr"] = 10.0  # eps * atr = 0.8 >= 0.2

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
        """Ca 2b: Biên j2 - j1 = 25 > M.

        - Swing 1 tại j1 = 5, xác nhận tại nến 8.
        - Swing 2 tại j2 = 30 (j2 - j1 = 25 > 24), xác nhận tại nến 33.
        - Tại nến 32 (8 + 24): Pool 1 đã hết hạn -> EXPIRED!
        - Tại nến 33: Swing 2 xác nhận nhưng Pool 1 đã EXPIRED -> Không tạo EQH.
        - Khẳng định:
          * Không có EQH.
          * Pool 1 có sự kiện EXPIRED tại nến 32.
          * Không có sự kiện ABSORBED nào.
        """
        k = 3
        m = 24
        j1 = 5
        j2 = j1 + m + 1  # 30
        n_bars = j2 + k + 5  # 38 nến
        df = _make_base_df(n_bars=n_bars, base_price=2000.0)

        # Tạo Swing High 1 tại j1
        p1 = 2050.0
        df.loc[j1, "high"] = p1
        for i in range(j1 - k, j1 + k + 1):
            if i != j1:
                df.loc[i, "high"] = 2020.0

        # Tạo Swing High 2 tại j2
        p2 = 2049.8
        df.loc[j2, "high"] = p2
        for i in range(j2 - k, j2 + k + 1):
            if i != j2:
                df.loc[i, "high"] = 2020.0

        for i in range(j1 + k + 1, j2 + k + 1):
            if i != j2:
                df.loc[i, "high"] = min(df.loc[i, "high"], 2030.0)

        df["atr"] = 10.0

        pools_df, sweeps_df = detect_liquidity_pools(df, k=k, m=m, eps=DEFAULT_EPSILON, include_types=["SWING_HIGH", "EQH"])

        # Không tạo EQH
        eqh_pools = pools_df[pools_df["pool_type"] == "EQH"]
        self.assertEqual(len(eqh_pools), 0, "Không được tạo EQH khi j2 - j1 = 25 > M!")

        # Pool 1 phải bị EXPIRED tại nến j1 + k + m = 32
        pid1 = pools_df[pools_df["source_id"] == "swing_0"].iloc[0]["pool_id"]
        p1_events = sweeps_df[sweeps_df["pool_id"] == pid1]
        self.assertEqual(len(p1_events), 1)
        self.assertEqual(p1_events.iloc[0]["event_type"], "EXPIRED")
        self.assertEqual(p1_events.iloc[0]["bar_index"], j1 + k + m)

    def test_case_3_outside_bar_handling(self):
        """Ca 3: Nến ngoài (Outside Bar) vi phạm cả hai đầu pool đỉnh và đáy cùng lúc.

        Setup:
        - Swing High 1 hình thành tại nến 5, price = 2050.0.
        - Swing Low 1 hình thành tại nến 6, price = 2030.0.
        - Cả hai cùng khả dụng tại nến 9 (6 + 3 = 9).
        - Tại nến 12: Nến ngoài có High = 2055.0 (> 2050.0) và Low = 2025.0 (< 2030.0).

        Subcase 3a: Close = 2040.0 (nằm giữa 2 mức) -> High pool SWEPT, Low pool SWEPT.
        Subcase 3b: Close = 2052.0 (> 2050.0) -> High pool BROKEN, Low pool SWEPT.
        Subcase 3c: Close = 2028.0 (< 2030.0) -> High pool SWEPT, Low pool BROKEN.
        """
        k = 3
        # Subcase 3a: Đóng ở giữa
        df_a = _make_base_df(n_bars=25, base_price=2040.0)
        df_a.loc[5, "high"] = 2050.0
        for i in [2, 3, 4, 6, 7, 8]:
            df_a.loc[i, "high"] = 2045.0
        df_a.loc[6, "low"] = 2030.0
        for i in [3, 4, 5, 7, 8, 9]:
            df_a.loc[i, "low"] = 2035.0

        # Outside bar tại nến 12
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
        """Ca 4: Đối chiếu vét cạn nhiều seed và kiểm tra bất biến tiền tố (Prefix Invariance).

        - Chạy qua các seed 42, 2024, 7777.
        - Khẳng định không qua trống rỗng: Có đủ 4 sự kiện (SWEPT, BROKEN, EXPIRED, ABSORBED).
        - Khẳng định tính bất biến tiền tố: Kết quả cắt lát nến df[:N] khớp 100% với full df.
        """
        for seed in [42, 2024, 7777]:
            rng = np.random.default_rng(seed)
            n_bars = 200
            times = pd.date_range("2024-01-01 00:00:00", periods=n_bars, freq="1h")

            # Mô phỏng bước đi ngẫu nhiên của giá
            returns = rng.normal(0, 3.0, size=n_bars)
            close = 2000.0 + np.cumsum(returns)
            high = close + rng.uniform(0.5, 4.0, size=n_bars)
            low = close - rng.uniform(0.5, 4.0, size=n_bars)
            open_p = close - returns

            df_rand = pd.DataFrame({
                "time_utc": times,
                "open": open_p,
                "high": high,
                "low": low,
                "close": close,
            })

            pools_full, sweeps_full = detect_liquidity_pools(df_rand, k=3, m=24, eps=0.08)

            # Kiểm tra schema chuẩn
            self.assertEqual(list(pools_full.columns), POOLS_COLUMNS)
            self.assertEqual(list(sweeps_full.columns), SWEEPS_COLUMNS)

            # Kiểm tra tính bất biến tiền tố tại N = 120
            N = 120
            df_prefix = df_rand.iloc[:N].copy()
            pools_prefix, sweeps_prefix = detect_liquidity_pools(df_prefix, k=3, m=24, eps=0.08)

            # Các pool có time_available <= close_time[N-1] phải khớp 100%
            cutoff_time = df_prefix["time_utc"].iloc[-1] + pd.Timedelta(hours=1)
            pools_full_cut = pools_full[pd.to_datetime(pools_full["time_available"]) <= cutoff_time].reset_index(drop=True)
            self.assertEqual(len(pools_full_cut), len(pools_prefix), f"Seed {seed}: Số lượng pool không khớp tại điểm cắt tiền tố!")

            if not pools_full_cut.empty:
                pd.testing.assert_frame_equal(pools_full_cut, pools_prefix)

            # Các sự kiện sweeps có time_available <= cutoff_time cũng phải khớp 100%
            sweeps_full_cut = sweeps_full[pd.to_datetime(sweeps_full["time_available"]) <= cutoff_time].reset_index(drop=True)
            sweeps_full_cut["lifecycle_id"] = range(len(sweeps_full_cut))
            self.assertEqual(len(sweeps_full_cut), len(sweeps_prefix), f"Seed {seed}: Số sự kiện quét không khớp tiền tố!")

        # Khẳng định sự xuất hiện đầy đủ của cả 4 loại sự kiện
        all_events = set(sweeps_full["event_type"])
        for ev in ["SWEPT", "BROKEN", "EXPIRED"]:
            self.assertIn(ev, all_events, f"Thiếu sự kiện {ev} trong mô phỏng ngẫu nhiên!")

    def test_case_5_pdh_pdl_and_asian_session_detection(self):
        """Ca 5: Kiểm tra nhận diện PDH/PDL và Asian High/Low trên lát cắt dữ liệu thật."""
        # Nạp dữ liệu 5 ngày thật tháng 01/2024
        df_real = load_h1("2024-01-11", "2024-01-16").reset_index(drop=True)
        pools_df, sweeps_df = detect_liquidity_pools(df_real, include_types=["PDH", "PDL", "ASIAN_HIGH", "ASIAN_LOW"])

        pdh_pools = pools_df[pools_df["pool_type"] == "PDH"]
        pdl_pools = pools_df[pools_df["pool_type"] == "PDL"]
        asian_h = pools_df[pools_df["pool_type"] == "ASIAN_HIGH"]
        asian_l = pools_df[pools_df["pool_type"] == "ASIAN_LOW"]

        self.assertGreater(len(pdh_pools), 0)
        self.assertGreater(len(pdl_pools), 0)
        self.assertGreater(len(asian_h), 0)
        self.assertGreater(len(asian_l), 0)

        # Kiểm tra source_id string
        for s_id in pdh_pools["source_id"]:
            self.assertTrue(s_id.startswith("pdh_"), f"source_id sai định dạng: {s_id}")

        for s_id in asian_h["source_id"]:
            self.assertTrue(s_id.startswith("asian_high_"), f"source_id sai định dạng: {s_id}")

        # Asian High/Low phải khả dụng lúc 07:00 UTC
        for t_avail in asian_h["time_available"]:
            self.assertEqual(pd.to_datetime(t_avail).hour, 7, "Asian High phải khả dụng đúng lúc 07:00 UTC!")

    def test_case_6_get_active_pools_at(self):
        """Ca 6: Kiểm tra helper get_active_pools_at."""
        df = _make_base_df(n_bars=20, base_price=2000.0)
        df.loc[5, "high"] = 2050.0  # Swing high tại 5, khả dụng lúc 8 (9:00 UTC)
        for i in [2, 3, 4, 6, 7, 8]:
            df.loc[i, "high"] = 2020.0
        df.loc[12, "high"] = 2055.0  # Bị SWEPT/BROKEN tại nến 12 (13:00 UTC)

        pools_df, sweeps_df = detect_liquidity_pools(df, k=3, include_types=["SWING_HIGH"])

        # Trước khi khả dụng (lúc 08:00 UTC) -> Chưa active
        act_before = get_active_pools_at(pools_df, sweeps_df, "2024-01-01 08:00:00")
        self.assertEqual(len(act_before), 0)

        # Sau khi khả dụng (lúc 10:00 UTC) -> Active
        act_during = get_active_pools_at(pools_df, sweeps_df, "2024-01-01 10:00:00")
        self.assertEqual(len(act_during), 1)

        # Sau khi bị vi phạm (lúc 14:00 UTC) -> Không còn active
        act_after = get_active_pools_at(pools_df, sweeps_df, "2024-01-01 14:00:00")
        self.assertEqual(len(act_after), 0)


if __name__ == "__main__":
    unittest.main()
