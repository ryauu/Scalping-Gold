# PROTOCOL.md — Giao thức kiểm thử bot GOLD

**Trạng thái: ĐÃ ĐÓNG BĂNG.**
Chỉ có hiệu lực khi (1) bạn đã sửa/chốt mọi mục đánh dấu ⚠, (2) đổi trạng thái thành "ĐÃ ĐÓNG BĂNG", (3) commit git, và (4) ghi commit hash vào mục 8. Sau đó KHÔNG sửa; mọi thay đổi chỉ được thêm ở mục 9 (nhật ký) kèm lý do và ngày.

## 1. Mục tiêu
Đo xem baseline rule-based đơn giản trên GOLD có edge sau chi phí hay không, để dùng làm thước đo cho SMC và ML về sau.

## 2. Dữ liệu và chia lớp (theo `config/splits.json`, múi giờ UTC)
| Lớp | Khoảng ngày | Cách dùng |
|---|---|---|
| Development | 2018-01-01 → 2025-06-30 | Phát triển + walk-forward |
| Robustness | 2014-07-01 → 2017-12-31 | Kiểm tra độ bền, KHÔNG dùng để tinh chỉnh |
| Holdout | 2025-07-01 → hết dữ liệu | Chạy ĐÚNG 1 LẦN khi cấu hình đã đóng băng |

## 3. Chi phí giả định ⚠
- Spread = trung vị spread của chính năm đó (xem `DATA.md`) × 1,25.
- Trượt giá: 5 points mỗi lần khớp (vào và ra).
- Kịch bản stress (EXPLORATORY): spread cố định 51 points cho mọi năm.
- Mọi lệnh đo bằng R-multiple (không gộp lãi).

## 4. Baseline PRIMARY ⚠ (một cấu hình duy nhất)
Breakout phiên Á:
- Vùng Á: cao/thấp M1 từ 00:00 đến 07:00 UTC.
- Cửa sổ vào lệnh: 07:00 – 13:00 UTC. Tối đa 1 lệnh/ngày (hướng phá vỡ đầu tiên).
- Tín hiệu: nến M1 ĐÓNG CỬA vượt biên ± 0,1 × ATR(H1, 14); vào lệnh ở giá MỞ nến M1 kế tiếp. ATR chỉ dùng các nến H1 đã đóng.
- SL: biên đối diện của vùng Á. TP: 2R. Thoát cứng lúc 16:00 UTC.
- Nếu một nến M1 chạm cả SL lẫn TP: tính SL.
- Không vào lệnh trong giờ nghỉ hằng ngày; ngày thiếu dữ liệu vùng Á bị bỏ qua và đếm lại.

## 5. Tiêu chí PASS (đánh giá trên lớp Development) ⚠
Phải đúng CẢ 5 điều kiện:
1. Tổng số lệnh ≥ 800.
2. Kỳ vọng ròng ≥ +0,05R mỗi lệnh (sau chi phí).
3. Profit factor ≥ 1,10.
4. Kỳ vọng dương ở ít nhất 5 trên 8 năm lịch.
5. Hơn kiểm soát ngẫu nhiên với p ≤ 0,05: cùng giờ vào, cùng SL/TP theo R, hướng ngẫu nhiên, 1.000 lần mô phỏng.

Lớp Robustness và Holdout (mỗi lớp chạy một lần): cần đạt điều kiện 2 và 3.
FAIL sát nút vẫn là FAIL, không tự nới ngưỡng sau khi thấy kết quả.

## 6. Biến thể EXPLORATORY
Khung giờ khác, buffer, RR khác, lọc độ rộng vùng Á, kịch bản stress chi phí... chỉ để tham khảo, KHÔNG dùng để lật lại kết luận PRIMARY và KHÔNG tính vào quyết định PASS/FAIL.

## 7. Quy tắc chung
- Walk-forward có purge; mọi đặc trưng tại thời điểm t chỉ dùng dữ liệu đã đóng đến t (không look-ahead).
- Holdout chỉ chạy 1 lần cho mỗi thiết kế đã đóng băng; không tune lại sau khi thấy kết quả.
- Mỗi thiết kế mới trước hết đăng ký tiêu chí ở mục 9, rồi mới chạy; ghi số thứ tự thử nghiệm (N) để tính đến đa so sánh.
- Trước khi tin số liệu bất thường: đối chiếu bản gốc, bóc tách theo năm, kiểm tra 30-50 lệnh bằng mắt trên biểu đồ.
- Quy ước phí cố định ngay từ đầu (tránh lặp lại lỗi tính phí giữa các script).

