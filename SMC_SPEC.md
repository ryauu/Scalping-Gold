# SMC_SPEC.md — Đặc tả kỹ thuật các thành phần Smart Money Concepts (SMC)

**Trạng thái: BẢN ĐẶC TẢ KỸ THUẬT ĐÃ ĐÓNG BĂNG V2.2 (Tag: `smc-spec-v2.2`)**  
*Ghi chú quan trọng: Toàn bộ các hiệu chỉnh trong bản v2.2 là việc chuẩn hóa toán học, khép kín các trường hợp biên và kiểm chứng thực nghiệm độc lập TRƯỚC KHI tiến hành bất kỳ thử nghiệm P&L nào.*

### Nhật ký hiệu chỉnh từ v1 sang v2.2 (Changelog):
- **V2-1 (Khởi tạo xu hướng từ NEUTRAL):** Đổi tên sự kiện phá vỡ đầu tiên từ `NEUTRAL` thành `BULLISH_INIT_BREAK` / `BEARISH_INIT_BREAK` thay vì gọi là BOS. Khẳng định đây là sự kiện khởi tạo xu hướng, không dùng làm tín hiệu giao dịch tiếp diễn.
- **V2-2 (Định nghĩa "Gần nhất" của Protected Level):** Quy định rõ ràng là gần nhất theo thời gian (`time_available`), với `bar_index` làm tie-breaker, loại bỏ cách hiểu theo mức giá.
- **V2-3 (Vòng đời FVG):** Sự kiện `TESTED` chỉ ghi nhận đúng 1 lần duy nhất (First Touch Only); xác lập thứ tự ưu tiên dứt điểm: `INVALIDATED` > `TESTED` > `EXPIRED`.
- **V2-4 (Quy tắc hấp thụ EQH/EQL):** Khi pool EQH/EQL hình thành, hai swing đơn lẻ cấu thành bị hấp thụ hoàn toàn vào pool, ngăn chặn việc 1 lần sweep bị đếm lặp 2–3 lần.
- **V2-5 (Đóng băng hệ số ATR):** Đưa hệ số $\alpha_{ATR} = 0,5$ vào bảng tham số PRIMARY để triệt tiêu bậc tự do ẩn.
- **V2-6 (Đăng ký quy tắc thoát lệnh SMC):** Đăng ký chính thức tỷ lệ R:R = 1:2 ($TP = Entry \pm 2 \cdot R_{dist}$) và kiểm soát ngẫu nhiên đối chứng C4.
- **V2-7 (Bảng tần suất sự kiện theo năm):** Bổ sung bảng số liệu thực nghiệm đầy đủ 2018–2025 trên H1 Development ($k=3$ và $k=5$).
- **V2-8 (Quy tắc che khuất v2 - Max Rule):** Chuyển điều kiện xóa swing sang $\le \max(P, Close[t])$ (với High) và $\ge \min(P, Close[t])$ (với Low) để triệt tiêu BOS ma khi nến đóng vượt mức swing mới nhưng mức swing cũ cao hơn vẫn chưa bị phá.
- **V2-9 (Chuẩn hóa Warm-up Period & Hàm load_structure mặc định toàn bộ lịch sử):** Kiểm chứng thực nghiệm chứng minh warm-up 24 tháng (nạp từ `2016-01-01`) hội tụ trùng khớp hoàn hảo 100% trên mọi thuộc tính sự kiện thị trường với toàn bộ lịch sử (từ `2014-06-01`) trên toàn bộ 4.486 sự kiện cấu trúc 2018–2025. Chuẩn hóa quy tắc mặc định nạp toàn bộ lịch sử trước $T_{\text{start}}$ (`warmup_months=None`), lọc sự kiện theo $time\_available \ge T_{\text{start}}$ (thay vì $time\_candle$) để bảo toàn nến 23:00 đóng cửa đúng 00:00 ngày bắt đầu, và ghi chú rõ `bar_index`, `structure_id` chỉ có ý nghĩa cục bộ.
- **V2-10 (Đóng băng đặc tả FVG & Vòng đời v2.2):** Chuẩn hóa quy tắc khoảng trống cuối tuần & giờ nghỉ hàng ngày (`spans_gap`), bộ lọc tối thiểu $\gamma \cdot ATR(H1, 14)_{[i-1]}$ không look-ahead, vòng đời 3 sự kiện (`TESTED`, `INVALIDATED`, `EXPIRED`) với quy tắc ưu tiên nghiêm ngặt, chuẩn hóa $time\_available = close\_time[j]$ cho từng sự kiện vòng đời, và làm rõ điều kiện chuyển tiếp nến $i+M+1$ nếu chưa bị `INVALIDATED`.

---

## 1. Khung thời gian & Dữ liệu chuẩn
- **Khung thời gian cấu trúc chính:** **H1** (nến 1 giờ UTC). Được tổng hợp từ dữ liệu M1 sạch trong `data/meta.duckdb` (view `h1_utc`), kèm chỉ báo biến động $ATR(H1, 14)$ tính theo chuẩn Wilder không look-ahead.
- **Quy ước nhãn thời gian H1:** Nhãn `time_utc` của nến H1 là giờ **MỞ** nến theo chuẩn UTC naive (ví dụ `08:00:00` là nến chạy từ `08:00:00` đến `08:59:59`).
- **Thời điểm đóng nến H1:** `close_time = open_time + 1 hour` (nến nhãn `08:00:00` đóng cửa hoàn chỉnh lúc `09:00:00`).

---

## 2. Swing High & Swing Low (`src/backtest/smc/swings.py`)

### 2.1. Định nghĩa toán học
Với một chuỗi nến H1 và tham số $k \in \mathbb{N}^*$ (số nến lân cận mỗi bên):
- **Đỉnh Swing High tại nến $i$:**
  $$High[i] > High[i - j] \quad \forall j \in \{1, \dots, k\} \quad \text{VÀ} \quad High[i] > High[i + j] \quad \forall j \in \{1, \dots, k\}$$
  *(Bắt buộc điều kiện lớn hơn nghiêm ngặt).*
- **Đáy Swing Low tại nến $i$:**
  $$Low[i] < Low[i - j] \quad \forall j \in \{1, \dots, k\} \quad \text{VÀ} \quad Low[i] < Low[i + j] \quad \forall j \in \{1, \dots, k\}$$
  *(Bắt buộc điều kiện nhỏ hơn nghiêm ngặt).*
