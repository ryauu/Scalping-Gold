# DATA.md — Dữ liệu vàng (GOLD) M1

Ngày ghi chú: 2026-10-13. Thư mục `data/` KHÔNG đưa lên git; file này là hồ sơ để dựng lại.

## 1. Nguồn
- Nền tảng: MetaTrader 5 (package Python `MetaTrader5`), Windows, tài khoản demo.
- Symbol trên broker: `GOLD`. Khung: M1.
- Khoảng yêu cầu: từ 2001-01 đến 2026-10-13. Thô nhận về: **4.363.757 dòng**, 2001-06-04 01:00 → 2026-10-13 09:03 (giờ server).
- Thư mục raw (bất biến): `data/raw/mt5/GOLD_M1/decf3c12/` (tên = 8 ký tự đầu của SHA-256 file nguồn; hash đầy đủ nằm trong bảng `sources` của `data/meta.duckdb`).
- Code tạo dữ liệu: `get_data.py` (tải) và `build_data.py` (làm sạch), chạy trong Jupyter. `SCRIPT_VERSION = "1.0"`.

## 2. Phát hiện quan trọng khi tải
MT5 trả nến thô hơn khi không có M1, và chúng lẫn vào chuỗi M1:
- 2001-06 → 2013-04: nến **D1** (khoảng 22 nến/tháng).
- 2013-05 → 2014-06: nến **H1** (khoảng 400-550 nến/tháng).
- **M1 thật bắt đầu 2014-06-13.** Trong cửa sổ MT5 thấy M1 từ 01:34, nhưng chuỗi 30 nến liên tiếp đầu tiên bắt đầu lúc **03:22** (giờ server) nên script cắt tại đây.
- Đã loại **9.890** nến thô. Sau làm sạch: **4.353.867** nến M1, và **72.955** nến H1 dựng lại.

## 3. Quy tắc làm sạch (build_data.py)
1. Cắt nến thô trước mốc M1 thật (xem trên).
2. Đổi giờ server sang UTC với giả thuyết: **giờ server = America/New_York + 7 giờ** (GMT+2 mùa đông, GMT+3 khi Mỹ dùng giờ mùa hè). Nến rơi vào giờ mơ hồ lúc chuyển giờ bị bỏ.
3. Giữ cột `time_server` để truy ngược; cột thời gian chuẩn là `time_utc`.
4. H1 dựng từ M1 sạch: nhãn thời gian = giờ MỞ nến, kèm `n_m1` (số nến M1 trong giờ), spread = trung vị.

## 4. Kiểm chứng múi giờ
- 2016-2026: giờ nghỉ hằng ngày (22:00 UTC mùa đông, 21:00 UTC mùa hè) **rỗng hoàn toàn** → giả thuyết offset đúng.
- 2014-2015: giờ nghỉ vẫn có nến lác đác; chỗ trũng chạy theo mùa (21h/22h UTC) chứ không đứng yên → nhiều khả năng broker lúc đó vẫn giao dịch trong giờ nghỉ, không phải sai offset (chưa kiểm chứng bằng nguồn thứ hai).
- Khoảng trống lớn trùng ngày lễ Mỹ và Giáng sinh/Năm mới.

## 5. Lưu ý khi dùng
- **M1 2014-2016 thưa:** phút không có tick thì không có nến. Backtest phải coi phút thiếu là "giá không đổi" (forward-fill), không phải đứt dữ liệu.
- **Giờ nghỉ 2014-2015** vẫn có nến: luật giao dịch phải tự loại giờ nghỉ, không dựa vào việc dữ liệu rỗng.
- **Spread (points) trung vị theo năm:** 2014: 45 · 2015: 39 · 2016: 43 · 2017: 41 · 2018: 32 · 2019: 32 · 2020: 33 · 2021: 35 · 2022: 26 · 2023: 30 · 2024: 31 · 2025: 34 · 2026: 51. Tỷ lệ nến spread = 0 gần như không đáng kể. Phân vị 99 của 2020 là 268 (nhiều khả năng giai đoạn Covid). Spread gần đây (2026) rộng hơn hẳn.
- Giá vàng thay đổi rất lớn giữa 2014 và 2026: mọi ngưỡng (SL/TP, khoảng cách vùng) phải tính theo ATR hoặc phần trăm, không theo giá tuyệt đối.

## 6. Cấu trúc thư mục (không commit)
```
data/
  raw/mt5/GOLD_M1/<sha8>/year=*/     # bản gốc, bất biến
  clean/GOLD_M1_utc/year=*/          # M1 đã cắt + UTC
  derived/GOLD_H1_utc/year=*/        # H1 dựng từ M1 sạch
  meta.duckdb                        # bảng sources + view m1_utc, h1_utc (đường dẫn tuyệt đối)
config/splits.json                   # (có commit) khoảng ngày dev / robustness / holdout
```

## 7. Chưa làm
- Dữ liệu 2009-2014 từ nguồn khác (Dukascopy hoặc tương tự); nếu lấy, giữ riêng nguồn, không nối vào chuỗi MT5.
- Đối chiếu giá/giờ giữa hai nguồn trên đoạn trùng.