## 8. Đóng băng
- Ngày đóng băng: 2026-10-13
- Git tag: `protocol-v1`
- Phiên bản `build_data.py`: 1.0 · `splits.json` commit: 7715bcf

## 9. Nhật ký thay đổi / đăng ký thử nghiệm
*Ghi chú về $N_{\text{trials}}$: Các dòng làm rõ quy ước (C1–C4) phục vụ chuẩn hóa kỹ thuật, không tính vào số thử nghiệm thật. $N_{\text{trials}}$ chỉ tăng khi có một thiết kế độc lập mới chạy kiểm thử P&L trên lớp Development để phục vụ hiệu chỉnh đa so sánh ($p_{\text{threshold}} = 0,05 / N_{\text{trials}}$).*

| Ngày | STT | N_trials | Nội dung | Tiêu chí đăng ký trước | Kết quả |
|---|---|---|---|---|---|
| 2026-10-14 | 1 | 1 | Baseline PRIMARY (mục 4) trên lớp Development (2018-01-01 → 2025-06-30) — Dữ liệu v1.0 | Mục 5 (đạt cả 5 điều kiện) | **FAIL** (1/5 điều kiện đạt). C1: 1.668 lệnh (PASS). C2: -0,0357R/lệnh (FAIL). C3: PF = 0,9169 (FAIL). C4: 4/8 năm dương (FAIL). C5: p = 0,1558 (FAIL). Git tag: `code-v1`, `results-baseline-v1`. |
| 2026-10-13 | 2 (C1) | — | Đăng ký ngưỡng nến vùng Á (bước 3) | Cửa sổ [00:00, 07:00) UTC. Ngưỡng hợp lệ: n_bars >= 378 (90% của 420 nến M1 lý thuyết). Ngày < 378 nến gán valid=False và bỏ qua. Dựa trên phân phối thực nghiệm (Development p0.5%=414.6, p50%=420; loại 2 ngày lễ tết 26/12 và 02/01 chỉ có 60 nến; Robustness loại 7 ngày) thuần túy theo chất lượng dữ liệu trước khi có P&L. | Đã chốt trước P&L |
| 2026-10-13 | 3 (C2) | — | Làm rõ buffer tín hiệu Breakout (bước 3b) | Buffer 0,1 × ATR(H1, 14) được lấy động tại thời điểm nến M1 đang xét trong cửa sổ [07:00, 13:00) UTC (thay đổi theo từng giờ từ nến H1 đã đóng gần nhất qua attach_h1_atr), không chốt cứng tại 07:00. Tín hiệu strictly: close > high + 0,1*ATR hoặc close < low - 0,1*ATR (bằng hoặc râu không tính). Vào lệnh tại giá Open của nến M1 kế tiếp theo thứ tự thực tế. Đăng ký trước khi code và chạy tín hiệu. | Đã chốt trước P&L |
| 2026-10-14 | 4 (C3) | — | C3 — Quy ước mô phỏng (bước 4) | Dữ liệu M1 là giá bid; 1 point = 0,01. Spread = trung vị spread năm đó × 1,25; slip = 5 points mỗi lần khớp (vào và ra); cột spread từng nến không dùng. BUY vào tại Open nến kế + spread + slip; SELL vào tại Open nến kế − slip. R_dist = \|giá vào đã gồm chi phí − SL\|; TP = giá vào ± 2×R_dist. Kích hoạt: BUY, SL khi bid low ≤ SL, TP khi bid high ≥ TP; SELL, SL khi bid high + spread ≥ SL, TP khi bid low + spread ≤ TP. Thoát tại mức kích hoạt: BUY trừ slip, SELL cộng slip. Kiểm tra từ chính nến vào lệnh. Gap qua SL: thoát tại Open (BUY: Open − slip; SELL: Open + spread + slip). Gap qua TP: chỉ lấy đúng mức TP. Chạm cả hai trong một nến: tính SL. Thoát cứng: nến đầu tiên có giờ ≥ 16:00 UTC, tại Open (BUY: − slip; SELL: + spread + slip). Hết dữ liệu trước 16:00: thoát tại Close nến cuối, ghi DATA_END và đếm. entry_delay được ghi lại, không dùng để lọc. | Đã chốt trước P&L |
| 2026-10-14 | 5 (C4) | — | C4 — Đo lường (bước 5) | (a) “8 năm lịch” = 2018-2025; năm 2025 tính là một năm dù chỉ đến 30/6; điều kiện: kỳ vọng dương ở ít nhất 5 trong 8 năm. (b) Kỳ vọng = trung bình pnl_r; profit factor = tổng pnl_r dương / \|tổng pnl_r âm\|; lệnh pnl_r = 0 không tính vào bên nào. (c) Kiểm soát ngẫu nhiên: với mỗi lệnh thật, mô phỏng cả BUY và SELL cùng entry_time, cùng entry_open, cùng khoảng cách SL theo giá d = \|entry_open − SL gốc\| (SL BUY = entry_open − d, SL SELL = entry_open + d), TP = 2R theo C3, cùng chi phí. Mỗi lần mô phỏng rút ngẫu nhiên BUY hoặc SELL cho từng lệnh với xác suất 0,5; 1.000 lần; seed cố định (seed = 42); thống kê = kỳ vọng; p = (1 + số lần kỳ vọng ngẫu nhiên ≥ kỳ vọng thật) / 1001. | Đã chốt trước P&L |
| 2026-10-16 | 6 (Quy tắc Baseline Data v2) | — | Quy tắc cập nhật Baseline PRIMARY trên dữ liệu Data v2 (Europe/Nicosia, build_data.py v2.0, tag data-v2) | (1) Nguồn dữ liệu mới Data v2 (Europe/Nicosia, build_data.py v2.0, commit dữ liệu gắn tag git data-v2) thay thế hoàn toàn dữ liệu cũ v1.0. (2) Kết quả trên Data v2 THAY THẾ HOÀN TOÀN kết quả cũ (Data v1); tuyệt đối không chọn lựa (no cherry-picking) giữa hai kết quả. (3) Tham số Baseline (Mục 4) và 5 tiêu chí PASS (Mục 5) giữ nguyên vẹn 100%. (4) Số thử nghiệm thật $N_{\text{trials}}$ KHÔNG TĂNG ($N_{\text{trials}} = 1$) do đây là khắc phục lỗi dữ liệu (data defect fix), không phải thử nghiệm chiến lược mới. (5) Chỉ chạy trên lớp Development (2018-01-01 → 2025-06-30). Bảo toàn tuyệt đối Robustness và Holdout. (6) Báo cáo song song cả hai cột để ghi nhận khách quan tác động của việc sửa lỗi múi giờ. | Đã chốt trước khi chạy lại P&L *(soạn thảo ngày 16/10 trước lần chạy, commit ngày 21/10)* |
| 2026-10-16 | 7 | 1 | Chạy lại Baseline PRIMARY trên lớp Development với dữ liệu Data v2 (Europe/Nicosia) | Mục 5 (đạt cả 5 điều kiện) | **FAIL** (1/5 điều kiện đạt). C1: 1.664 lệnh (PASS). C2: -0,0438R/lệnh (FAIL). C3: PF = 0,8987 (FAIL). C4: 4/8 năm dương (FAIL). C5: p = 0,2428 (FAIL, Monte Carlo 1.000 lần, seed = 42; sim mean = -0,0592R, std = 0,0216; exploratory: always BUY = -0,0626R, always SELL = -0,0568R). Chi tiết 8 năm: 2018 (-0,0191R), 2019 (-0,1276R), 2020 (+0,0180R), 2021 (-0,0975R), 2022 (+0,0308R), 2023 (-0,1471R), 2024 (+0,0216R), 2025 (+0,0060R). Tổng PnL: -72,92R; Win rate: 41,65%. Thay thế chính thức kết quả STT 1. |
| 2026-10-21 | 8 | 2 | Đăng ký thử nghiệm SMC Sweep Confirmation v1 (Lọc Sweep Thanh khoản + Xác nhận Cấu trúc & FVG) trên lớp Development (2018-01-01 → 2025-06-30) | Mục 5 (đạt cả 5 điều kiện, với C5 điều chỉnh đa so sánh $p \le 0{,}025$ do $N_{\text{trials}} = 2$). Chi tiết đặc tả logic đóng băng xem tiểu mục 4 bên dưới. | (chưa chạy — đã đóng băng trước khi code và tính toán) |

