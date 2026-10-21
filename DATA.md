# DATA.md — Dữ liệu vàng (GOLD) M1

Ngày ghi chú: 2026-10-21 (khởi tạo: 2026-10-13, cập nhật Data v2: 2026-10-16, cập nhật manifest & phân tích giờ nghỉ: 2026-10-21). Thư mục `data/` KHÔNG đưa lên git; file này là hồ sơ để dựng lại và kiểm chứng tính toàn vẹn qua manifest SHA-256.

## 1. Nguồn
- Nền tảng: MetaTrader 5 (package Python `MetaTrader5`), Windows, tài khoản demo.
- Symbol trên broker: `GOLD`. Khung: M1.
- Thông số hợp đồng MT5 (`mt5.symbol_info("GOLD")`): `point = 0.01`, `digits = 2`. Quy đổi: `1 point = 0.01` giá (ví dụ: spread 32 points = $0.32/oz).
- Khoảng yêu cầu: từ 2001-01 đến 2026-10-13. Thô nhận về: **4.363.757 dòng**, 2001-06-04 01:00 → 2026-10-13 09:03 (giờ server).
- Thư mục raw (bất biến): `data/raw/mt5/GOLD_M1/decf3c12/` (tên = 8 ký tự đầu của SHA-256 file nguồn; hash đầy đủ nằm trong bảng `sources` của `data/meta.duckdb`).
- Code tạo dữ liệu: `get_data.py` (tải) và `build_data.py` (làm sạch), chạy trong Jupyter. `SCRIPT_VERSION = "2.1"`.

## 2. Phát hiện quan trọng khi tải
MT5 trả nến thô hơn khi không có M1, và chúng lẫn vào chuỗi M1:
- 2001-06 → 2013-04: nến **D1** (khoảng 22 nến/tháng).
- 2013-05 → 2014-06: nến **H1** (khoảng 400-550 nến/tháng).
- **M1 thật bắt đầu 2014-06-13.** Trong cửa sổ MT5 thấy M1 từ 01:34, nhưng chuỗi 30 nến liên tiếp đầu tiên bắt đầu lúc **03:22** (giờ server) nên script cắt tại đây.
- Đã loại **9.890** nến thô. Sau làm sạch: **4.353.867** nến M1, và **72.955** nến H1 dựng lại.

## 3. Quy tắc làm sạch (build_data.py v2.1)
1. Cắt nến thô trước mốc M1 thật (xem trên). Luôn gọi `cut_coarse_bars` trực tiếp, đối chiếu với file kỳ vọng bản chụp (`config/snapshot_expectations.json`) và bảng `sources`.
2. Đổi giờ server sang UTC theo múi giờ chuẩn: **`Europe/Nicosia` (EET/EEST, tuân thủ lịch đổi giờ mùa hè EU DST)**:
   - Mùa đông (EET): UTC+2 (trừ 2 giờ ra UTC).
   - Mùa hè (EEST): UTC+3 (trừ 3 giờ ra UTC).
   - Nến rơi vào giờ mơ hồ lúc chuyển giờ (`n_bad`): ghi nhận đúng 0 nến vì broker đóng cửa rạng sáng Chủ nhật lúc 03:00 giờ server.
3. Giữ cột `time_server` để truy ngược nguồn gốc; cột thời gian chuẩn hóa là `time_utc`.
4. H1 dựng từ M1 sạch: nhãn thời gian = giờ MỞ nến, kèm `n_m1` (số nến M1 trong giờ), spread = trung vị.
5. `write_splits`: Đã xóa khỏi `build_data.py` vì `config/splits.json` đã có sẵn và được commit trên git; script không tự ý tạo lại để bảo vệ tuyệt đối các mốc phân chia lớp dữ liệu.

## 4. Kiểm chứng múi giờ & Erratum DST
- **Erratum múi giờ v1.0:** Giả thuyết cũ v1.0 (`America/New_York + 7h`) làm lệch 1 giờ trong các tuần chênh DST giữa Mỹ và Châu Âu (tháng 3 và cuối tháng 10/đầu tháng 11).
- **Kiểm chứng quét toàn bộ 2014–2026 (4.353.867 nến M1):**
   - Chênh lệch $\Delta t = t_{\text{Nicosia}} - t_{\text{NY+7}}$ trên mọi dòng dữ liệu CHỈ CÓ ĐÚNG HAI GIÁ TRỊ: 0 giờ hoặc +1 giờ.
   - Tổng số 208 ngày giao dịch có nến chênh +1h TRÙNG KHỚP TUYỆT ĐỐI 100% với khoảng chênh DST lý thuyết tính bằng thư viện chuẩn `zoneinfo` (sai lệch 0 ngày).
   - Số lần xuất hiện nến mở lại dị thường lúc 21:00 UTC giảm từ 151 lần (dữ liệu cũ) về đúng 1 lần duy nhất trong toàn bộ 12 năm lịch sử (ngày 04/07/2014 do nghỉ lễ Quốc khánh Mỹ đóng cửa sớm).
   - Nến tin tức vĩ mô hồi hương chính xác: CPI về nến 12:00 UTC (thay vì 11:00 UTC), FOMC về nến 18:00 UTC (thay vì 17:00 UTC), và nến 20:00 UTC ngày FOMC không còn bị rỗng.