- **Xử lý ca hòa (Tie Rule):**
  Nếu tồn tại bất kỳ nến lân cận nào có giá bằng đúng đỉnh/đáy ($High[i] = High[i \pm j]$ hoặc $Low[i] = Low[i \pm j]$ với $j \le k$), ứng viên tại $i$ **BỊ LOẠI BỎ HOÀN TOÀN**, không tạo swing đơn lẻ.  
  *Thực nghiệm trên dữ liệu vàng thật H1 (2018–2025):* Do độ chia giá vàng là $0,01$ USD, tỷ lệ ca hòa chỉ chiếm $\approx 1,73\%$ tổng ứng viên (khoảng 10–12 ca/năm với $k=3$). Khái niệm Equal Highs / Equal Lows (EQH/EQL) sẽ do mô-đun Pool thanh khoản (`liquidity.py`) quét trực tiếp trên cấu trúc swing riêng biệt với ngưỡng dung sai $\varepsilon \times ATR$, không phụ thuộc vào ca hòa cục bộ trong cửa sổ $k$.
- **Bảo vệ chống NaN & Kiểm tra đầu vào:**
  1. Kiểm tra đủ các cột bắt buộc `{'time_utc', 'high', 'low'}` trước tiên (kể cả với DataFrame rỗng).
  2. Dữ liệu `time_utc`, `high`, `low` bắt buộc không được chứa giá trị NaN/NaT.
  3. Thời gian `time_utc` phải tăng dần nghiêm ngặt (không trùng lặp).
  4. Chuẩn hóa đồng nhất về UTC naive.

### 2.2. Thời điểm khả dụng (`time_available`)
- Nến $i$ là đỉnh/đáy tiềm năng, nhưng chỉ được **xác nhận hoàn toàn** khi nến $i + k$ đã đóng cửa.
- Công thức:
  $$\text{time\_available}(i) = \text{close\_time}(i + k) = \text{open\_time}(i + k) + \text{bar\_duration}$$
- **Trường hợp bước qua khoảng trống (Gaps / Weekend):**
  Thời điểm khả dụng lấy theo thời điểm đóng cửa thực tế của nến thứ $i+k$ trong dữ liệu.  
  *Ví dụ:* Nếu nến $i$ là nến Thứ Sáu 21:00 UTC (nến cuối tuần) với $k=2$, thì nến $i+1$ là Chủ Nhật 23:00 UTC, nến $i+2$ là Thứ Hai 00:00 UTC. Swing chỉ khả dụng khi nến Thứ Hai 00:00 UTC đóng cửa (tức Thứ Hai 01:00 UTC). Tuyệt đối không cộng thời gian cơ học theo lịch.

### 2.3. Cấu trúc dữ liệu đầu ra & Sắp xếp ổn định (Stable Sort)
Danh sách swing được sắp xếp ổn định (`kind="stable"`) theo 3 khóa bắt buộc:
1. `time_available` tăng dần.
2. `bar_index` tăng dần.
3. `swing_type` (đảm bảo tính tất định đối với outside bar sinh cả HIGH và LOW cùng nến).

Mỗi bản ghi chứa các trường:
- `swing_id`: Số nguyên tăng dần từ 0 sau khi sắp xếp ổn định.
- `swing_type`: `'HIGH'` hoặc `'LOW'`.
- `bar_index`: Vị trí nến $i$ tạo đỉnh/đáy.
- `time_candle`: Thời gian mở nến $i$ (`time_utc[i]`).
- `time_available`: Thời điểm đóng nến $i + k$ (`time_utc[i + k] + bar_duration`).
- `price`: Mức giá đỉnh ($High[i]$) hoặc đáy ($Low[i]$).
- `k`: Tham số lân cận đã dùng.

---

## 3. Cấu trúc thị trường: BOS & CHoCH (`src/backtest/smc/structure.py`)

### 3.1. Trạng thái cấu trúc & Mức được bảo vệ (Protected Level)
- **Trạng thái cấu trúc thị trường (Market Trend State):**
  - `BULLISH`: Thị trường đang trong cấu trúc tăng.
  - `BEARISH`: Thị trường đang trong cấu trúc giảm.
  - `NEUTRAL`: Trạng thái khởi tạo ban đầu khi chưa ghi nhận bất kỳ sự kiện phá vỡ cấu trúc nào.
- **Định nghĩa Mức được bảo vệ (Protected Level - Gần nhất theo thời gian):**
  - **Đỉnh được bảo vệ (`Protected High`):** Là Swing High chưa bị phá **gần nhất theo thời gian (`time_available`)** (nếu trùng `time_available` thì lấy `bar_index` lớn hơn) thỏa mãn $\text{time\_available} \le \text{open\_time}[t]$.
  - **Đáy được bảo vệ (`Protected Low`):** Là Swing Low chưa bị phá **gần nhất theo thời gian (`time_available`)** (nếu trùng `time_available` thì lấy `bar_index` lớn hơn) thỏa mãn $\text{time\_available} \le \text{open\_time}[t]$.

### 3.2. Quy tắc phân định rạch ròi giữa INIT_BREAK, BOS và CHoCH
Tại mỗi nến H1 $t$, việc kiểm tra phá vỡ được đối chiếu với mức được bảo vệ tương ứng:
- **INIT_BREAK (Khởi tạo xu hướng):** Phá vỡ mức swing đầu tiên khi thị trường đang ở trạng thái `NEUTRAL`. Sự kiện này **chỉ dùng để xác lập trạng thái xu hướng ban đầu**, TUYỆT ĐỐI KHÔNG dùng làm tín hiệu giao dịch tiếp diễn (BOS).
- **BOS (Break of Structure - Tiếp diễn):** Phá vỡ mức swing **CÙNG CHIỀU** với trạng thái xu hướng hiện tại (`BULLISH` phá đỉnh, `BEARISH` phá đáy).
- **CHoCH (Change of Character - Đảo chiều):** Phá vỡ mức swing **NGƯỢC CHIỀU** với trạng thái xu hướng hiện tại (`BEARISH` phá đỉnh, `BULLISH` phá đáy), lập tức đảo chiều trạng thái xu hướng.

**Bảng ma trận chuyển trạng thái:**