> **Ghi nhận trung thực lịch sử Git về STT 6 & STT 7:**  
> Quy tắc STT 6 được viết trước lần chạy ngày 16/10/2026, nhưng được commit vào git ngày 21/10/2026 (commit `789654c` cho STT 6 rồi `3340cde` cho STT 7 cùng ngày 21/10 do tách/tái cấu trúc lịch sử sau khi kết quả đã có; kiểm tra bằng `git log --format='%h %ad %s' --date=iso`). Do đó, thứ tự commit trong lịch sử git không còn độc lập chứng minh STT 6 có trước lần chạy. Kết luận baseline không đổi (vẫn FAIL trên cả v1 và v2, $N_{\text{trials}} = 1$ không tăng, không gây hại). Đối với mọi đăng ký chiến lược SMC tiếp theo, bắt buộc phải commit và gắn tag git trước khi chạy bất kỳ tính toán P&L nào.

### Chi tiết đối chiếu kết quả Baseline PRIMARY: Dữ liệu cũ v1.0 (NY+7) vs Dữ liệu mới v2.0 (Europe/Nicosia)

**1. Đối chiếu 5 điều kiện PASS trên lớp Development (2018-01-01 → 2025-06-30):**

| Tiêu chí | Ngưỡng PASS | Dữ liệu cũ v1.0 (NY+7) | Dữ liệu mới v2.0 (Europe/Nicosia) | Kết luận |
|---|---|---|---|---|
| **Điều kiện 1: Tổng số lệnh** | $\ge 800$ lệnh | **1.668 lệnh** (PASS) | **1.664 lệnh** (PASS) | ĐẠT (giảm 4 lệnh do nến phiên Á dịch chuẩn ở các tuần DST) |
| **Điều kiện 2: Kỳ vọng ròng** | $\ge +0,05$R/lệnh | **-0,0357R** (FAIL) | **-0,0438R** (FAIL) | KHÔNG ĐẠT (cả 2 đều âm sâu sau chi phí) |
| **Điều kiện 3: Profit Factor** | $\ge 1,10$ | **0,9169** (FAIL) | **0,8987** (FAIL) | KHÔNG ĐẠT (cả 2 đều < 1,00) |
| **Điều kiện 4: Số năm dương** | $\ge 5 / 8$ năm | **4 / 8 năm** (FAIL) | **4 / 8 năm** (FAIL) | KHÔNG ĐẠT (cùng 4 năm dương: 2020, 2022, 2024, 2025) |
| **Điều kiện 5: Kiểm soát ngẫu nhiên** | $p \le 0,05$ | **$p = 0,1558$** (FAIL) | **$p = 0,2428$** (FAIL) | KHÔNG ĐẠT (không vượt qua phân phối ngẫu nhiên) |
| **TỔNG KẾT TOÀN BỘ** | **Đạt cả 5 điều kiện** | **FAIL (1/5)** | **FAIL (1/5)** | **KẾT QUẢ THAY THẾ CHÍNH THỨC: FAIL** |

