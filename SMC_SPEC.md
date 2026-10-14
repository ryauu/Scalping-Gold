# SMC_SPEC.md — Đặc tả kỹ thuật các thành phần Smart Money Concepts (SMC)

**Trạng thái: BẢN ĐẶC TẢ ĐÃ HOÀN TẤT & ĐÓNG BĂNG (Tag: `smc-spec-v1`)**  
Tuân thủ nguyên tắc nghiên cứu định lượng:
- **Nguyên tắc 1 (Không tiêu tốn thử nghiệm):** Không tính P&L khi xây dựng các thành phần. Chỉ kiểm tra tính đúng đắn toán học, tính bất biến theo tiền tố, hình dạng hình học, đếm tần suất xuất hiện và đối chiếu trực quan bằng mắt. Tuyệt đối không tiêu tốn số lượt thử nghiệm ($N_{\text{trials}}$) và không làm bẩn lớp Development.
- **Nguyên tắc 2 (Chống Look-Ahead tuyệt đối):** Mọi sự kiện phát hiện được BẮT BUỘC phải trả về thuộc tính `time_available` (thời điểm nến cuối cùng cần thiết để xác nhận sự kiện đóng cửa hoàn toàn). Thuật toán giao dịch tại thời điểm $t$ chỉ được sử dụng các sự kiện có $\text{time\_available} \le t$.
- **Nguyên tắc 3 (Đóng băng tham số & Chống bẫy tinh chỉnh):** Toàn bộ tham số cấu hình PRIMARY phải được **đóng băng trước khi đếm sự kiện**. Không được phép thay đổi hay nới lỏng tham số để ép mẫu đạt đủ $\ge 800$ lệnh. Nếu thiết kế không đủ số lệnh khả thi, kết luận thiết kế không đạt, hoặc đăng ký thiết kế mới với số thứ tự thử nghiệm tăng lên ($N_{\text{trials}} + 1$).

---

## 1. Khung thời gian & Dữ liệu chuẩn
- **Khung thời gian cấu trúc chính:** **H1** (nến 1 giờ UTC). Được tổng hợp từ dữ liệu M1 sạch trong `data/meta.duckdb` (view `h1_utc`), kèm chỉ báo biến động $ATR(H1, 14)$ tính theo chuẩn Wilder không look-ahead.
- **Quy ước nhãn thời gian H1:** Nhãn `time_utc` của nến H1 là giờ **MỞ** nến (ví dụ `08:00:00` là nến chạy từ `08:00:00` đến `08:59:59`).
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
- **Bảo vệ chống NaN:** Dữ liệu đầu vào `time_utc`, `high`, `low` bắt buộc không được chứa giá trị NaN/NaT. Mô-đun assert và raise `ValueError` ngay lập tức nếu phát hiện NaN nhằm ngăn chặn việc so sánh `<= NaN` trả về False sai lệch.

### 2.2. Thời điểm khả dụng (`time_available`)
- Nến $i$ là đỉnh/đáy tiềm năng, nhưng chỉ được **xác nhận hoàn toàn** khi nến $i + k$ đã đóng cửa.
- Công thức:
  $$\text{time\_available}(i) = \text{close\_time}(i + k) = \text{open\_time}(i + k) + \text{bar\_duration}$$
- **Trường hợp bước qua khoảng trống (Gaps / Weekend):**
  Thời điểm khả dụng lấy theo thời điểm đóng cửa thực tế của nến thứ $i+k$ trong dữ liệu.  
  *Ví dụ:* Nếu nến $i$ là nến Thứ Sáu 21:00 UTC (nến cuối tuần) với $k=2$, thì nến $i+1$ là Chủ Nhật 23:00 UTC, nến $i+2$ là Thứ Hai 00:00 UTC. Swing chỉ khả dụng khi nến Thứ Hai 00:00 UTC đóng cửa (tức Thứ Hai 01:00 UTC). Tuyệt đối không cộng thời gian cơ học theo lịch.

