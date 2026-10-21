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

## 7. Quy tắc chung & Hiệu chỉnh đa so sánh
- Walk-forward có purge; mọi đặc trưng tại thời điểm t chỉ dùng dữ liệu đã đóng đến t (không look-ahead).
- **Bảo toàn tuyệt đối Robustness và Holdout:** Thiết kế đã FAIL trên Development tuyệt đối KHÔNG chạm vào Robustness hay Holdout. Hai lớp này chỉ mở khóa cho thiết kế nào vượt qua trọn vẹn Development (đạt cả 5 điều kiện).
- **Không tune lại trên thiết kế đã FAIL:** Mọi biến thể (đổi khung giờ, RR, lọc SMC...) là một giả thuyết độc lập mới, phải đăng ký trước ở Mục 9 kèm số thứ tự thử nghiệm thật ($N_{\text{trials}}$) trước khi chạy.
- **Đếm số thử nghiệm thật ($N_{\text{trials}}$):** Cột nhật ký có các dòng làm rõ quy ước kỹ thuật (Clarifications C1–C4). Khi tính toán đa so sánh, chỉ đếm số thử nghiệm độc lập thực tế đã chạy P&L trên Development ($N_{\text{trials}}$, hiện tại Baseline PRIMARY là $N_{\text{trials}} = 1$).
- **Quy tắc hiệu chỉnh đa so sánh (Multiple Testing Correction):**
  Vì lớp Development đã được "nhìn" qua các thử nghiệm, để ngăn chặn rủi ro dữ liệu (data snooping / p-hacking):
  1. **Siết ngưỡng p-value kiểm soát ngẫu nhiên (Điều kiện 5):** Áp dụng hiệu chỉnh Bonferroni theo số thử nghiệm thật:
     $$p_{\text{threshold}} = \frac{0,05}{N_{\text{trials}}}$$
     *(Ví dụ: thử nghiệm kế tiếp $N_{\text{trials}} = 2 \implies p \le 0,025$; $N_{\text{trials}} = 3 \implies p \le 0,0167$; $N_{\text{trials}} = 5 \implies p \le 0,010$).*
  2. **Đánh giá Deflated Sharpe Ratio (DSR):** Khi có thiết kế vượt qua, tính DSR với tham số $N = N_{\text{trials}}$ (Bailey & López de Prado 2014) để kiểm tra xem kỳ vọng ròng có vượt qua phân phối giá trị cực đại ngẫu nhiên hay không.
  3. Các tiêu chí cốt lõi (Kỳ vọng $\ge +0,05$R, $PF \ge 1,10$, $\ge 5/8$ năm dương) giữ nguyên, không được nới lỏng.
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
| 2026-10-16 | 6 (Quy tắc Baseline Data v2) | — | Quy tắc cập nhật Baseline PRIMARY trên dữ liệu Data v2 (Europe/Nicosia, build_data.py v2.0, tag data-v2) | (1) Nguồn dữ liệu mới Data v2 (Europe/Nicosia, build_data.py v2.0, commit dữ liệu gắn tag git data-v2) thay thế hoàn toàn dữ liệu cũ v1.0. (2) Kết quả trên Data v2 THAY THẾ HOÀN TOÀN kết quả cũ (Data v1); tuyệt đối không chọn lựa (no cherry-picking) giữa hai kết quả. (3) Tham số Baseline (Mục 4) và 5 tiêu chí PASS (Mục 5) giữ nguyên vẹn 100%. (4) Số thử nghiệm thật $N_{\text{trials}}$ KHÔNG TĂNG ($N_{\text{trials}} = 1$) do đây là khắc phục lỗi dữ liệu (data defect fix), không phải thử nghiệm chiến lược mới. (5) Chỉ chạy trên lớp Development (2018-01-01 → 2025-06-30). Bảo toàn tuyệt đối Robustness và Holdout. (6) Báo cáo song song cả hai cột để ghi nhận khách quan tác động của việc sửa lỗi múi giờ. | Đã chốt trước khi chạy lại P&L |