**2. Bảng phân rã 8 năm lịch chi tiết (2018–2025):**

| Năm | n (v1) | n (v2) | Kỳ vọng v1 (R) | Kỳ vọng v2 (R) | PF v1 | PF v2 | Win Rate v1 | Win Rate v2 | Dương? (v1) | Dương? (v2) |
|---|---|---|---|---|---|---|---|---|---|---|
| 2018 | 219 | 218 | -0,0170R | -0,0191R | 0,9567 | 0,9518 | 41,10% | 40,83% | Không | Không |
| 2019 | 225 | 225 | -0,0984R | -0,1276R | 0,7822 | 0,7235 | 37,33% | 36,89% | Không | Không |
| 2020 | 214 | 214 | +0,0236R | +0,0180R | 1,0597 | 1,0457 | 45,33% | 44,86% | **Có** | **Có** |
| 2021 | 228 | 231 | -0,0936R | -0,0975R | 0,8081 | 0,8026 | 39,04% | 38,53% | Không | Không |
| 2022 | 233 | 231 | +0,0271R | +0,0308R | 1,0635 | 1,0730 | 44,64% | 45,45% | **Có** | **Có** |
| 2023 | 229 | 230 | -0,1453R | -0,1471R | 0,7199 | 0,7159 | 36,24% | 36,09% | Không | Không |
| 2024 | 225 | 221 | +0,0264R | +0,0216R | 1,0672 | 1,0548 | 44,00% | 43,89% | **Có** | **Có** |
| 2025 | 95 | 94 | +0,0383R | +0,0060R | 1,1343 | 1,0202 | 55,79% | 54,26% | **Có** | **Có** |
| **Tổng** | **1.668** | **1.664** | **-0,0357R** | **-0,0438R** | **0,9169** | **0,8987** | **41,91%** | **41,65%** | **4/8** | **4/8** |