### 2.3. Cấu trúc dữ liệu đầu ra của Swing
- `swing_id`: Số nguyên tăng dần từ 0.
- `swing_type`: `'HIGH'` hoặc `'LOW'`.
- `bar_index`: Vị trí nến $i$ tạo đỉnh/đáy.
- `time_candle`: Thời gian mở nến $i$ (`time_utc[i]`).
- `time_available`: Thời điểm đóng nến $i + k$ (`time_utc[i + k] + bar_duration`).
- `price`: Mức giá đỉnh ($High[i]$) hoặc đáy ($Low[i]$).
- `k`: Tham số lân cận đã dùng.

---

## 3. Cấu trúc thị trường: BOS & CHoCH (`src/backtest/smc/structure.py`)

### 3.1. Trạng thái cấu trúc & Mức được bảo vệ (Protected Level)
Để thuật toán vận hành hoàn toàn khách quan, không chứa bậc tự do ẩn:
- **Trạng thái cấu trúc thị trường (Market Trend State):**
  - `BULLISH`: Thị trường đang trong cấu trúc tăng.
  - `BEARISH`: Thị trường đang trong cấu trúc giảm.
  - `NEUTRAL`: Trạng thái khởi tạo ban đầu khi chưa ghi nhận bất kỳ sự kiện phá vỡ cấu trúc nào.
- **Định nghĩa Mức được bảo vệ (Protected Level - 0 bậc tự do):**
  - **Đỉnh được bảo vệ (`Protected High`):** Là Swing High chưa bị phá gần nhất (`last unbroken Swing High`) có $\text{time\_available} \le \text{open\_time}[t]$.
  - **Đáy được bảo vệ (`Protected Low`):** Là Swing Low chưa bị phá gần nhất (`last unbroken Swing Low`) có $\text{time\_available} \le \text{open\_time}[t]$.

### 3.2. Quy tắc phân định rạch ròi giữa BOS và CHoCH
Tại mỗi nến H1 $t$, việc kiểm tra phá vỡ được đối chiếu với mức được bảo vệ tương ứng:
- **BOS (Break of Structure):** Là sự kiện phá vỡ **CÙNG CHIỀU** với trạng thái xu hướng hiện tại (hoặc xác lập xu hướng đầu tiên từ `NEUTRAL`).
- **CHoCH (Change of Character):** Là sự kiện phá vỡ **NGƯỢC CHIỀU** với trạng thái xu hướng hiện tại.

**Bảng ma trận chuyển trạng thái:**

| Trạng thái hiện tại | Điều kiện nến $t$ đóng cửa | Sự kiện kích hoạt | Trạng thái tiếp theo |
| :---: | :---: | :---: | :---: |
| `BULLISH` | $Close[t] > \text{Protected High}.\text{price}$ | **Bullish BOS** (Tiếp diễn tăng) | Giữ nguyên `BULLISH` |
| `BULLISH` | $Close[t] < \text{Protected Low}.\text{price}$ | **Bearish CHoCH** (Đảo chiều giảm) | Lật sang `BEARISH` |
| `BEARISH` | $Close[t] < \text{Protected Low}.\text{price}$ | **Bearish BOS** (Tiếp diễn giảm) | Giữ nguyên `BEARISH` |
| `BEARISH` | $Close[t] > \text{Protected High}.\text{price}$ | **Bullish CHoCH** (Đảo chiều tăng) | Lật sang `BULLISH` |
| `NEUTRAL` | $Close[t] > \text{Protected High}.\text{price}$ | **Bullish BOS** (Khởi tạo xu hướng tăng) | Xác lập `BULLISH` |
| `NEUTRAL` | $Close[t] < \text{Protected Low}.\text{price}$ | **Bearish BOS** (Khởi tạo xu hướng giảm) | Xác lập `BEARISH` |

*Lưu ý:* Khi trạng thái là `NEUTRAL`, tuyệt đối **KHÔNG** sinh ra CHoCH vì chưa có trạng thái trước đó để đảo chiều.