| Trạng thái hiện tại | Điều kiện nến $t$ đóng cửa | Sự kiện kích hoạt | Trạng thái tiếp theo | Ý nghĩa giao dịch |
| :---: | :---: | :---: | :---: | :---: |
| `NEUTRAL` | $Close[t] > \text{Protected High}.\text{price}$ | **`BULLISH_INIT_BREAK`** | Xác lập `BULLISH` | Khởi tạo cấu trúc (Không vào lệnh) |
| `NEUTRAL` | $Close[t] < \text{Protected Low}.\text{price}$ | **`BEARISH_INIT_BREAK`** | Xác lập `BEARISH` | Khởi tạo cấu trúc (Không vào lệnh) |
| `BULLISH` | $Close[t] > \text{Protected High}.\text{price}$ | **`BULLISH_BOS`** | Giữ `BULLISH` | Tín hiệu tiếp diễn tăng |
| `BULLISH` | $Close[t] < \text{Protected Low}.\text{price}$ | **`BEARISH_CHOCH`** | Lật sang `BEARISH` | Tín hiệu đảo chiều giảm |
| `BEARISH` | $Close[t] < \text{Protected Low}.\text{price}$ | **`BEARISH_BOS`** | Giữ `BEARISH` | Tín hiệu tiếp diễn giảm |
| `BEARISH` | $Close[t] > \text{Protected High}.\text{price}$ | **`BULLISH_CHOCH`** | Lật sang `BULLISH` | Tín hiệu đảo chiều tăng |

### 3.3. Quy tắc "Chỉ phá một lần" & Xóa bỏ Swing bị che khuất (Shadowed Swings Elimination)
1. **Chỉ tính lần phá đầu tiên (First Break Only Rule):**  
   Mỗi mức Swing chỉ bị phá vỡ đúng 1 lần. Khi nến $t$ đóng cửa vượt qua mức của Swing được bảo vệ, swing đó lập tức chuyển sang trạng thái `BROKEN` tại $\text{time\_available} = \text{close\_time}[t]$. Các nến tiếp theo $t+1, t+2,\dots$ dù giá đóng cửa vẫn nằm ngoài mức giá đó cũng **KHÔNG ĐƯỢC sinh thêm BOS**.
2. **Quy tắc vô hiệu hóa các swing cũ bị che khuất (Shadowed Swings Elimination Rule):**  
   - Khi một Swing High tại mức giá $P$ bị phá vỡ ($Close[t] > P$):  
     **Mức đó chuyển sang `BROKEN`, ĐỒNG THỜI TOÀN BỘ các Swing High chưa bị phá khác có giá $\le \max(P, Close[t])$ CŨNG LẬP TỨC CHUYỂN SANG `BROKEN`.**
   - Khi một Swing Low tại mức giá $P$ bị phá vỡ ($Close[t] < P$):  
     **Mức đó chuyển sang `BROKEN`, ĐỒNG THỜI TOÀN BỘ các Swing Low chưa bị phá khác có giá $\ge \min(P, Close[t])$ CŨNG LẬP TỨC CHUYỂN SANG `BROKEN`.**

### 3.4. Quy tắc khởi động (Warm-up Period Rule) & Bằng chứng hội tụ thực nghiệm
- **Vấn đề rủi ro khởi động:** Thuật toán cấu trúc thị trường luôn khởi tạo ở trạng thái `NEUTRAL`. Nếu nạp dữ liệu bắt đầu đúng ngày đầu tiên của giai đoạn phân tích (ví dụ `2018-01-01`), những nến đầu tiên hoàn toàn chưa có swing nào khả dụng (do cần tối thiểu $2k+1$ nến và độ trễ xác nhận $k$ nến). Khi mức swing đầu tiên bị phá vỡ, sự kiện đó bị gắn nhãn `INIT_BREAK` và bị bỏ qua không tạo tín hiệu, đồng thời trạng thái xu hướng trong những ngày đầu tháng 01/2018 bị sai lệch so với diễn biến tự nhiên của thị trường. Hơn nữa, danh sách các swing chưa bị phá từ quá khứ quyết định mức nào là Protected Level hiện tại; ký ức cấu trúc có thể kéo dài trên 3 năm (thực nghiệm cho thấy Swing High 1374.99 từ năm 2016 vẫn chi phối đến tận ngày 20/06/2019).
- **Bằng chứng hội tụ thực nghiệm (Warm-up Convergence Proof):**
  Thử nghiệm đối chứng trên dữ liệu H1 thật với các độ dài warm-up khác nhau cho giai đoạn đánh giá 2018–2025 (khi ghép từng sự kiện theo bộ 3 khóa nghiêm ngặt `["time_available", "event_type", "price_broken"]` để không ghép nhầm swing):
  - Khởi động 6 tháng (từ `2017-06-01`): Sinh ra đúng 4.481 sự kiện, ghép trùng khớp 100% cả 4.481 sự kiện với bảng chuẩn (đạt tỷ lệ 4.481/4.486 = 99,889%), thiếu đúng 5 sự kiện (4 sự kiện phá đáy cũ trong tháng 08/2018 và 1 sự kiện phá đỉnh cũ vào tháng 06/2019).
  - Khởi động 12 tháng (từ `2017-01-01`): Sinh ra đúng 4.485 sự kiện, ghép trùng khớp 100% cả 4.485 sự kiện với bảng chuẩn (đạt tỷ lệ 4.485/4.486 = 99,978%), thiếu đúng 1 sự kiện duy nhất vào ngày 20/06/2019 do ảnh hưởng của đỉnh swing 1374.99 từ năm 2016.
  - **Khởi động 24 tháng (từ `2016-01-01`) so với Toàn bộ lịch sử (từ đầu kho DuckDB `2014-06-01`): TRÙNG KHỚP HOÀN HẢO 100% TRÊN MỌI THUỘC TÍNH THỊ TRƯỜNG** trên toàn bộ 4.486 sự kiện cấu trúc của cả giai đoạn 2018–2025 (`time_candle`, `time_available`, `event_type`, `price_broken`, `close_price`, `prior_trend`, `new_trend`). Trạng thái thị trường tại 2018-01-01 hoàn toàn ổn định và độc lập với điểm bắt đầu trước đó.