**3. Thông tin tham chiếu & Cơ chế thoát lệnh (Dữ liệu mới v2.0):**
- Tổng PnL: -72,92R (v1 là -59,56R).
- Lệnh thắng: 693, Lệnh thua: 971, Lệnh hòa: 0.
- Phân phối lý do thoát: TP: 153 lệnh; SL: 577 lệnh; SL_GAP: 3 lệnh; TIME 16:00 UTC: 931 lệnh; DATA_END: 0 lệnh.
- Monte Carlo (1.000 mô phỏng, seed = 42): Mean = -0,0592R, Std = 0,0216; Always BUY = -0,0626R; Always SELL = -0,0568R.
- Số thử nghiệm độc lập thật: $N_{\text{trials}} = 1$ (không tăng do đây là bước sửa lỗi dữ liệu).
- Kết luận: Baseline Breakout phiên Á thuần túy chính thức **FAIL** trên Development cả trước và sau khi sửa lỗi múi giờ. Lớp Robustness và Holdout tiếp tục được bảo toàn nguyên vẹn 100%.

### 4. Đăng ký trước thử nghiệm SMC Sweep Confirmation v1 (STT 8 — N_trials = 2)

**Trạng thái:** ĐÃ ĐÓNG BĂNG VÀ COMMIT TRƯỚC KHI CODE TÍN HIỆU VÀ TRƯỚC KHI TÍNH TOÁN P&L (Git tag: `prereg-smc-v1`).

#### A. Giả thuyết & Mục tiêu
Khắc phục điểm yếu chí mạng của Baseline Breakout (bị bẫy phá vỡ giả / fakeout tại các vùng thanh khoản quan trọng) bằng cách tiếp cận theo góc nhìn Smart Money Concepts (SMC): chỉ giao dịch theo hướng đảo chiều sau khi một vùng thanh khoản quan trọng đã bị quét (`SWEPT`) và được xác nhận bởi cấu trúc thị trường đảo chiều (`CHOCH`/`BOS`) cùng khối mất cân bằng (`FVG`) hợp lưu.

#### B. Biểu thức logic xác nhận duy nhất (Single Deterministic Confirmation Chain)
Chiến lược chỉ kích hoạt tín hiệu khi chuỗi sự kiện tuần tự sau đây được thỏa mãn trọn vẹn:

**1. Chuỗi tín hiệu SELL:**
1. **Sweep High Pool:** Tại nến H1 $s$, ghi nhận sự kiện `event_type == 'SWEPT'` của một trong 4 loại High pool: `SWING_HIGH`, `EQH`, `PDH`, `ASIAN_HIGH` (từ `lifecycle_df` của `liquidity.py`). Chỉ sự kiện `SWEPT` được tính; `BROKEN`, `ABSORBED`, `EXPIRED` tuyệt đối không kích hoạt.
2. **Sự kiện Cấu trúc Giảm (Bearish Structure Event):** Trong cửa sổ nến H1 $s < t_{\text{struct}} \le s + 24$, xuất hiện sự kiện `BEARISH_CHOCH` hoặc `BEARISH_BOS` từ bảng `events_df` của `structure.py`. Sự kiện cấu trúc phải hoàn thành sau nến sweep ($t_{\text{struct}} > s$).
3. **Khối FVG Giảm Hợp lưu (Bearish FVG Confluence):** Tại nến tín hiệu $c = t_{\text{struct}}$, BẮT BUỘC tồn tại ít nhất một khối FVG giảm (`direction == 'BEARISH'`) từ `fvg.py` thỏa mãn đồng thời cả 3 điều kiện:
   - `spans_gap == False` (loại bỏ hoàn toàn FVG bắc qua giờ nghỉ/cuối tuần).
   - Đang ở trạng thái `ACTIVE` tại nến $c$ (được tạo trước hoặc tại $c$, và chưa bị `INVALIDATED` hay `EXPIRED` tính đến thời điểm đóng nến $c$).
   - Được tạo ra từ nến sweep $s$ trở đi (`time_candle >= time_s`), thể hiện xung lực bán mạnh mẽ (displacement) xuất hiện ngay sau khi thanh khoản bị quét.