- **Giờ nghỉ giao dịch hàng ngày của broker/nguồn giá (Gold Spot):** Giờ nghỉ hằng ngày thường là 17:00–18:00 giờ New York, tương ứng với **21:00–22:00 UTC (mùa hè EDT)** và **22:00–23:00 UTC (mùa đông EST)**.
- **Phân tích 111 nến H1 thưa trong giờ nghỉ (2014–2015):**
  - Giai đoạn 2014–2015 có 111 nến H1 thưa trong giờ nghỉ (trung vị $n_{\text{m1}} = 14$ mùa đông EST lúc 22:00 UTC và 15 mùa hè EDT lúc 21:00 UTC, tối đa 15 nến).
  - Phân tích phân phối phút trong giờ của toàn bộ 2.853 nến M1 thuộc 111 nến H1 này: Các nến chỉ xuất hiện duy nhất ở các phút từ 00 đến 14 (mỗi phút có ~200 nến); từ phút 15 đến phút 59 hoàn toàn rỗng.
  - Kết luận: Hiện tượng này KHÔNG PHẢI là nhiễu broker mở sớm/đóng muộn ngẫu nhiên, mà nhất quán với lịch của broker/nguồn giá lúc đó: giờ nghỉ bảo trì bắt đầu từ **17:15 đến 18:00 New York** (thay vì 17:00).
  - Thời điểm đổi lịch: Tuần cuối tháng 6 / đầu tháng 7 năm 2015 (nến thưa 14 phút cuối cùng ghi nhận ngày 2015-06-30; từ ngày 2015-07-02 trở đi giờ nghỉ 21:00 UTC hoàn toàn rỗng, thị trường dừng giao dịch đúng 17:00 New York). Hiện tượng này chỉ quan trọng cho lớp Robustness (2014–2017) khi mở khóa về sau.

## 5. Lưu ý khi dùng
- **Độ phủ M1 2014-2016:** Độ phủ thực tế đạt khoảng 99% (không phải quá thưa như nhận định ban đầu). Phút không có tick thì không có nến; backtest coi phút thiếu là "giá không đổi" (forward-fill), không phải đứt dữ liệu.
- **Giờ nghỉ 2014-2015** vẫn có nến: luật giao dịch phải tự loại giờ nghỉ, không dựa vào việc dữ liệu rỗng.
- **Spread (points) trung vị theo năm:** 2014: 45 · 2015: 39 · 2016: 43 · 2017: 41 · 2018: 32 · 2019: 32 · 2020: 33 · 2021: 35 · 2022: 26 · 2023: 30 · 2024: 31 · 2025: 34 · 2026: 51. Tỷ lệ nến spread = 0 gần như không đáng kể. Phân vị 99 của 2020 là 268 (nhiều khả năng giai đoạn Covid). Spread gần đây (2026) rộng hơn hẳn.
- Giá vàng thay đổi rất lớn giữa 2014 và 2026: mọi ngưỡng (SL/TP, khoảng cách vùng) phải tính theo ATR hoặc phần trăm, không theo giá tuyệt đối.

## 6. Cấu trúc thư mục (không commit)
```
data/
  raw/mt5/GOLD_M1/<sha8>/year=*/     # bản gốc, bất biến (decf3c12)
  clean/GOLD_M1_utc/year=*/          # M1 đã cắt + UTC v2.0 (Europe/Nicosia)
  clean_v1/GOLD_M1_utc/year=*/       # Bản sao lưu M1 sạch v1.0 (đối chiếu)
  derived/GOLD_H1_utc/year=*/        # H1 dựng từ M1 sạch v2.0
  derived_v1/GOLD_H1_utc/year=*/     # Bản sao lưu H1 v1.0 (đối chiếu)
  meta.duckdb                        # bảng sources (status active/superseded) + view m1_utc, h1_utc
config/splits.json                   # (có commit) khoảng ngày dev / robustness / holdout
config/snapshot_expectations.json    # (có commit) kỳ vọng bản chụp 2026-10-13 (sha8: decf3c12)
```