- **Quy tắc chuẩn hóa chính thức (Mặc định toàn bộ lịch sử):**
  1. **Quy tắc chính:** Với mọi khoảng thời gian đánh giá $[T_{\text{start}}, T_{\text{end}}]$, mặc định **NẠP TOÀN BỘ LỊCH SỬ CÓ TRƯỚC $T_{\text{start}}$** từ đầu kho dữ liệu DuckDB (`warmup_months=None`). Vì việc nạp toàn bộ lịch sử (~100k nến H1 từ năm 2014) chỉ tốn chưa tới 1 giây và phát hiện cấu trúc chỉ tốn ~4 giây, đây là giải pháp 0 bậc tự do hoàn hảo nhất, đảm bảo tính nhất quán tuyệt đối cho cả Development, Robustness và Holdout mà không cần chạm vào dữ liệu để dò tìm thời gian hội tụ.
  2. Thời lượng 24 tháng chỉ là bằng chứng thực nghiệm tối thiểu cho tập Development.
  3. **Chuẩn hóa mốc cắt lọc:** Sau khi tính toán liên tục từ đầu dữ liệu nạp, chỉ giữ lại các sự kiện có $\mathbf{time\_available \ge T_{\text{start}}}$ (sử dụng $time\_available$ thay vì $time\_candle$ để không bỏ sót nến 23:00 của ngày hôm trước vốn đóng cửa và khả dụng đúng 00:00 ngày $T_{\text{start}}$).
  4. Sử dụng hàm wrapper chuẩn hóa **`load_structure(start_date, end_date, warmup_months=None)`** làm giao diện chính thức.
  5. **Lưu ý về định danh cục bộ:** Các cột `bar_index`, `structure_id`, `broken_swing_id` chỉ có ý nghĩa cục bộ trong từng lần nạp. Mọi mô-đun hạ nguồn (FVG, Liquidity sweeps) TUYỆT ĐỐI KHÔNG dùng các ID/index này để liên kết mà phải ghép bảng theo mốc thời gian vật lý (`time_candle`, `time_available`).

---

## 4. Fair Value Gap (FVG) & Nhật ký vòng đời (`src/backtest/smc/fvg.py`)

### 4.1. Định nghĩa hình học 3 nến liên tiếp & Quyết định khoảng trống cuối tuần (Spans Gap Rule)
Xét bộ 3 nến H1 liên tiếp tại chỉ số $[i-2, i-1, i]$:
- **Bullish FVG (Khoảng trống giá tăng):**
  Điều kiện nghiêm ngặt: $Low[i] > High[i-2]$ (ca bằng nhau $Low[i] = High[i-2]$ tuyệt đối KHÔNG phải FVG).
  - **Mép gần (Near edge / Entry threshold):** $\text{top} = Low[i]$. Khi giá quay đầu hồi quy (pullback) giảm từ trên xuống, đây là mức giá đầu tiên chạm vào vùng gap.
  - **Mép xa (Far edge / Invalidation threshold):** $\text{bottom} = High[i-2]$. Nếu giá đóng cửa xuyên thủng mép này, vùng mất cân bằng bị xóa bỏ hoàn toàn.
- **Bearish FVG (Khoảng trống giá giảm):**
  Điều kiện nghiêm ngặt: $High[i] < Low[i-2]$ (ca bằng nhau $High[i] = Low[i-2]$ tuyệt đối KHÔNG phải FVG).
  - **Mép gần (Near edge / Entry threshold):** $\text{bottom} = High[i]$. Khi giá quay đầu hồi quy tăng từ dưới lên, đây là mức giá đầu tiên chạm vào vùng gap.
  - **Mép xa (Far edge / Invalidation threshold):** $\text{top} = Low[i-2]$. Nếu giá đóng cửa vượt lên trên mép này, vùng mất cân bằng bị vô hiệu.
- **Quyết định tiên nghiệm về khoảng trống cuối tuần & giờ nghỉ hàng ngày (Weekend/Holiday & Daily Maintenance Gap Decision - Spec v2.2):**
  - *Bản chất vi mô:* FVG đại diện cho sự mất cân bằng cung-cầu trong phiên đấu giá liên tục (continuous auction orderflow). Bước nhảy mở cửa qua 48h đóng sàn cuối tuần (auction reopening jump) hoặc qua 1 giờ nghỉ kỹ thuật hàng ngày (daily maintenance break, thường vào 21:00–22:00 hoặc 22:00–23:00 UTC theo quy ước của thị trường GOLD) không phải là sự mất cân bằng thanh khoản nội phiên.
  - *Quy tắc thuật toán:* Kiểm tra khoảng cách thời gian giữa 2 hàng liên tiếp trong bộ ba:
    $$\Delta t_1 = time\_utc[i-1] - time\_utc[i-2] \quad , \quad \Delta t_2 = time\_utc[i] - time\_utc[i-1]$$
    Nếu $\Delta t_1 > bar\_duration$ hoặc $\Delta t_2 > bar\_duration$ (với H1 là $> 1\text{ hour}$): Gán cờ **`spans_gap = True`** (bao gồm cả vắt qua cuối tuần, vắt qua giờ nghỉ bảo trì hàng ngày hoặc nến thiếu dữ liệu).
    Nếu bộ 3 nến hoàn toàn liền mạch liên tục ($\Delta t_1 \le 1\text{h}$ và $\Delta t_2 \le 1\text{h}$): Gán cờ **`spans_gap = False`**.
  - *Quy chuẩn áp dụng:* Bảng đầu ra lưu trữ cờ `spans_gap` minh bạch cho từng FVG. Trong quy chuẩn chiến lược SMC PRIMARY, **chỉ các FVG có `spans_gap == False` mới được công nhận là FVG giao dịch hợp lệ**. Các FVG có `spans_gap == True` bị loại khỏi luồng tín hiệu vào lệnh.

### 4.2. Bộ lọc kích thước tối thiểu & Chống rò rỉ Look-Ahead với ATR
Để loại bỏ các vi khoảng trống nhiễu:
$$\text{gap\_size} = |\text{top} - \text{bottom}| \ge \gamma \times ATR(H1, 14)_{[i-1]}$$
- **Quy tắc tuyệt đối chống look-ahead của ATR:**  
  Chỉ số $ATR(H1, 14)$ tại hàng $i$ trong `data.py` vốn được tính từ $High[i], Low[i], Close[i]$. Do đó, giá trị tham chiếu bắt buộc phải lấy ở hàng **$i-1$** ($ATR[i-1]$), tức giá trị ATR đã chốt khi nến $i-1$ đóng cửa, hoàn toàn độc lập với nến $i$. Thay đổi $High[i]$ hay $Low[i]$ của nến $i$ tuyệt đối không làm thay đổi $ATR[i-1]$.
