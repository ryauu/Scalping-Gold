"""Mô-đun data.py - Nạp dữ liệu và tính toán chỉ báo không look-ahead.

Bao gồm:
- M1Data: Cấu trúc chứa các mảng numpy (thời gian, open, high, low, close, spread, atr).
- normalize_end_date: Chuẩn hóa mốc thời gian kết thúc (mở rộng về cuối ngày nếu không kèm giờ).
- load_splits_config / get_development_end_date: Đọc và xác thực cấu hình phân chia lớp (splits.json).
- validate_date_range: Kiểm tra khoảng ngày và chốt chặn bảo vệ lớp Development / Robustness / Holdout.
- load_m1: Đọc nến M1 từ view m1_utc của DuckDB kèm chốt chặn bảo vệ.
- load_h1: Đọc nến H1 từ view h1_utc kèm chốt chặn mặc định ở lớp Development khi end_date=None.
- compute_wilder_atr: Tính ATR chuẩn Wilder trên chuỗi giá.
- attach_h1_atr: Dịch nến H1 xuống 1 nến và merge_asof backward vào M1 (chống look-ahead).
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import duckdb
import numpy as np
import pandas as pd

# Đường dẫn mặc định (tìm tương đối từ thư mục gốc của repository)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = _REPO_ROOT / "data" / "meta.duckdb"
DEFAULT_SPLITS_PATH = _REPO_ROOT / "config" / "splits.json"
FALLBACK_DEV_START = "2018-01-01"
FALLBACK_DEV_END = "2025-06-30"


def normalize_end_date(end_date: str | pd.Timestamp) -> pd.Timestamp:
    """Chuẩn hóa mốc thời gian kết thúc về cuối ngày nếu không kèm giờ.

    Nếu truyền chuỗi ngày 'YYYY-MM-DD' hoặc pd.Timestamp ở đúng 00:00:00 (không có giờ cụ thể),
    tự động mở rộng đến 23:59:59.999999 để bao gồm trọn vẹn dữ liệu của ngày đó.
    Nếu đã có giờ cụ thể (ví dụ: '2024-01-02 09:00:00' hoặc Timestamp có giờ khác 0), giữ nguyên.
    """
    if isinstance(end_date, str):
        s = end_date.strip()
        if len(s) <= 10:
            return pd.Timestamp(f"{s} 23:59:59.999999")
        return pd.to_datetime(s)

    ts = pd.to_datetime(end_date)
    if ts.hour == 0 and ts.minute == 0 and ts.second == 0 and ts.microsecond == 0:
        return ts.replace(hour=23, minute=59, second=59, microsecond=999999)
    return ts


def load_splits_config(splits_path: Path | str | None = None) -> dict[str, Any]:
    """Đọc file cấu hình config/splits.json.

    Nếu file tồn tại nhưng bị hỏng hoặc thiếu trường bắt buộc, báo lỗi rõ ràng (ValueError).
    Nếu file không tồn tại, phát cảnh báo và trả về cấu hình dự phòng mặc định.
    """
    path = Path(splits_path) if splits_path is not None else DEFAULT_SPLITS_PATH
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            raise ValueError(f"Không thể đọc file cấu hình splits tại '{path}': {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(f"Nội dung file cấu hình splits tại '{path}' phải là JSON object.")
        return data

    warnings.warn(
        f"Không tìm thấy file cấu hình splits tại '{path}'. Sử dụng mốc phân chia dự phòng.",
        UserWarning,
        stacklevel=2,
    )
    return {
        "development": [FALLBACK_DEV_START, FALLBACK_DEV_END],
        "robustness": ["2014-07-01", "2017-12-31"],
        "holdout": ["2025-07-01", "2026-10-13"],
    }


def get_development_end_date(splits_path: Path | str | None = None) -> pd.Timestamp:
    """Lấy mốc thời gian kết thúc của lớp Development từ config/splits.json (hết ngày 23:59:59.999999)."""
    data = load_splits_config(splits_path)
    dev_dates = data.get("development", [])
    if not isinstance(dev_dates, list) or len(dev_dates) < 2:
        raise ValueError("Cấu hình 'development' trong splits.json phải chứa ít nhất 2 mốc ngày [start, end].")
    return normalize_end_date(dev_dates[1])


def get_development_start_date(splits_path: Path | str | None = None) -> pd.Timestamp:
    """Lấy mốc thời gian bắt đầu của lớp Development từ config/splits.json."""
    data = load_splits_config(splits_path)
    dev_dates = data.get("development", [])
    if not isinstance(dev_dates, list) or len(dev_dates) < 2:
        raise ValueError("Cấu hình 'development' trong splits.json phải chứa ít nhất 2 mốc ngày [start, end].")
    return pd.to_datetime(dev_dates[0])


def validate_date_range(
    start_date: str | pd.Timestamp,
    end_date: str | pd.Timestamp,
    allow_holdout: bool = False,
    allow_robustness: bool = False,
    splits_path: Path | str | None = None,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Kiểm tra khoảng ngày và kích hoạt chốt chặn ngăn vi phạm các lớp dữ liệu.

    Quy tắc bảo vệ:
    1. Chốt chặn Holdout: end_date > Development End (2025-06-30) bị chặn trừ khi allow_holdout=True.
    2. Chốt chặn Robustness: start_date < Development Start (2018-01-01) bị chặn trừ khi allow_robustness=True.
       (Ngăn việc vô tình đem dữ liệu Robustness ra tối ưu/tinh chỉnh trong pha phát triển).
    """
    start_ts = pd.to_datetime(start_date)
    end_ts = normalize_end_date(end_date)

    if start_ts > end_ts:
        raise ValueError(f"start_date ({start_ts}) không thể lớn hơn end_date ({end_ts})")

    dev_end_ts = get_development_end_date(splits_path)
    if end_ts > dev_end_ts and not allow_holdout:
        raise ValueError(
            f"Khoảng ngày kết thúc ({end_date}) vượt quá lớp Development (hạn chót: {dev_end_ts.date()}). "
            "Để ngăn ngừa rò rỉ dữ liệu (data leakage) vào Holdout, yêu cầu cờ allow_holdout=True để tiếp tục."
        )

    dev_start_ts = get_development_start_date(splits_path)
    if start_ts < dev_start_ts and not allow_robustness:
        raise ValueError(
            f"Khoảng ngày bắt đầu ({start_date}) nằm trong lớp Robustness (trước {dev_start_ts.date()}). "
            "Theo PROTOCOL.md, lớp Robustness KHÔNG được dùng để tinh chỉnh. "
            "Yêu cầu cờ allow_robustness=True nếu bạn đang chủ đích chạy vòng kiểm tra Robustness."
        )

    return start_ts, end_ts


