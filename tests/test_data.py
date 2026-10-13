"""Kiểm thử mô-đun data.py:
1. Số nến các năm trọn vẹn (2014-2025) và kiểm tra năm dở dang 2026 trong DuckDB.
2. Chốt chặn Holdout và chuẩn hóa ngày cuối (chuỗi vs pd.Timestamp).
3. Cấu trúc M1Data: unpack qua __iter__, cấm __len__ (tránh bẫy), thuộc tính trực tiếp.
4. Thuật toán ATR chuẩn Wilder (RMA).
5. ATR(H1, 14) chống look-ahead tại 07:30 UTC lấy nến H1 06:00.
6. Chốt chặn Holdout trong load_h1 (mặc định chặn ở Development khi end_date=None).
7. Chốt chặn bảo vệ lớp Robustness (2014-07 đến 2017-12) cho M1 và nạp lùi H1 warm-up.
8. attach_h1_atr ở mốc biên 2025-06-30 không bị va vào chốt chặn Holdout.
9. Xử lý lỗi khi file splits.json bị hỏng.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Đảm bảo thư mục gốc repo có trong sys.path để có thể chạy trực tiếp: python tests/test_data.py
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
import pandas as pd

from src.backtest.data import (
    M1Data,
    attach_h1_atr,
    compute_wilder_atr,
    get_candle_counts_by_year,
    get_development_end_date,
    load_h1,
    load_m1,
    validate_date_range,
)

# Số lượng nến chuẩn cho các năm đã trọn vẹn (2014-2025) theo DATA.md và DuckDB view m1_utc
EXPECTED_COUNTS_FULL_YEARS = {
    2014: 192076,
    2015: 352656,
    2016: 353513,
    2017: 352107,
    2018: 351696,
    2019: 352158,
    2020: 354255,
    2021: 353758,
    2022: 354441,
    2023: 353062,
    2024: 354980,
    2025: 353069,
}
EXPECTED_TOTAL_2014_2025 = 4077771  # Tổng các năm đã đủ từ 2014 đến hết 2025


class TestDataModule(unittest.TestCase):
    def test_01_candle_counts_by_year(self):
        """Bước 1 kiểm tra: Số nến các năm hoàn chỉnh (2014-2025) và kiểm tra năm dở dang 2026."""
        actual_counts = get_candle_counts_by_year()

        # 1. Kiểm tra chính xác từng năm đã hoàn chỉnh (2014-2025)
        for year, expected in EXPECTED_COUNTS_FULL_YEARS.items():
            self.assertIn(year, actual_counts, f"Thiếu năm {year} trong kết quả truy vấn")
            self.assertEqual(
                actual_counts[year],
                expected,
                f"Số nến năm {year} không khớp: thực tế {actual_counts[year]}, kỳ vọng {expected}",
            )

        # 2. Kiểm tra tổng số nến của các năm trọn vẹn (2014-2025)
        total_full_years = sum(actual_counts[year] for year in EXPECTED_COUNTS_FULL_YEARS)
        self.assertEqual(
            total_full_years,
            EXPECTED_TOTAL_2014_2025,
            f"Tổng số nến 2014-2025 phải đúng {EXPECTED_TOTAL_2014_2025} nến M1",
        )

        # 3. Năm 2026 là năm dở dang: chỉ kiểm tra có dữ liệu và không rỗng (không cố định số nến)
        self.assertIn(2026, actual_counts, "Thiếu năm 2026 trong DuckDB")
        self.assertGreater(actual_counts[2026], 0, "Năm 2026 phải có nến M1")

    def test_02_holdout_guard(self):
        """Bước 1 kiểm tra: Chốt chặn bảo vệ Holdout và chuẩn hóa ngày cuối."""
        # 1. Truy vấn trong phạm vi Development bằng chuỗi ngày
        start_ts, end_ts = validate_date_range("2024-01-01", "2025-06-30")
        self.assertEqual(str(start_ts.date()), "2024-01-01")
        self.assertEqual(str(end_ts), "2025-06-30 23:59:59.999999")

        # 2. Truy vấn bằng pd.Timestamp không kèm giờ cũng phải mở rộng hết ngày
        start_ts_dt, end_ts_dt = validate_date_range(
            pd.Timestamp("2024-01-01"), pd.Timestamp("2025-06-30")
        )
        self.assertEqual(str(end_ts_dt), "2025-06-30 23:59:59.999999")

        # 3. Truy vấn vượt quá Development (vào Holdout: 2025-07-01 trở đi) mà không có cờ -> phải báo lỗi
        with self.assertRaises(ValueError) as ctx:
            validate_date_range("2025-01-01", "2025-07-01", allow_holdout=False)
        self.assertIn("Holdout", str(ctx.exception))

        with self.assertRaises(ValueError):
            load_m1("2025-01-01", "2025-07-01", allow_holdout=False)

        # 4. Truy vấn với cờ allow_holdout=True -> được phép
        start_ts_h, end_ts_h = validate_date_range("2025-01-01", "2025-07-01", allow_holdout=True)
        self.assertEqual(str(start_ts_h.date()), "2025-01-01")

    def test_03_load_m1_returns_numpy_arrays(self):
        """Bước 1 kiểm tra: load_m1 trả về M1Data, hỗ trợ unpack 5 mảng, bỏ __len__ để tránh bẫy."""
        data = load_m1("2024-01-02", "2024-01-02")
        self.assertIsInstance(data, M1Data)

        # Kiểm tra khả năng unpack 5 biến cốt lõi qua iterator
        time_arr, open_arr, high_arr, low_arr, close_arr = data

        self.assertIsInstance(time_arr, np.ndarray)
        self.assertIsInstance(open_arr, np.ndarray)
        self.assertIsInstance(high_arr, np.ndarray)
        self.assertIsInstance(low_arr, np.ndarray)
        self.assertIsInstance(close_arr, np.ndarray)

        self.assertTrue(np.issubdtype(time_arr.dtype, np.datetime64))
        self.assertEqual(open_arr.dtype, np.float64)
        self.assertEqual(high_arr.dtype, np.float64)
        self.assertEqual(low_arr.dtype, np.float64)
        self.assertEqual(close_arr.dtype, np.float64)

        n = len(time_arr)
        self.assertGreater(n, 0)
        self.assertEqual(data.n_bars, n)

        # Đảm bảo bẫy __len__ = 5 đã bị loại bỏ: len(data) phải ném TypeError
        with self.assertRaises(TypeError):
            len(data)

        # Truy cập thuộc tính trực tiếp (khuyến nghị để không bỏ sót spread / atr)
        self.assertEqual(len(data.close), n)
        self.assertIsNotNone(data.spread)
        self.assertEqual(len(data.spread), n)

    def test_04_wilder_atr_math(self):
        """Bước 2 kiểm tra: Thuật toán ATR chuẩn Wilder (RMA)."""
        high = np.array([10, 11, 12, 11, 12, 13, 14, 15, 14, 13, 12, 13, 14, 15, 16], dtype=float)
        low = np.array([9, 10, 11, 10, 11, 12, 13, 14, 13, 12, 11, 12, 13, 14, 15], dtype=float)
        close = np.array([9.5, 10.5, 11.5, 10.5, 11.5, 12.5, 13.5, 14.5, 13.5, 12.5, 11.5, 12.5, 13.5, 14.5, 15.5], dtype=float)

        atr = compute_wilder_atr(high, low, close, period=14)

        # 13 phần tử đầu tiên (từ 0 đến 12) phải là NaN
        self.assertTrue(np.all(np.isnan(atr[:13])))

        # Phần tử thứ 14 (index 13) là trung bình cộng của 14 giá trị TR đầu tiên
        self.assertFalse(np.isnan(atr[13]))
        self.assertFalse(np.isnan(atr[14]))

        # Giá trị nến 14 phải tuân theo công thức: (atr[13] * 13 + tr[14]) / 14
        tr14 = max(high[14] - low[14], abs(high[14] - close[13]), abs(low[14] - close[13]))
        expected_atr14 = (atr[13] * 13.0 + tr14) / 14.0
        self.assertAlmostEqual(atr[14], expected_atr14, places=7)

    def test_05_atr_no_lookahead_at_0730(self):
        """Bước 2 kiểm tra cốt lõi: Lúc 07:30 UTC giá trị ATR phải lấy từ nến H1 mở lúc 06:00, không phải 07:00."""
        # 1. Nạp nến H1 xung quanh ngày 2024-01-02 (bao gồm warm-up từ 2023-12-15)
        h1 = load_h1(start_date="2023-12-15", end_date="2024-01-03")
        h1_sorted = h1.sort_values("time_utc").reset_index(drop=True)

        # Tính ATR thô trên H1
        h1_sorted["atr_raw"] = compute_wilder_atr(
            h1_sorted["high"].to_numpy(),
            h1_sorted["low"].to_numpy(),
            h1_sorted["close"].to_numpy(),
            period=14,
        )

        atr_0600 = h1_sorted[h1_sorted["time_utc"] == "2024-01-02 06:00:00"]["atr_raw"].iloc[0]
        atr_0700 = h1_sorted[h1_sorted["time_utc"] == "2024-01-02 07:00:00"]["atr_raw"].iloc[0]

        # Đảm bảo 2 giá trị ATR này khác nhau để việc kiểm tra có ý nghĩa
        self.assertNotEqual(atr_0600, atr_0700, "ATR tại 06:00 và 07:00 phải khác nhau")

        # 2. Nạp nến M1 ngày 2024-01-02 và gắn ATR bằng hàm attach_h1_atr
        m1 = load_m1("2024-01-02 06:00:00", "2024-01-02 09:00:00")
        merged = attach_h1_atr(m1, h1=h1_sorted, period=14)

        # Tìm nến M1 lúc 07:30 UTC
        row_0730 = merged[merged["time_utc"] == "2024-01-02 07:30:00"]
        self.assertEqual(len(row_0730), 1, "Phải tìm thấy nến M1 lúc 07:30:00")
        m1_atr_0730 = row_0730["atr"].iloc[0]

        # Khẳng định: ATR tại 07:30 UTC PHẢI BẰNG ATR của nến H1 mở lúc 06:00
        self.assertAlmostEqual(
            m1_atr_0730,
            atr_0600,
            places=6,
            msg=f"ATR tại 07:30 UTC ({m1_atr_0730}) phải bằng ATR nến H1 06:00 ({atr_0600})",
        )

        # Khẳng định: ATR tại 07:30 UTC KHÔNG ĐƯỢC BẰNG ATR của nến H1 mở lúc 07:00 (chống look-ahead)
        self.assertNotAlmostEqual(
            m1_atr_0730,
            atr_0700,
            places=6,
            msg=f"ATR tại 07:30 UTC không được dính nến H1 07:00 ({atr_0700})",
        )

        # Kiểm tra thêm: tại 07:00:00 M1 cũng phải lấy ATR của nến 06:00
        row_0700 = merged[merged["time_utc"] == "2024-01-02 07:00:00"]
        if not row_0700.empty:
            self.assertAlmostEqual(row_0700["atr"].iloc[0], atr_0600, places=6)

        # Kiểm tra thêm: tại 06:59:00 M1 phải lấy ATR của nến 05:00
        row_0659 = merged[merged["time_utc"] == "2024-01-02 06:59:00"]
        if not row_0659.empty:
            atr_0500 = h1_sorted[h1_sorted["time_utc"] == "2024-01-02 05:00:00"]["atr_raw"].iloc[0]
            self.assertAlmostEqual(row_0659["atr"].iloc[0], atr_0500, places=6)

    def test_06_load_h1_holdout_guard(self):
        """Chốt chặn Holdout cho load_h1: mặc định chặn ở Development khi end_date=None."""
        # 1. Gọi load_h1() không truyền end_date và không có allow_holdout=True:
        # Tự động chặn ở cuối lớp Development (2025-06-30 23:59:59.999999)
        h1_default = load_h1(start_date="2025-01-01")
        max_time = pd.to_datetime(h1_default["time_utc"].max())
        self.assertLessEqual(max_time, pd.Timestamp("2025-06-30 23:59:59.999999"))

        # 2. Truyền end_date lấn vào Holdout mà không bật cờ -> phải báo lỗi
        with self.assertRaises(ValueError) as ctx:
            load_h1(start_date="2025-01-01", end_date="2025-07-01", allow_holdout=False)
        self.assertIn("Development", str(ctx.exception))

        # 3. Khi bật allow_holdout=True thì được phép nạp quá 2025-06-30
        h1_holdout = load_h1(start_date="2025-01-01", allow_holdout=True)
        max_time_h = pd.to_datetime(h1_holdout["time_utc"].max())
        self.assertGreater(max_time_h, pd.Timestamp("2025-06-30 23:59:59.999999"))

    def test_07_robustness_guard(self):
        """Chốt chặn bảo vệ lớp Robustness (2014-07 đến 2017-12)."""
        # 1. load_m1 trong khoảng Robustness mà không có cờ allow_robustness -> phải báo lỗi
        with self.assertRaises(ValueError) as ctx:
            load_m1("2015-01-01", "2015-01-02", allow_robustness=False)
        self.assertIn("Robustness", str(ctx.exception))

        # 2. Bật cờ allow_robustness=True -> nạp bình thường
        data_rob = load_m1("2015-01-02", "2015-01-02", allow_robustness=True)
        self.assertGreater(data_rob.n_bars, 0)

        # 3. load_h1 mặc định cho phép đọc lùi quá khứ để warm-up ATR
        h1_warmup = load_h1(start_date="2017-12-01", end_date="2018-01-02")
        self.assertFalse(h1_warmup.empty)

        # 4. load_h1 khi cố tình cấm allow_robustness=False thì phải báo lỗi
        with self.assertRaises(ValueError):
            load_h1(start_date="2017-12-01", end_date="2018-01-02", allow_robustness=False)

    def test_08_attach_h1_atr_boundary_no_clash(self):
        """attach_h1_atr tại mốc kết thúc lớp Development không bị va vào chốt chặn Holdout."""
        # Nạp M1 đến hết ngày 2025-06-30 (mốc cuối lớp Development)
        m1 = load_m1("2025-06-30 20:00:00", "2025-06-30 23:59:00")
        self.assertGreater(m1.n_bars, 0)

        # Tự động nạp H1 và gắn ATR mà không cần allow_holdout=True (vì đã bỏ +1h)
        merged = attach_h1_atr(m1, allow_holdout=False)
        self.assertIn("atr", merged.columns)
        self.assertFalse(merged["atr"].isna().all())

    def test_09_splits_json_error_handling(self):
        """Xử lý lỗi rõ ràng khi file splits.json bị hỏng cấu trúc."""
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write("{invalid json format")
            temp_path = Path(f.name)

        try:
            with self.assertRaises(ValueError):
                get_development_end_date(splits_path=temp_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)