### 3.3. Quy tắc "Chỉ phá một lần" & Xử lý lỗ hổng "Swing cũ bị che khuất"
1. **Chỉ tính lần phá đầu tiên (First Break Only Rule):**  
   Mỗi mức Swing chỉ bị phá vỡ đúng 1 lần. Khi nến $t$ đóng cửa vượt qua mức của Swing được bảo vệ, swing đó lập tức chuyển sang trạng thái `BROKEN` tại $\text{time\_available} = \text{close\_time}[t]$. Các nến tiếp theo $t+1, t+2,\dots$ dù giá đóng cửa vẫn nằm ngoài mức giá đó cũng **KHÔNG ĐƯỢC sinh thêm BOS**.
2. **Quy tắc vô hiệu hóa các swing cũ bị che khuất (Shadowed Swings Elimination Rule):**  
   *Nguy cơ:* Giả sử Swing High cũ tại 100 chưa bị phá, một Swing High mới hình thành tại 110. Giá đóng cửa tại 105 không sinh BOS (tham chiếu là 110). Khi nến sau phá 110 và đánh dấu 110 là `BROKEN`, nếu mức 100 vẫn còn `UNBROKEN` thì nó sẽ lộ ra làm tham chiếu cho nến kế tiếp đóng ở 108 và sinh ra BOS giả.  
   *Quy tắc chuẩn hóa bắt buộc:*
   - Khi một Swing High tại mức giá $P$ bị phá vỡ ($Close[t] > P$):  
     **Mức đó chuyển sang `BROKEN`, ĐỒNG THỜI TOÀN BỘ các Swing High chưa bị phá khác có giá $\le P$ CŨNG LẬP TỨC CHUYỂN SANG `BROKEN`.**
   - Khi một Swing Low tại mức giá $P$ bị phá vỡ ($Close[t] < P$):  
     **Mức đó chuyển sang `BROKEN`, ĐỒNG THỜI TOÀN BỘ các Swing Low chưa bị phá khác có giá $\ge P$ CŨNG LẬP TỨC CHUYỂN SANG `BROKEN`.**

---

## 4. Fair Value Gap (FVG) & Nhật ký vòng đời (`src/backtest/smc/fvg.py`)

### 4.1. Định nghĩa hình học 3 nến liên tiếp & Quy ước mép
Xét bộ 3 nến H1 liên tiếp tại chỉ số $[i-2, i-1, i]$:
- **Bullish FVG (Khoảng trống giá tăng):**
  Điều kiện: $Low[i] > High[i-2]$.
  - **Mép gần (Near edge / Entry threshold):** $\text{top} = Low[i]$. Khi giá quay đầu hồi quy (pullback) giảm từ trên xuống, đây là mức giá đầu tiên chạm vào vùng gap.
  - **Mép xa (Far edge / Invalidation threshold):** $\text{bottom} = High[i-2]$. Nếu giá đóng cửa xuyên thủng mép này, vùng mất cân bằng bị xóa bỏ hoàn toàn.
- **Bearish FVG (Khoảng trống giá giảm):**
  Điều kiện: $High[i] < Low[i-2]$.
  - **Mép gần (Near edge / Entry threshold):** $\text{bottom} = High[i]$. Khi giá quay đầu hồi quy tăng từ dưới lên, đây là mức giá đầu tiên chạm vào vùng gap.
  - **Mép xa (Far edge / Invalidation threshold):** $\text{top} = Low[i-2]$. Nếu giá đóng cửa vượt lên trên mép này, vùng mất cân bằng bị vô hiệu.

### 4.2. Bộ lọc kích thước tối thiểu
Để loại bỏ các vi khoảng trống nhiễu (vài cents):
$$\text{gap\_size} = |\text{top} - \text{bottom}| \ge \gamma \times ATR(H1, 14)_{[i-1]}$$
- **Quy tắc không look-ahead của ATR:** $ATR(H1, 14)_{[i-1]}$ bắt buộc là giá trị ATR tính đến hết nến $i-1$ (tại thời điểm đóng nến $i-1$, hoàn toàn thuộc về quá khứ, không chứa dữ liệu nến $i$).
- **Tham số:** **PRIMARY $\gamma = 0,15$** (tối thiểu 15% ATR H1). EXPLORATORY: $\gamma = 0,25$.