@dataclass(frozen=True)
class M1Data:
    """Cấu trúc dữ liệu M1 đóng gói các mảng numpy.

    Khuyến nghị truy cập trực tiếp qua thuộc tính để không bỏ sót thông tin:
        data.time, data.open, data.high, data.low, data.close, data.spread, data.atr
        Số nến: data.n_bars
        Chuyển đổi DataFrame: data.to_dataframe()

    Vẫn hỗ trợ unpacking thành 5 mảng giá cốt lõi qua iterator:
        time, open_, high, low, close = data
    Lưu ý: Không dùng len(data); hãy dùng data.n_bars để lấy số lượng nến M1.
    """

    time: np.ndarray  # dtype: datetime64[us] hoặc datetime64[ns]
    open: np.ndarray  # dtype: float64
    high: np.ndarray  # dtype: float64
    low: np.ndarray  # dtype: float64
    close: np.ndarray  # dtype: float64
    spread: np.ndarray | None = None  # dtype: int32 hoặc None
    atr: np.ndarray | None = None  # dtype: float64 hoặc None

    def __iter__(self):
        """Unpack đúng 5 mảng numpy cốt lõi: thời gian, open, high, low, close."""
        return iter((self.time, self.open, self.high, self.low, self.close))

    @property
    def n_bars(self) -> int:
        return len(self.time)

    def to_dataframe(self) -> pd.DataFrame:
        """Chuyển thành pandas DataFrame."""
        df = pd.DataFrame(
            {
                "time_utc": self.time,
                "open": self.open,
                "high": self.high,
                "low": self.low,
                "close": self.close,
            }
        )
        if self.spread is not None:
            df["spread"] = self.spread
        if self.atr is not None:
            df["atr"] = self.atr
        return df