- **Khởi động ATR:** Bỏ qua các nến chưa có giá trị ATR (14 nến đầu tiên, tức các nến có $ATR[i-1] = \text{NaN}$).
- **Tham số:** **PRIMARY $\gamma = 0,15$** (tối thiểu 15% ATR H1). EXPLORATORY: $\gamma = 0,25$. Dấu so sánh là $\ge$ (bằng đúng $\gamma \times ATR$ vẫn được giữ).

### 4.3. Bảng FVG & Nhật ký vòng đời bất biến
Mỗi FVG được ghi nhận trong bảng `fvg_df` với các cột:
- `fvg_id`: int (tăng dần từ 0)
- `direction`: str ('BULLISH' / 'BEARISH')
- `bar_index`: int (chỉ số nến $i$ tạo FVG)
- `time_candle`: pd.Timestamp (giờ mở nến $i$)
- `time_available`: pd.Timestamp (giờ đóng nến $i$: $time\_utc[i] + bar\_duration$)
- `top`: float, `bottom`: float
- `near_edge`: float, `far_edge`: float
- `gap_size`: float, `atr_ref`: float ($ATR[i-1]$)
- `spans_gap`: bool

Nhật ký vòng đời `lifecycle_df` theo dõi trạng thái bằng thuật toán quét tiến $j = i+1, i+2, \dots$:
1. **Sự kiện `CREATED`:** Ghi nhận tại nến $i$ với $\text{time\_available} = \text{close\_time}[i] = time\_utc[i] + bar\_duration$.
   - **Quy tắc nến test:** Nến test bắt buộc có $j > i$. Nến $i$ tuyệt đối **KHÔNG TỰ TEST CHÍNH FVG NÓ TẠO RA**.
2. **Thứ tự kiểm tra nghiêm ngặt tại mỗi nến tương lai $j \ge i+1$:**
   - **Bước 1 — Invalidation (Đóng cửa xuyên mép xa):**
     - Bullish FVG: $Close[j] < \text{bottom}$.
     - Bearish FVG: $Close[j] > \text{top}$.
     - Nếu thỏa: Ghi nhận sự kiện `INVALIDATED` tại nến $j$ với $\text{price\_trigger} = Close[j]$, $\text{time\_available} = \text{close\_time}[j] = time\_utc[j] + bar\_duration$, và **LẬP TỨC DỪNG THEO DÕI FVG NÀY**.
   - **Bước 2 — Touch / Test (Chạm mép gần):**
     - Bullish FVG: $Low[j] \le \text{top}$ (và chưa từng bị ghi TESTED trước đó).
     - Bearish FVG: $High[j] \ge \text{bottom}$ (và chưa từng bị ghi TESTED trước đó).
     - Nếu thỏa: Ghi nhận sự kiện `TESTED` tại nến $j$ với $\text{price\_trigger} = \text{near\_edge}$, $\text{time\_available} = \text{close\_time}[j] = time\_utc[j] + bar\_duration$. **CHỈ GHI TESTED ĐÚNG 1 LẦN DUY NHẤT**. Sau khi TESTED, FVG vẫn tiếp tục ACTIVE theo dõi các nến tiếp theo.
   - **Bước 3 — Expiration (Hết hạn sau $M$ nến):**
     - Khi $j - i \ge M$ và chưa bị `INVALIDATED`:
       - Nếu tại chính nến $j = i+M$ vừa ghi nhận `TESTED` (chạm mép gần ở nến thứ $M$): `TESTED` được ghi tại nến $i+M$, và `EXPIRED` sẽ được ghi nhận tại nến kế tiếp **$i+M+1$** (với $\text{time\_available} = \text{close\_time}[i+M+1]$, nếu nến này có dữ liệu và **chưa bị `INVALIDATED` ở nến đó**).
       - Trong mọi trường hợp khác ($j = i+M$ không chạm, hoặc đã TESTED từ nến trước đó): Ghi nhận `EXPIRED` tại nến **$i+M$** với $\text{time\_available} = \text{close\_time}[i+M]$ (nếu chưa bị `INVALIDATED` ở nến đó), sau đó dừng theo dõi FVG này.
3. **Thứ tự ưu tiên khi xung đột cùng nến:**
   $$\mathbf{INVALIDATED} > \mathbf{TESTED} > \mathbf{EXPIRED}$$
   Nếu một nến vừa chạm mép gần vừa đóng cửa xuyên mép xa $\implies$ Chỉ ghi nhận duy nhất `INVALIDATED`.
4. **Tham số vòng đời:** **PRIMARY $M = 24$ nến** (1 ngày giao dịch). EXPLORATORY: $M = 48$ nến. Độ phức tạp $O(N_{\text{FVG}} \times M)$ tối ưu, duyệt tiến siêu tốc.
5. **Sắp xếp thời gian ổn định (Chronological Order):**
   Bảng `lifecycle_df` được sắp xếp ổn định theo `time_available` (thời điểm khả dụng), tiếp đến `fvg_id`, rồi thứ tự ưu tiên sự kiện (`CREATED` -> `INVALIDATED` -> `TESTED` -> `EXPIRED`). Cột `lifecycle_id` được gán tuần tự từ 0 sau khi hoàn tất sắp xếp, đảm bảo người dùng có thể duyệt sự kiện xuôi theo dòng thời gian mà không bị xáo trộn.
6. **Bảo toàn FVG warm-up còn hiệu lực (`load_fvg`):**
   Để duy trì tính liên tục của dữ liệu và tuân thủ nguyên tắc warm-up như cấu trúc thị trường, `load_fvg(start_date, ...)` bảo toàn mọi FVG chưa kết thúc trước `start_date` (tức các FVG chưa bị `INVALIDATED` hay `EXPIRED` trước mốc đó, bao gồm cả dòng `CREATED` và chuỗi sự kiện của chúng), và chỉ lọc bỏ các FVG đã kết thúc hoàn toàn trước `start_date`. Với tập Development 2018–2025, có 4 FVG tạo cuối ngày 29/12/2017 còn hiệu lực lúc 00:00 ngày 01/01/2018 được bảo toàn, nâng tổng số FVG từ 5.774 lên 5.778 FVG.