### 4.3. Nhật ký vòng đời bất biến (Immutable Lifecycle Log)
FVG không được ghi đè trạng thái tại chỗ mà lưu vết dưới dạng Event Log bất biến:
1. `CREATED`: Được tạo khi nến $i$ đóng cửa ($\text{time\_available} = \text{close\_time}[i]$).
   - **Quy tắc nến test:** Nến test bắt buộc phải có chỉ số $j > i$. **Nến $i$ (nến hoàn thành tạo FVG) tuyệt đối KHÔNG ĐƯỢC tính là nến test FVG do chính nó tạo ra.**
2. `TESTED` (Chạm mép gần): Nến $j > i$ có râu chạm vào mép gần nhưng giá đóng cửa chưa xuyên thủng mép xa:
   - Bullish FVG: $Low[j] \le \text{top}$ và $Close[j] \ge \text{bottom}$.
   - Bearish FVG: $High[j] \ge \text{bottom}$ và $Close[j] \le \text{top}$.
   - **Hiệu lực sau khi TESTED:** FVG ở trạng thái `TESTED` **VẪN CÒN HIỆU LỰC (active)** cho các tín hiệu vào lệnh (ví dụ tìm phản ứng bật lại từ FVG), cho đến khi bị `INVALIDATED` hoặc `EXPIRED`.
3. `INVALIDATED` (Vô hiệu hóa): Nến $j > i$ có giá ĐÓNG CỬA xuyên thủng mép xa:
   - Bullish FVG: $Close[j] < \text{bottom}$.
   - Bearish FVG: $Close[j] > \text{top}$.
   - FVG chuyển sang trạng thái kết thúc (inactive), không còn hiệu lực.
4. **Xử lý xung đột cùng nến (Chạm mép gần & Đóng xuyên mép xa):**
   Nếu trong cùng một nến $j > i$, giá vừa chạm mép gần vừa đóng cửa xuyên qua mép xa:
   Hệ thống ghi nhận trạng thái dứt điểm `INVALIDATED` tại nến $j$ với $\text{time\_available} = \text{close\_time}[j]$. FVG bị vô hiệu hóa ngay lập tức, không được giữ lại ở trạng thái active.
5. `EXPIRED` (Hết hạn): Áp dụng cho cả FVG chưa test lẫn FVG đã `TESTED` nếu thời gian tồn tại vượt quá $M$ nến H1 ($j - i \ge M$) mà chưa bị `INVALIDATED`.
   - **Tham số:** **PRIMARY $M = 24$ nến** (1 ngày giao dịch). EXPLORATORY: $M = 48$ nến.

---

## 5. Pool thanh khoản & Quét thanh khoản (Liquidity Pools & Sweeps) (`src/backtest/smc/liquidity.py`)

### 5.1. Các nguồn Pool thanh khoản
1. **Swing High / Swing Low:** Các đỉnh/đáy đã xác nhận ($\text{time\_available} \le t$).
2. **Equal Highs / Equal Lows (EQH / EQL):**
   - **Định nghĩa "lân cận" chính xác:** Hai Swing High (cho EQH) hoặc hai Swing Low (cho EQL) liên tiếp cùng loại thỏa mãn 3 điều kiện:
     1. *Khoảng cách nến:* $j_2 - j_1 \le N_{\text{bars}}$ (PRIMARY: $N_{\text{bars}} = 24$ nến = 1 ngày giao dịch; EXPLORATORY: $48$ nến).
     2. *Không bị vi phạm:* Không có nến nào nằm giữa $j_1$ và $j_2$ đóng cửa vượt lên trên đỉnh 1 (với EQH) hoặc xuyên dưới đáy 1 (với EQL).
     3. *Biên độ giá bằng nhau:* $|Price_1 - Price_2| \le \varepsilon \times ATR(H1, 14)_{[j_2]}$.  
        (PRIMARY: $\varepsilon = 0,08$; EXPLORATORY: $\varepsilon = 0,05$).
   - **Mức giá của Pool:**
     - $Price_{\text{EQH}} = \max(High_1, High_2)$ (lấy đỉnh cao hơn).
     - $Price_{\text{EQL}} = \min(Low_1, Low_2)$ (lấy đáy thấp hơn).
   - **Thời điểm khả dụng:** $\text{time\_available} = \text{time\_available}(\text{swing}_2)$.