4. Khi cả 3 điều kiện được thỏa mãn tại nến $c \le s + 24$, nến $c$ được chốt là **Nến Tín Hiệu (Signal Candle)**. Nếu sau 24 nến H1 kể từ $s$ mà chuỗi xác nhận chưa hoàn tất, sweep tại $s$ hết hiệu lực (`EXPIRED_WINDOW`) và bị hủy bỏ.

**2. Chuỗi tín hiệu BUY (đối xứng hoàn toàn):**
1. **Sweep Low Pool:** Tại nến H1 $s$, ghi nhận sự kiện `event_type == 'SWEPT'` của một trong 4 loại Low pool: `SWING_LOW`, `EQL`, `PDL`, `ASIAN_LOW`.
2. **Sự kiện Cấu trúc Tăng (Bullish Structure Event):** Trong cửa sổ $s < t_{\text{struct}} \le s + 24$, xuất hiện `BULLISH_CHOCH` hoặc `BULLISH_BOS` ($t_{\text{struct}} > s$).
3. **Khối FVG Tăng Hợp lưu (Bullish FVG Confluence):** Tại nến tín hiệu $c = t_{\text{struct}}$, tồn tại ít nhất một Bullish FVG có `spans_gap == False`, đang `ACTIVE` tại $c$, và được tạo từ nến $s$ trở đi (`time_candle >= time_s`).

*Tính duy nhất của thử nghiệm:* Các biến thể khác (chỉ FVG không cấu trúc, chỉ cấu trúc không FVG, hoặc FVG trước $s$) được xem là các giả thuyết khác biệt và KHÔNG đăng ký trong đợt này để bảo vệ chặt chẽ số bậc tự do ($N_{\text{trials}} = 2$).

#### C. Quy tắc giải quyết xung đột & đa pool
- **Xung đột hai hướng trong cùng một nến $s$:** Nếu trong cùng một nến H1 $s$ có cả High pool bị `SWEPT` và Low pool bị `SWEPT` (ví dụ nến ngoài quét cả 2 đầu): **HỦY BỎ HOÀN TOÀN CẢ HAI HƯỚNG VÀ GHI NHẬN ĐẾM `DROP_CONFLICT_SWEEP`**.
- **Nhiều pool cùng hướng trong cùng nến $s$:** Coi là **MỘT SWEEP DUY NHẤT** theo hướng đó tại nến $s$ (mức giá tham chiếu là pool có `price_level` cực trị nhất: cao nhất cho High pool, thấp nhất cho Low pool).
- **Giới hạn một lệnh per sweep:** Mỗi sự kiện sweep tại nến $s$ chỉ được sinh tối đa đúng **MỘT lệnh duy nhất**. Sau khi đã sinh một lệnh tại nến $c$, sweep $s$ đó đóng lại, không bao giờ sinh thêm lệnh thứ hai.

#### D. Điểm vào lệnh (Entry), Stop Loss (SL), Take Profit (TP) và R_dist
- **Điểm vào lệnh (Entry):** Giá mở cửa `Open` của nến M1 đầu tiên ngay sau khi nến tín hiệu H1 $c$ đóng cửa hoàn toàn ($close\_time_c = time_c + 1\text{h}$).
- **Cấu trúc chi phí (theo C3):**
  - Spread = trung vị spread năm đó $\times 1{,}25$.
  - Slippage = 5 points mỗi chiều (vào và ra).
  - Giá vào BUY: $\text{Open} + \text{spread} + \text{slip}$; Giá vào SELL: $\text{Open} - \text{slip}$.