---

## 5. Pool thanh khoản & Quét thanh khoản (Liquidity Pools & Sweeps) (`src/backtest/smc/liquidity.py`)

### 5.1. Các nguồn Pool thanh khoản
1. **Swing High / Swing Low:** Các đỉnh/đáy đã xác nhận ($\text{time\_available} \le t$).
2. **Equal Highs / Equal Lows (EQH / EQL) & Quy tắc Hấp thụ (Absorption Rule):**
   - **Định nghĩa "lân cận" chính xác:** Hai Swing High (cho EQH) hoặc hai Swing Low (cho EQL) liên tiếp cùng loại thỏa mãn 3 điều kiện:
     1. *Khoảng cách nến:* $j_2 - j_1 \le N_{\text{bars}}$ (PRIMARY: $N_{\text{bars}} = 24$ nến = 1 ngày giao dịch; EXPLORATORY: $48$ nến).
     2. *Không bị vi phạm:* Không có nến nào nằm giữa $j_1$ và $j_2$ đóng cửa vượt lên trên đỉnh 1 (với EQH) hoặc xuyên dưới đáy 1 (với EQL).
     3. *Biên độ giá bằng nhau:* $|Price_1 - Price_2| \le \varepsilon \times ATR(H1, 14)_{[j_2]}$ (PRIMARY: $\varepsilon = 0,08$; EXPLORATORY: $\varepsilon = 0,05$).
   - **Mức giá của Pool:**
     - $Price_{\text{EQH}} = \max(High_1, High_2)$ (lấy đỉnh cao hơn).
     - $Price_{\text{EQL}} = \min(Low_1, Low_2)$ (lấy đáy thấp hơn).
   - **Thời điểm khả dụng:** $\text{time\_available} = \text{time\_available}(\text{swing}_2)$.
   - **Quy tắc Hấp thụ (Absorption Rule - BẮT BUỘC):**  
     Khi Pool EQH (hoặc EQL) được kích hoạt tại $\text{time\_available}(\text{swing}_2)$, hai mức Swing đơn lẻ cấu thành ($\text{swing}_1$ và $\text{swing}_2$) **LẬP TỨC BỊ HẤP THỤ VÀ VÔ HIỆU HÓA (Absorbed / Deactivated)**. Hệ thống chỉ theo dõi duy nhất mức giá của Pool EQH/EQL, loại bỏ hoàn toàn nguy cơ một cú sweep bị đếm lặp 2–3 lần.
3. **Đỉnh/Đáy ngày hôm trước (Previous Day High/Low - PDH/PDL):**
   - **Quy chuẩn cắt ngày theo giờ New York (`America/New_York`):**
     Sử dụng mốc đóng cửa thị trường New York lúc **17:00 NY** làm thời điểm chốt ngày giao dịch (xử lý giờ mùa hè DST tự động bằng `zoneinfo`):
     - Mùa đông (EST, UTC-5): 17:00 NY tương ứng **22:00 UTC** (nến H1 đóng lúc 22:00 UTC).
     - Mùa hè (EDT, UTC-4): 17:00 NY tương ứng **21:00 UTC** (nến H1 đóng lúc 21:00 UTC).
   - PDH và PDL khả dụng ngay khi nến H1 cuối ngày đóng cửa (21:00 UTC mùa hè, 22:00 UTC mùa đông).
4. **Biên vùng Á (Asian High / Asian Low):** Khả dụng từ 07:00 UTC (sau khi phiên Á 00:00–07:00 UTC kết thúc).

### 5.2. Định nghĩa Quét thanh khoản & Quy tắc "Nến đầu tiên quyết định số phận của Pool"
Nến đầu tiên $t$ có giá vi phạm mức của Pool ($High[t] > \text{Pool}_{\text{High}}$ hoặc $Low[t] < \text{Pool}_{\text{Low}}$) sẽ **QUYẾT ĐỊNH DỨT ĐIỂM SỐ PHẬN CỦA POOL**:
- **Trường hợp 1 — Quét thanh khoản (Liquidity Sweep):**
  - High Sweep: $High[t] > \text{Pool}_{\text{High}}$ VÀ $Close[t] \le \text{Pool}_{\text{High}}$.
  - Low Sweep: $Low[t] < \text{Pool}_{\text{Low}}$ VÀ $Close[t] \ge \text{Pool}_{\text{Low}}$.
  - Phát ra sự kiện Sweep tại $\text{time\_available} = \text{close\_time}[t]$.
  - Pool lập tức chuyển sang trạng thái `SWEPT` và **HẾT HIỆU LỰC (Inactive)** vĩnh viễn.
- **Trường hợp 2 — Phá vỡ (Breakout):**
  - High Breakout: $Close[t] > \text{Pool}_{\text{High}}$.
  - Low Breakout: $Close[t] < \text{Pool}_{\text{Low}}$.
  - Pool lập tức chuyển sang trạng thái `BROKEN` và **HẾT HIỆU LỰC (Inactive)** vĩnh viễn.
- **Hệ quả cốt lõi:** Dù là `SWEPT` hay `BROKEN`, pool thanh khoản đó không bao giờ được phép sinh thêm bất kỳ sự kiện sweep nào sau này.

---

## 6. Quyết định loại bỏ Order Block (OB) khỏi thiết kế ban đầu
- **Lý do:** Khái niệm Order Block (cây nến ngược màu cuối cùng trước đợt sóng mạnh) mang tính chủ quan cao, có quá nhiều biến thể tùy biến (thân nến, râu nến, cụm nến) làm tăng mạnh số bậc tự do không cần thiết.
- **Quyết định:** Thiết kế SMC ban đầu tập trung hoàn toàn vào bộ ba khách quan: **Liquidity Sweep + FVG + Market Structure (BOS/CHoCH)**, loại bỏ hoàn toàn Order Block nhằm giữ tính chặt chẽ và giảm thiểu nguy cơ overfitting.

---

## 7. Ràng buộc chi phí đối với cấu trúc SMC (Cost Floor Rule)