## 7. Manifest kiểm chứng Data v2.0 (clean M1 & derived H1)
Do thư mục `data/` không commit lên git, bảng manifest dưới đây lưu số dòng, mã băm SHA-256 của file Parquet lưu trữ và mã băm theo nội dung chuẩn hóa (Canonical Content SHA-256) của từng phân vùng theo năm.

> **Quy ước tính Canonical Content SHA-256:**
> - Băm chuỗi text UTF-8 của các dòng dữ liệu sau khi sắp xếp tăng dần theo `time_utc`.
> - **Clean M1:** mỗi dòng định dạng `f"{time_utc},{time_server},{open:.2f},{high:.2f},{low:.2f},{close:.2f},{tick_volume},{spread}\n"`.
> - **Derived H1:** mỗi dòng định dạng `f"{time_utc},{open:.2f},{high:.2f},{low:.2f},{close:.2f},{tick_volume},{spread:.1f},{n_m1}\n"`.
> - Cho phép kiểm chứng tính toàn vẹn 100% độc lập với phiên bản DuckDB/Parquet/OS khi dựng lại.

### 7.1. Clean M1 (`data/clean/GOLD_M1_utc`) — Tổng cộng: 4.353.867 dòng
| Năm | Số dòng M1 | Kích thước (bytes) | File SHA-256 (bản lưu) | Canonical Content SHA-256 (nội dung) |
|:---:|:---:|:---:|:---|:---|
| 2014 | 192.076 | 5.109.288 | `0027e6d5e1a409a509135831c459ffd7285985b61e6cfbc79faaa93db62fb6b7` | `d7bb540f47c0021b08e99b652ecf9eb9db781ab4f6f221ce560905c0234f37f3` |
| 2015 | 352.656 | 9.490.829 | `9372d207deaab9726367df5a28afddecec2c20fabbcfa4076d05bb23f907cf6f` | `b5761ec7b2cdcd1cede731e2b18eaa3e75647d1959941e962003fab49e017706` |
| 2016 | 353.513 | 9.671.862 | `6e1dd0fce588508d4bd2a6504a5ad44df0088a8398e641b8659919cc7ffb7c2d` | `f1dcd19593862424afa7d434a00065cd5e6a3b3659e160802ffe9bdbb82b54fc` |
| 2017 | 352.107 | 9.422.670 | `45cdd5fc9b28e70b8d879e392ad380795820ebccfa5e6ad8d2d3f30a97a3fc13` | `ce43deb5e81e580de01555e8a68590c3c2674af090aff60f92864604a37c0aae` |
| 2018 | 351.696 | 9.180.599 | `985563c3506d19878d393813b2bd3221e9ac8fde25391e93001b905b15f545a5` | `f3e02490f78aca64b52efa5574c184b3df004f51ce097b7de4e15c12fac694da` |
| 2019 | 352.158 | 9.326.057 | `fc95e4e06229c74e76a6e4c92ca9e9faec21a4c1751b7bb78eccb625b6eff785` | `8f7a7745627a803d535c49339c682de4da3de76cd6277a33d4b408f5c2d52085` |
| 2020 | 354.255 | 10.131.037 | `f9ad4a443fe357ae6f6aed14d2be1bf1b62091051a605ecba79eba42aa78cd9e` | `a8e257f9c433505e539771c5cac595309288668a68bf1b7f191430cb43010610` |
| 2021 | 353.758 | 9.815.221 | `a05abc3a12f12315d9d99515d77da2933f9efeac17ef182f8df1dc84bf5eb001` | `ab53aa10362d3c64ab0907d63accdd6a136ec518620f965bd99a42ace4829c4d` |
| 2022 | 354.441 | 10.061.948 | `b52915fc08153661b102c27fe4f263b81e35cad62a071ccc9a7553187af3f607` | `fcf4cd551963f06b28f15b17322f3fbd8560f00abef8fbf6b16f4f740e4b4c54` |
| 2023 | 353.062 | 10.037.494 | `8dbd6e029f1451686e7023190a12eea8c8d0cab28cb8615e7aa2509c855a2587` | `95680c90ff9613365f3c4d6afe4694881846ed8bf2d88dfd1cd37c91cdd90b5b` |
| 2024 | 354.980 | 10.810.636 | `7ba5045f463256d1ee98b8edaa0fde8a3c11e9cb5571fa83bd85ed7b9e49e6df` | `79982f26286c7c17097841c445ad52df26bf89f0aaa65bb5241c4c7d1fd98072` |
| 2025 | 353.069 | 11.716.761 | `e33e6879d5d55775698bff7b5b259f30123f1b91c0971194b4c61fac0c98a381` | `e9d54f170d8d7255b3b626cd15dd2bf4931c0e1d25a1e2ae824a124e6cdfde0b` |
| 2026 | 276.096 | 10.056.994 | `4f71d216afdfc42e74e82b24d5bad530646d73240032a676092ae9a593c73b35` | `3a79d75041ccc0595fe26c656737f460c9ea101e83ffbab30a5857e3b323940d` |