3. **Đỉnh/Đáy ngày hôm trước (Previous Day High/Low - PDH/PDL):**
   - **Quy chuẩn cắt ngày theo giờ New York (`America/New_York`):**
     Sử dụng mốc đóng cửa thị trường New York lúc **17:00 NY** làm thời điểm chốt ngày giao dịch (xử lý giờ mùa hè DST tự động bằng `zoneinfo`):
     - Mùa đông (EST, UTC-5): 17:00 NY tương ứng **22:00 UTC** (nến H1 đóng lúc 22:00 UTC).
     - Mùa hè (EDT, UTC-4): 17:00 NY tương ứng **21:00 UTC** (nến H1 đóng lúc 21:00 UTC).
   - *Lý do định lượng:* Cắt lúc 00:00 UTC chia đôi phiên Á đang diễn ra, làm méo mó các mức giá cực đại/cực tiểu. Mốc 17:00 NY là chuẩn thanh toán bù trừ FX/Gold quốc tế.
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
- **Hệ quả cốt lõi:** Dù là `SWEPT` hay `BROKEN`, pool thanh khoản đó không bao giờ được phép sinh thêm bất kỳ sự kiện sweep nào sau này. Không có hiện tượng một pool đã bị breakout trước đó sau này lại ghi nhận sweep giả.

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
- Năm 2022 ($\text{spread} = 0,266 \times 1,25 \approx 0,33$ USD): $\text{friction} \approx 0,43$ USD.
- Năm 2024 ($\text{spread} = 0,397 \times 1,25 \approx 0,50$ USD): $\text{friction} \approx 0,60$ USD.

### 7.2. Ràng buộc khoảng cách Stop Loss ($R_{dist}$)
Để tỷ lệ chi phí rủi ro không vượt quá mục tiêu chuẩn mực $\le \mathbf{0,08R}$ (tương thích với Baseline):
$$\text{cost\_R} = \frac{\text{friction}_{\text{year}}}{R_{dist}} \le 0,08 \implies R_{dist} \ge \mathbf{12,5 \times (\text{spread}_{\text{year}} \times 1,25 + 2 \times \text{slip})}$$

- **Công thức ràng buộc chi phí chính thức:**
  $$R_{dist} \ge \max\Big(0,5 \times ATR(H1, 14), \; 12,5 \times (\text{spread}_{\text{year}} \times 1,25 + 2 \times \text{slip})\Big)$$
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
| $C_{\text{mult}}$ | Cost Multiplier | Hệ số khoảng cách SL tối thiểu theo phí ma sát | **12,5** (chi phí $\le 0,08$R) | 10,0 (chi phí $\le 0,10$R) |

*Nguyên tắc:* Mọi bước lập trình, kiểm thử và thiết kế ban đầu CHỈ ĐƯỢC PHÉP SỬ DỤNG cấu hình **PRIMARY**. Các giá trị EXPLORATORY chỉ được dùng để phân tích độ nhạy (sensitivity analysis) sau khi đã đăng ký và chốt kết quả PRIMARY.

---

## 9. Hiệu chỉnh thử nghiệm & Quy tắc kiểm soát ngẫu nhiên ($N_{\text{trials}} = 2$)

Khi hoàn thiện các thành phần và đăng ký thiết kế chiến lược SMC đầu tiên:
- Số thứ tự thử nghiệm thật: **$N_{\text{trials}} = 2$**.
- Ngưỡng $p$-value kiểm soát ngẫu nhiên hiệu chỉnh Bonferroni:
  $$p_{\text{threshold}} = \frac{0,05}{2} = \mathbf{0,0250}$$