### 7.1. Phân tích chi phí ma sát động theo năm (Quy ước C3)
Chi phí ma sát vòng đời 1 lệnh thay đổi theo từng năm theo quy ước C3 của `PROTOCOL.md`:
$$\text{friction}_{\text{year}} = \text{spread}_{\text{year}} \times 1,25 + 2 \times \text{slip}$$
- Với $\text{slip} = 0,05$ USD/lượt ($2 \times \text{slip} = 0,10$ USD).

### 7.2. Ràng buộc khoảng cách Stop Loss ($R_{dist}$)
Để tỷ lệ chi phí rủi ro không vượt quá mục tiêu chuẩn mực $\le \mathbf{0,08R}$ (tương thích với Baseline):
$$\text{cost\_R} = \frac{\text{friction}_{\text{year}}}{R_{dist}} \le 0,08 \implies R_{dist} \ge \mathbf{12,5 \times (\text{spread}_{\text{year}} \times 1,25 + 2 \times \text{slip})}$$

- **Công thức ràng buộc chi phí chính thức:**
  $$R_{dist} \ge \max\Big(\alpha_{ATR} \times ATR(H1, 14)_{[t-1]}, \; 12,5 \times (\text{spread}_{\text{year}} \times 1,25 + 2 \times \text{slip})\Big)$$
- **Trần chi phí tối đa (Hard Cap):** $\text{cost\_R} \le \mathbf{0,10R}$ trong mọi lệnh.
- **Lưu ý định lượng quan trọng về rào cản lợi thế:**  
  Với mức chi phí $0,08$R, để đạt tiêu chí phê duyệt kỳ vọng ròng $\ge +0,05$R mỗi lệnh, chiến lược SMC cần tạo ra **kỳ vọng gộp $\ge \mathbf{0,13R}$** (trong khi baseline chỉ cho kỳ vọng gộp khoảng $0,08$R để đạt $0,02$R ròng). Đây là một ngưỡng kỳ vọng cực kỳ cao, do đó nhóm nghiên cứu cần chuẩn bị sẵn kịch bản thiết kế SMC đầu tiên có thể không vượt qua (FAIL) bài kiểm tra này.

---

## 8. Bảng phân định tham số: PRIMARY (Đóng băng) vs EXPLORATORY

| Ký hiệu | Tham số | Ý nghĩa toán học | Giá trị PRIMARY (ĐÓNG BĂNG) | Giá trị EXPLORATORY |
| :---: | :--- | :--- | :---: | :---: |
| $k$ | Swing Window | Số nến lân cận mỗi bên phát hiện Swing | **3** | 5 |
| $\gamma$ | FVG Min Size | Kích thước tối thiểu FVG theo $ATR(H1, 14)_{[i-1]}$ | **0,15** | 0,25 |
| $M$ | FVG Lifespan | Số nến tồn tại tối đa của FVG trước khi hết hạn | **24** | 48 |
| $\varepsilon$ | EQH Tolerance | Dung sai chênh lệch giá EQH/EQL theo ATR | **0,08** | 0,05 |
| $N_{\text{bars}}$ | EQH Distance | Khoảng cách nến tối đa giữa 2 đỉnh/đáy EQH | **24** | 48 |
| $\alpha_{ATR}$ | ATR Floor Mult | Hệ số chặn dưới khoảng cách SL theo ATR H1 | **0,50** | 0,75 |
| $C_{\text{mult}}$ | Cost Multiplier | Hệ số khoảng cách SL tối thiểu theo phí ma sát | **12,5** (chi phí $\le 0,08$R) | 10,0 (chi phí $\le 0,10$R) |

*Nguyên tắc:* Mọi bước lập trình, kiểm thử và thiết kế ban đầu CHỈ ĐƯỢC PHÉP SỬ DỤNG cấu hình **PRIMARY**. Các giá trị EXPLORATORY chỉ được dùng để phân tích độ nhạy (sensitivity analysis) sau khi đã đăng ký và chốt kết quả PRIMARY.

---

## 9. Đăng ký quy tắc thoát lệnh SMC & Kiểm soát ngẫu nhiên ($N_{\text{trials}} = 2$)

Khi hoàn thiện các thành phần và đăng ký thiết kế chiến lược SMC đầu tiên:
- Số thứ tự thử nghiệm thật: **$N_{\text{trials}} = 2$**.
- Ngưỡng $p$-value kiểm soát ngẫu nhiên hiệu chỉnh Bonferroni:
  $$p_{\text{threshold}} = \frac{0,05}{2} = \mathbf{0,0250}$$
- **Quy tắc thoát lệnh SMC đăng ký trước (SMC Exit Rules):**
  - **Điểm vào lệnh (`entry_open`):** Giá mở nến H1 kế tiếp ngay sau khi tín hiệu xác nhận ($\text{time\_available}$).
  - **Khoảng cách Stop Loss ($d$):** Xác định theo mức cực trị của cấu trúc kỹ thuật (ngoài râu nến sweep hoặc ngoài biên xa FVG), đồng thời thỏa mãn ràng buộc sàn chi phí:
    $$d \ge \max\Big(\alpha_{ATR} \times ATR(H1, 14)_{[t-1]}, \; 12,5 \times \text{friction}_{\text{year}}\Big)$$
  - **Chốt lời (Take Profit):** Cố định theo tỷ lệ $1:2$ R:R:
    $$\text{TP}_{\text{BUY}} = \text{entry\_open} + 2d \quad ; \quad \text{TP}_{\text{SELL}} = \text{entry\_open} - 2d$$
- **Mô hình kiểm soát ngẫu nhiên chuẩn hóa (tương thích C4):**
  - Với mỗi lệnh SMC thật: giữ nguyên `entry_time`, cùng giá mở `entry_open`, cùng khoảng cách dừng lỗ $d$.
  - Nếu lệnh thật là BUY $\implies$ đối chứng ngẫu nhiên có thể là SELL với $\text{SL} = \text{entry\_open} + d$, $\text{TP} = \text{entry\_open} - 2d$, cùng chi phí ma sát theo năm.
  - Nếu lệnh thật là SELL $\implies$ đối chứng ngẫu nhiên có thể là BUY với $\text{SL} = \text{entry\_open} - d$, $\text{TP} = \text{entry\_open} + 2d$, cùng chi phí ma sát theo năm.
  - Rút ngẫu nhiên BUY hoặc SELL với xác suất 0,5 cho từng lệnh qua 1.000 lần lặp với seed cố định.
  - $p$-value: $p = (1 + \sum(\text{EV}_{\text{rand}} \ge \text{EV}_{\text{real}})) / 1001$.