def load_m1(
    start_date: str | pd.Timestamp,
    end_date: str | pd.Timestamp,
    db_path: Path | str | None = None,
    allow_holdout: bool = False,
    allow_robustness: bool = False,
    include_spread: bool = True,
    splits_path: Path | str | None = None,
) -> M1Data:
    """Đọc dữ liệu nến M1 từ view m1_utc của DuckDB theo khoảng ngày.

    Tham số:
        start_date: Ngày bắt đầu (chuỗi hoặc Timestamp, UTC).
        end_date: Ngày kết thúc (chuỗi hoặc Timestamp, UTC).
        db_path: Đường dẫn đến file meta.duckdb (mặc định lấy theo cấu trúc repo).
        allow_holdout: Chốt chặn bảo vệ Holdout; nếu False, báo lỗi khi vượt quá 2025-06-30.
        allow_robustness: Chốt chặn bảo vệ Robustness; nếu False, báo lỗi khi trước 2018-01-01.
        include_spread: Có đọc cột spread hay không.
        splits_path: Đường dẫn tùy chọn tới file splits.json.

    Trả về:
        M1Data đóng gói các mảng numpy (truy cập qua thuộc tính hoặc unpack 5 giá cốt lõi).
    """
    start_ts, end_ts = validate_date_range(
        start_date,
        end_date,
        allow_holdout=allow_holdout,
        allow_robustness=allow_robustness,
        splits_path=splits_path,
    )

    resolved_db = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if not resolved_db.exists():
        # Fallback thử kiểm tra thư mục hiện tại
        if Path("data/meta.duckdb").exists():
            resolved_db = Path("data/meta.duckdb")
        else:
            raise FileNotFoundError(f"Không tìm thấy file DuckDB tại: {resolved_db}")

    query = """
        SELECT time_utc, open, high, low, close, spread
        FROM m1_utc
        WHERE time_utc >= ? AND time_utc <= ?
        ORDER BY time_utc
    """
    with duckdb.connect(str(resolved_db), read_only=True) as con:
        df = con.execute(query, [start_ts.to_pydatetime(), end_ts.to_pydatetime()]).df()

    time_arr = df["time_utc"].to_numpy()
    open_arr = df["open"].to_numpy(dtype=np.float64)
    high_arr = df["high"].to_numpy(dtype=np.float64)
    low_arr = df["low"].to_numpy(dtype=np.float64)
    close_arr = df["close"].to_numpy(dtype=np.float64)
    spread_arr = df["spread"].to_numpy(dtype=np.int32) if include_spread and "spread" in df else None

    return M1Data(
        time=time_arr,
        open=open_arr,
        high=high_arr,
        low=low_arr,
        close=close_arr,
        spread=spread_arr,
    )