### 7.2. Derived H1 (`data/derived/GOLD_H1_utc`) — Tổng cộng: 72.955 dòng
| Năm | Số dòng H1 | Kích thước (bytes) | File SHA-256 (bản lưu) | Canonical Content SHA-256 (nội dung) |
|:---:|:---:|:---:|:---|:---|
| 2014 | 3.361 | 119.495 | `f461ac424a5a7f7cde06e364adac61b965bd0646dced4e1a3600be98324deed3` | `27aa935c9e540727e0d739058cab7f2bff8bf8e3f52430c8e3dafa9bca0e6316` |
| 2015 | 5.993 | 205.853 | `97b17ce387c0c72added244aeb26ac8083f891b65761f44d6b0e465a60dc4b1d` | `6320a10d4008ee4e73bc21ff14936921ce5338fcb4f970266ac34d602424aa53` |
| 2016 | 5.906 | 207.922 | `5c60d754d6fa43cbb36df0a4a05c8f0c352aac4d59e80bbec887d8620a8fbcaf` | `74686840bb2e1669f1d1d0b4f1c9a0f9b7753d9358dbbdcb64e4aa7fb2b5e377` |
| 2017 | 5.876 | 198.282 | `e6bfc59f7df9c6a4b59f8ada6d5c44a0b73d8b245e77429b84cc720421e2f0de` | `243da5584ccb8508a21c5a43fe54be4f3e1d50af313b37da5bb750d8a7f51f23` |
| 2018 | 5.881 | 201.297 | `2b0d450e841430413036d8f921b8fd8cf743229eac0cb1795d504ee3f2e6798b` | `d978253e57cc5c376cb3cb4414722528138f57ace5d377507605b730c98769ef` |
| 2019 | 5.882 | 199.622 | `5a587c77418762985acc34852c62bec743402b3296191febe7e3a2ebfd8a253f` | `86e6e4a9c1593fc55911a72e7b6139d93a499aa3d4ba4445a01eaf54cbe0bb25` |
| 2020 | 5.912 | 211.443 | `1a2104095e6a73c7126ebc5a75e65308488a2c996c185365313b63b72587c8e0` | `7ae8293de03b9dd99cd48b11e9c457863afd3cee892b9d03514c08fc2b161cb4` |
| 2021 | 5.902 | 201.387 | `7e2c55e74ebe782d4b5c4d17c8a425f8d1bc8871c06681e80afe44fe6f02adea` | `765e83e30df080cd694267073fbd80f6026f453e18cea1d0cc1857b75ff537b2` |
| 2022 | 5.917 | 210.726 | `d6e1673d95efcdfb6ae8957562b0c7722cbd16090f455ab9565dd672e631bf15` | `3c88a90e723ca5f02c59efbe6056d8be0bf8249c5f7a5eed92092204cd21e0fc` |
| 2023 | 5.894 | 206.492 | `7b6b5d0f2329f428fac0731e069380624bd36625522563bf3e5d0f9569874b04` | `a5806c99eb415cb6b5c6e4ffc4d1539857468ca478a03dd760d978501e60b8cd` |
| 2024 | 5.928 | 226.264 | `e81ec2fd933980ce98853c20e4d7cde6da9e030e9478eaa5485262ace977ba37` | `0b4ccbd26444177c946d48ca827db709bd6b8f106106ff75be84968e6dd8720a` |
| 2025 | 5.894 | 233.452 | `958cffdffb22240680dd630e017a633e1e5b2c70dc16d5279dc8c2f95e09edb8` | `b47f4cc521dfae0e8f5ed4a632503cb9cf6276415e843455a967b8bcf2b2642e` |
| 2026 | 4.609 | 189.620 | `f4dd32156214c2f27c6dabf613326b73d7c0ac8d9da07b786d50bc8b6c1277f5` | `8c3ed0c169509dc3f533aeb6893ae41979e632791b85afa0da0355e69f5d9b80` |

## 8. Chưa làm
- Dữ liệu 2009-2014 từ nguồn khác (Dukascopy hoặc tương tự); nếu lấy, giữ riêng nguồn, không nối vào chuỗi MT5.
- Đối chiếu giá/giờ giữa hai nguồn trên đoạn trùng.