- **Kiểm soát ngẫu nhiên chuẩn hóa (tương thích C4):**
  - Với mỗi lệnh SMC thật: giữ nguyên thời điểm vào lệnh `entry_time`, cùng giá mở nến `entry_open`.
  - Cùng khoảng cách dừng lỗ theo giá $d = |\text{entry\_open} - \text{SL}|$.
  - Nếu lệnh thật là BUY $\implies$ đối chứng ngẫu nhiên có thể là SELL với $\text{SL} = \text{entry\_open} + d$, $\text{TP} = \text{entry\_open} - 2d$ (tỷ lệ 1:2), cùng chi phí ma sát theo năm.
  - Nếu lệnh thật là SELL $\implies$ đối chứng ngẫu nhiên có thể là BUY với $\text{SL} = \text{entry\_open} - d$, $\text{TP} = \text{entry\_open} + 2d$ (tỷ lệ 1:2), cùng chi phí ma sát theo năm.
  - Rút ngẫu nhiên BUY hoặc SELL với xác suất 0,5 cho từng lệnh qua 1.000 lần lặp với seed cố định.
  - $p$-value: $p = (1 + \sum(\text{EV}_{\text{rand}} \ge \text{EV}_{\text{real}})) / 1001$.
- Giữ nguyên các tiêu chí phê duyệt: Tổng số lệnh $\ge 800$, Kỳ vọng ròng $\ge +0,05$R, Profit Factor $\ge 1,10$, $\ge 5/8$ năm dương lịch.
- Tính toán Deflated Sharpe Ratio (DSR) với $N = 2$.

---

## 10. Bộ tiêu chuẩn kiểm thử bắt buộc cho mọi thành phần SMC

Mỗi thành phần SMC khi lập trình phải vượt qua 4 bài kiểm tra:
1. **Test bất biến theo tiền tố (Prefix Invariance Test - BẮT BUỘC):**
   - Chạy trên dữ liệu cắt ngắn đến hàng $M$ của bảng H1, sau đó chạy trên toàn bộ dữ liệu dài hơn.
   - **Quy tắc cắt:** Cắt theo hàng của bảng H1, không cắt M1.
   - Mọi sự kiện có $\text{time\_available} \le \text{close\_time}[M]$ phải giống nhau 100% từng giá trị.
2. **Test các ca tính tay (Hand-crafted synthetic tests):**
   - Kiểm tra các ca biên: đỉnh bằng nhau bị loại, ngoài nến (outside bar) sinh cả 2 swing, bước qua khoảng trống cuối tuần, FVG vừa chạm mép gần vừa đóng xuyên mép xa, swing cũ bị che khuất.
3. **Thống kê tần suất xuất hiện theo năm & Cảnh báo bẫy tinh chỉnh (The Tuning Trap):**
   - Đếm số lượng sự kiện mỗi năm trên lớp Development (2018–2025) để kiểm tra tính khả thi của quy mô mẫu ($\ge 800$ lệnh).
   - > [!CAUTION]
     > **CẢNH BÁO BẪY TINH CHỈNH:** Đóng băng toàn bộ giá trị tham số PRIMARY TRƯỚC KHI thực hiện đếm sự kiện. Tuyệt đối không nới lỏng tham số ($k$ từ 3 xuống 2, hoặc $\gamma$ từ 0,15 xuống 0,05) để "ép đủ 800 lệnh". Nếu số sự kiện không đủ, kết luận là thiết kế không khả thi, hoặc đăng ký thiết kế mới với $N_{\text{trials}} = 3$, chứ không âm thầm nới tham số.
4. **Kiểm tra trực quan bằng biểu đồ:**
   - Vẽ trực quan các thành phần lên đồ thị nến H1 nhiều tuần liên tiếp để đối chiếu bằng mắt trước khi kết nối vào bộ sinh tín hiệu.