- Giữ nguyên các tiêu chí phê duyệt: Tổng số lệnh $\ge 800$, Kỳ vọng ròng $\ge +0,05$R, Profit Factor $\ge 1,10$, $\ge 5/8$ năm dương lịch.
- Tính toán Deflated Sharpe Ratio (DSR) với $N = 2$.

---

## 10. Bộ tiêu chuẩn kiểm thử bắt buộc & Thống kê tần suất sự kiện

### 10.1. Bộ tiêu chuẩn kiểm thử
1. **Test bất biến theo tiền tố (Prefix Invariance Test - BẮT BUỘC):** Cắt theo hàng bảng H1, khớp 100% từng thuộc tính.
2. **Test các ca tính tay (Hand-crafted synthetic tests):** Ca hòa bị loại, nến ngoài sinh cả 2 swing, bước qua cuối tuần, FVG xung đột mép, swing cũ bị che khuất.
3. **Cảnh báo bẫy tinh chỉnh (The Tuning Trap):** Tuyệt đối không nới lỏng tham số để ép đủ 800 lệnh.
4. **Kiểm tra trực quan bằng biểu đồ:** Xem xét trên HTML Plotly với hover tooltip không chồng chữ.

### 10.2. Bảng thống kê số lượng Swing theo năm trên H1 Development (2018–2025)
Thống kê thực nghiệm từ 44.224 nến H1 thuần của tập Development (`load_h1("2018-01-01", "2025-06-30")` không kèm warm-up):

| Năm | k=3 High | k=3 Low | Tổng k=3 (PRIMARY) | k=5 High | k=5 Low | Tổng k=5 (EXPLORATORY) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **2018** | 551 | 563 | **1.114** | 352 | 347 | **699** |
| **2019** | 581 | 584 | **1.165** | 370 | 377 | **747** |
| **2020** | 575 | 596 | **1.171** | 361 | 382 | **743** |
| **2021** | 574 | 573 | **1.147** | 350 | 371 | **721** |
| **2022** | 566 | 575 | **1.141** | 351 | 359 | **710** |
| **2023** | 560 | 568 | **1.128** | 362 | 358 | **720** |
| **2024** | 572 | 573 | **1.145** | 367 | 364 | **731** |
| **2025 (H1)** | 269 | 281 | **550** | 174 | 183 | **357** |
| **TỔNG CỘNG** | **4.248** | **4.313** | **8.561** | **2.687** | **2.741** | **5.428** |

*Nhận xét:* Tần suất xuất hiện swing ổn định qua từng năm (~1.140 swing/năm đối với $k=3$, ~725 swing/năm đối với $k=5$). Tỷ lệ ca hòa trên dữ liệu giá vàng thật chỉ chiếm $1,73\%$. Số lượng swing là dồi dào, tuy nhiên số lượng lệnh SMC thực tế sẽ phụ thuộc vào các điều kiện hợp lưu và bộ lọc tiếp theo (FVG, Sweeps, và đặc biệt là ràng buộc sàn chi phí $R_{dist}$).

### 10.3. Bảng phân bố sự kiện cấu trúc theo năm trên H1 Development (2018–2025)
Thống kê thực nghiệm từ 4.486 sự kiện cấu trúc (`load_structure("2018-01-01", "2025-06-30", warmup_months=None, k=3)`):

| Năm | BEARISH_BOS | BEARISH_CHOCH | BULLISH_BOS | BULLISH_CHOCH | Tổng sự kiện cấu trúc |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **2018** | 204 | 106 | 192 | 106 | **608** |
| **2019** | 166 | 117 | 221 | 117 | **621** |
| **2020** | 155 | 94 | 227 | 94 | **570** |
| **2021** | 170 | 105 | 193 | 105 | **573** |
| **2022** | 198 | 106 | 216 | 106 | **626** |
| **2023** | 190 | 90 | 221 | 89 | **590** |
| **2024** | 168 | 103 | 230 | 104 | **605** |
| **2025 (H1)** | 71 | 52 | 118 | 52 | **293** |
| **TỔNG CỘNG** | **1.322** | **773** | **1.618** | **773** | **4.486** |

*Đặc tính định lượng & 5 Bất biến vật lý đã được chứng minh 100%:*
1. **Không có bất kỳ sự kiện INIT_BREAK nào:** Quy tắc warm-up mặc định nạp toàn bộ lịch sử (`warmup_months=None`) đã loại bỏ hoàn toàn sự kiện khởi tạo nhân tạo; xu hướng mở đầu năm 2018 là `BULLISH` tự nhiên đã được kiểm chứng thực nghiệm (kế thừa trực tiếp từ sự kiện `BULLISH_BOS` lúc 17:00 UTC ngày 29/12/2017 phá đỉnh 1305.85, đóng cửa 1305.99; và mở đầu năm 2018 bằng `BULLISH_BOS` phá đỉnh 1307.41 lúc 07:00 ngày 02/01/2018).
2. **Tính đối xứng luân phiên hoàn hảo của CHoCH:** $N_{\text{BULLISH\_CHOCH}} = 773$ và $N_{\text{BEARISH\_CHOCH}} = 773$ bằng nhau tuyệt đối qua toàn bộ 7.5 năm. Đây là hệ quả cấu trúc logic của việc đảo chiều trạng thái nhị phân.
3. **Tỷ lệ Tiếp diễn trên Đảo chiều:** Tỷ lệ $\frac{\text{BOS}}{\text{CHoCH}} \approx \frac{2.940}{1.546} \approx 1,90$ (khoảng 1,8–2,2 BOS cho mỗi lần đổi chiều).
4. **Nhịp độ đổi xu hướng:** Trung bình ~206 lần đổi chiều/năm (~28 nến H1 mỗi chế độ xu hướng).
5. **Khớp 5 bất biến vật lý:** Tối đa 1 sự kiện/nến; tính liên tục Markov ($prior\_trend_{n+1} = new\_trend_n$); First Break Only (không trùng $broken\_swing\_id$); Zero Look-ahead ($time\_available \le time\_candle$); Giá đóng cửa vượt đúng phía.