def load_h1(
    start_date: str | pd.Timestamp | None = None,
    end_date: str | pd.Timestamp | None = None,
    db_path: Path | str | None = None,
    allow_holdout: bool = False,
    allow_robustness: bool = True,
    splits_path: Path | str | None = None,
) -> pd.DataFrame:
    """Đọc dữ liệu nến H1 từ view h1_utc của DuckDB.

    Chốt chặn bảo vệ:
    - Khi end_date là None: Mặc định tự động giới hạn ở hết lớp Development (2025-06-30),
      trừ khi allow_holdout=True.
    - Khi end_date được truyền vào: Phải <= ngày kết thúc Development, trừ khi allow_holdout=True.
    - Cho phép đọc lùi vào lớp Robustness (start_date < 2018-01-01) khi allow_robustness=True
      (mặc định True để phục vụ warm-up ATR).
    """
    resolved_db = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if not resolved_db.exists():
        if Path("data/meta.duckdb").exists():
            resolved_db = Path("data/meta.duckdb")
        else:
            raise FileNotFoundError(f"Không tìm thấy file DuckDB tại: {resolved_db}")

    dev_end_ts = get_development_end_date(splits_path)

    # Xử lý end_date: nếu None thì mặc định chặn ở hết lớp Development trừ khi allow_holdout=True
    if end_date is None:
        end_ts = None if allow_holdout else dev_end_ts
    else:
        end_ts = normalize_end_date(end_date)
        if end_ts > dev_end_ts and not allow_holdout:
            raise ValueError(
                f"end_date ({end_date}) vượt quá lớp Development (hạn chót: {dev_end_ts.date()}). "
                "Yêu cầu cờ allow_holdout=True để tiếp tục."
            )

    # Xử lý start_date
    if start_date is not None:
        start_ts = pd.to_datetime(start_date)
        if end_ts is not None and start_ts > end_ts:
            raise ValueError(f"start_date ({start_ts}) không thể lớn hơn end_date ({end_ts})")
        if not allow_robustness:
            dev_start_ts = get_development_start_date(splits_path)
            if start_ts < dev_start_ts:
                raise ValueError(
                    f"start_date ({start_date}) nằm trong lớp Robustness (trước {dev_start_ts.date()}). "
                    "Theo PROTOCOL.md, lớp Robustness KHÔNG được dùng để tinh chỉnh. "
                    "Yêu cầu cờ allow_robustness=True nếu bạn đang chủ đích chạy vòng kiểm tra Robustness."
                )
    else:
        start_ts = None

    clauses: list[str] = []
    params: list[Any] = []
    if start_ts is not None:
        clauses.append("time_utc >= ?")
        params.append(start_ts.to_pydatetime())
    if end_ts is not None:
        clauses.append("time_utc <= ?")
        params.append(end_ts.to_pydatetime())

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"""
        SELECT time_utc, open, high, low, close, spread, n_m1
        FROM h1_utc
        {where_sql}
        ORDER BY time_utc
    """

    with duckdb.connect(str(resolved_db), read_only=True) as con:
        df = con.execute(query, params).df()

    return df


def get_candle_counts_by_year(db_path: Path | str | None = None) -> dict[int, int]:
    """Lấy số lượng nến M1 theo từng năm trong DuckDB view m1_utc."""
    resolved_db = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    with duckdb.connect(str(resolved_db), read_only=True) as con:
        rows = con.execute("SELECT year, count(*) FROM m1_utc GROUP BY year ORDER BY year").fetchall()
    return {int(year): int(count) for year, count in rows}