- **Stop Loss (SL) theo cực trị cấu trúc:**
  - Đối với lệnh SELL: SL đặt tại mức High cao nhất trong đoạn từ nến sweep $s$ đến nến tín hiệu $c$ (bao gồm cả $s$ và $c$):
    $$SL = \max_{i \in [s, c]} High[i]$$
  - Đối với lệnh BUY: SL đặt tại mức Low thấp nhất trong đoạn từ nến sweep $s$ đến nến tín hiệu $c$:
    $$SL = \min_{i \in [s, c]} Low[i]$$
- **Ràng buộc khoảng cách R ($R_{dist}$ — SMC_SPEC Mục 7.2):**
  - $R_{dist} = |\text{Entry}_{\text{with\_friction}} - SL|$.
  - Điều kiện sàn chi phí: $R_{dist} \ge \max(0{,}5 \times ATR(H1, 14), 12{,}5 \times friction)$.
  - Điều kiện trần an toàn: $R_{dist} \le 0{,}10 \times \text{Entry}$.
  - *Xử lý vi phạm:* Mọi tín hiệu vi phạm sàn hoặc trần $R_{dist}$ sẽ **BỊ HỦY BỎ HOÀN TOÀN (DROP_INVALID_RDIST)** và được ghi nhận số lượng trong báo cáo.
- **Take Profit (TP):** Cố định tỷ lệ $RR = 1:2$ ($TP = \text{Entry} \pm 2 \times R_{dist}$).

#### E. Quy tắc Giờ nghỉ & Một vị thế
- **Giờ nghỉ hằng ngày của broker (Daily Maintenance Break & Weekend):** Không vào lệnh trong giờ nghỉ của broker (21:00–22:00 UTC mùa hè EDT / 22:00–23:00 UTC mùa đông EST, hoặc các nến thuộc giờ nghỉ cuối tuần). Tín hiệu rơi vào khung giờ này sẽ **BỊ HỦY BỎ (DROP_DAILY_BREAK)** và được đếm.
- **Quy tắc một vị thế duy nhất (Single Position Rule):** Tại bất kỳ thời điểm nào, chiến lược chỉ nắm giữ tối đa 1 vị thế mở. Mọi tín hiệu xuất hiện trong khi đang có vị thế mở sẽ **BỊ HỦY BỎ HOÀN TOÀN (DROP_POSITION_BUSY), KHÔNG HOÃN (NO POSTPONE)** sang sau khi vị thế đóng.

#### F. Tiêu chí PASS & Kiểm soát ngẫu nhiên ($N_{\text{trials}} = 2$)
- **Tập dữ liệu kiểm định:** Lớp Development (2018-01-01 → 2025-06-30). Bảo toàn tuyệt đối Robustness và Holdout.
- **5 Tiêu chí PASS (Mục 5):**
  - *Điều kiện 1 (Cỡ mẫu):* Tổng số lệnh thực thi $\ge 800$.
  - *Điều kiện 2 (Kỳ vọng):* Kỳ vọng ròng $\ge +0{,}05$R/lệnh sau chi phí.
  - *Điều kiện 3 (Profit Factor):* $PF \ge 1{,}10$.
  - *Điều kiện 4 (Tính ổn định):* Dương ở ít nhất 5 trong 8 năm lịch.
  - *Điều kiện 5 (Kiểm soát ngẫu nhiên C4(c)):* Monte Carlo 1.000 lần, seed 42. Do $N_{\text{trials}} = 2$, ngưỡng thống kê điều chỉnh:
    $$p \le \frac{0{,}05}{2} = 0{,}025$$

#### G. Quy trình thực hiện nghiêm ngặt
1. Đăng ký trước bằng commit và gắn tag `prereg-smc-v1` TRƯỚC KHI code mô-đun tín hiệu.
2. Xây dựng mô-đun tín hiệu `src/backtest/smc/signals.py` và kiểm thử unit test (kèm test khẳng định FVG `spans_gap == True` không bao giờ sinh tín hiệu).
3. Đếm tần suất tín hiệu theo từng năm (2018–2025) để kiểm tra điều kiện $\ge 800$ lệnh TRƯỚC KHI tính toán P&L. Nếu $< 800$ lệnh: ghi nhận kết quả thiếu mẫu mà không tự ý nới lỏng tham số.
4. Chạy mô phỏng P&L và ghi nhận kết quả chính thức vào STT 8 Mục 9.