def compute_wilder_atr(
    high: Sequence[float] | np.ndarray,
    low: Sequence[float] | np.ndarray,
    close: Sequence[float] | np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """Tính ATR chuẩn Wilder (RMA - Running Moving Average / Wilder's Smoothing).

    Công thức:
        TR_t = max(High_t - Low_t, |High_t - Close_{t-1}|, |Low_t - Close_{t-1}|)
        ATR_t (khởi đầu ở nến period-1) = trung bình cộng period giá trị TR đầu tiên
        ATR_t (về sau) = (ATR_{t-1} * (period - 1) + TR_t) / period
    """
    high_arr = np.asarray(high, dtype=np.float64)
    low_arr = np.asarray(low, dtype=np.float64)
    close_arr = np.asarray(close, dtype=np.float64)

    n = len(high_arr)
    atr = np.full(n, np.nan, dtype=np.float64)
    if n < period or period <= 0:
        return atr

    # Tính True Range (TR)
    tr = np.empty(n, dtype=np.float64)
    tr[0] = high_arr[0] - low_arr[0]
    for i in range(1, n):
        hl = high_arr[i] - low_arr[i]
        hc = abs(high_arr[i] - close_arr[i - 1])
        lc = abs(low_arr[i] - close_arr[i - 1])
        tr[i] = max(hl, hc, lc)

    # Khởi tạo ATR bằng trung bình cộng (SMA) của period giá trị TR đầu tiên
    atr[period - 1] = np.mean(tr[:period])

    # Làm mượt theo kiểu Wilder
    c1 = (period - 1) / period
    c2 = 1.0 / period
    for i in range(period, n):
        atr[i] = atr[i - 1] * c1 + tr[i] * c2

    return atr


def attach_h1_atr(
    m1: pd.DataFrame | M1Data,
    h1: pd.DataFrame | None = None,
    period: int = 14,
    db_path: Path | str | None = None,
    allow_holdout: bool = False,
) -> pd.DataFrame:
    """Gắn ATR(H1, period) vào dữ liệu M1 KHÔNG LOOK-AHEAD.

    Quy trình:
    1. Chuẩn bị nến H1: nếu không truyền vào, tự động nạp từ h1_utc với khoảng lùi (warm-up)
       30 ngày trước mốc M1 đầu tiên để đảm bảo ATR ổn định.
    2. Tính ATR kiểu Wilder trên nến H1.
    3. DỊCH XUỐNG 1 NẾN (shift(1)): tại thời điểm nhãn H1 (giờ mở nến), giá trị ATR được gán
       chính là ATR của nến H1 giờ trước (nến đã đóng hoàn chỉnh).
    4. Gắn vào M1 bằng merge_asof với hướng backward:
       - Ví dụ: Tại M1 lúc 07:30 UTC, merge_asof backward khớp với H1 lúc 07:00 UTC.
         Nhờ bước shift(1), H1 lúc 07:00 mang giá trị ATR tính từ nến H1 mở lúc 06:00
         (đã đóng lúc 07:00), hoàn toàn không dính nến 07:00 đang chạy.

    Lưu ý: end_date khi nạp H1 tự động được đặt bằng chính last_m1 (không cộng thêm 1 giờ)
    để tránh chạm vào chốt chặn Holdout ở mốc biên 2025-06-30.

    Trả về:
        DataFrame M1 chứa cột 'atr' đã căn chuẩn không look-ahead.
    """
    m1_df = m1.to_dataframe() if isinstance(m1, M1Data) else m1.copy()
    if m1_df.empty:
        m1_df["atr"] = np.nan
        return m1_df

    if "time_utc" not in m1_df.columns:
        raise KeyError("Dữ liệu M1 cần có cột 'time_utc'")

    # Chuẩn bị dữ liệu H1
    if h1 is None:
        first_m1 = pd.to_datetime(m1_df["time_utc"].min())
        last_m1 = pd.to_datetime(m1_df["time_utc"].max())
        # Warm-up 30 ngày trước first_m1 để ATR có đủ dữ liệu lịch sử
        warmup_start = first_m1 - pd.Timedelta(days=30)
        h1_df = load_h1(
            start_date=warmup_start,
            end_date=last_m1,
            db_path=db_path,
            allow_holdout=allow_holdout,
            allow_robustness=True,
        )
    else:
        h1_df = h1.copy()

    if h1_df.empty:
        m1_df["atr"] = np.nan
        return m1_df

    # 1. Tính ATR Wilder trên H1
    h1_df = h1_df.sort_values("time_utc").reset_index(drop=True)
    h1_atr_raw = compute_wilder_atr(
        h1_df["high"].to_numpy(),
        h1_df["low"].to_numpy(),
        h1_df["close"].to_numpy(),
        period=period,
    )
    h1_df["atr_raw"] = h1_atr_raw

    # 2. Dịch xuống 1 nến để tại thời điểm t chỉ dùng nến H1 ĐÃ ĐÓNG
    h1_df["atr_closed"] = h1_df["atr_raw"].shift(1)

    # 3. Gắn vào M1 bằng merge_asof hướng backward
    m1_df = m1_df.sort_values("time_utc").reset_index(drop=True)
    h1_subset = h1_df[["time_utc", "atr_closed"]].dropna(subset=["time_utc"]).sort_values("time_utc")

    merged = pd.merge_asof(
        m1_df,
        h1_subset.rename(columns={"atr_closed": "atr"}),
        on="time_utc",
        direction="backward",
    )

    return merged
