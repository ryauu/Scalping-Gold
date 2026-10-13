# SMC_SPEC.md — Đặc tả kỹ thuật các thành phần Smart Money Concepts (SMC)

**Trạng thái: BẢN THẢO THIẾT KẾ (Chưa tính P&L, chưa chạy backtest).**  
Tuân thủ nguyên tắc nghiên cứu định lượng:
- **Nguyên tắc 1:** Không tính P&L khi xây dựng các thành phần. Chỉ kiểm tra tính đúng đắn toán học, hình dạng hình học, đếm tần suất xuất hiện và đối chiếu trực quan bằng mắt. Không tiêu tốn số lượt thử nghiệm ($N_{\text{trials}}$) và không làm bẩn lớp Development.
- **Nguyên tắc 2:** Mọi sự kiện phát hiện được BẮT BUỘC phải trả về thuộc tính `time_available` (thời điểm nến cuối cùng cần thiết để xác nhận sự kiện đóng cửa). Thuật toán giao dịch tại thời điểm $t$ chỉ được sử dụng các sự kiện có $\text{time\_available} \le t$. Tuyệt đối chống look-ahead bias.

---

## 1. Khung thời gian & Dữ liệu chuẩn
- **Khung thời gian cấu trúc chính:** **H1** (nến 1 giờ UTC). Đã được chuẩn hóa từ M1 sạch trong `data/meta.duckdb` (view `h1_utc`), kèm chỉ báo biến động $ATR(H1, 14)$ tính theo chuẩn Wilder không look-ahead.
- **Quy ước nhãn thời gian H1:** Nhãn `time_utc` của nến H1 là giờ **MỞ** nến (ví dụ `08:00:00` là nến chạy từ `08:00:00` đến `08:59:59`).
- **Thời điểm đóng nến H1:** `close_time = open_time + 1 hour` (nến nhãn `08:00:00` đóng cửa hoàn chỉnh lúc `09:00:00`).

---

## 2. Swing High & Swing Low (`src/backtest/smc/swings.py`)

### 2.1. Định nghĩa toán học
Với một chuỗi nến H1 và tham số $k \in \mathbb{N}^*$ (số nến lân cận mỗi bên, mặc định $k = 3$ cho nhịp sóng cục bộ, $k = 5$ cho nhịp sóng cấu trúc lớn):
- **Đỉnh Swing High tại nến $i$:**
  $$High[i] > High[i - j] \quad \forall j \in \{1, \dots, k\} \quad \text{VÀ} \quad High[i] > High[i + j] \quad \forall j \in \{1, \dots, k\}$$
  *(Điều kiện lớn hơn nghiêm ngặt; nếu có nến lân cận bằng đúng $High[i]$ thì không tạo đỉnh đơn lẻ).*
- **Đáy Swing Low tại nến $i$:**
  $$Low[i] < Low[i - j] \quad \forall j \in \{1, \dots, k\} \quad \text{VÀ} \quad Low[i] < Low[i + j] \quad \forall j \in \{1, \dots, k\}$$
  *(Điều kiện nhỏ hơn nghiêm ngặt).*

### 2.2. Thời điểm khả dụng (`time_available`)
- Nến $i$ là đỉnh/đáy tiềm năng, nhưng chỉ được **xác nhận hoàn toàn** khi nến $i + k$ đã đóng cửa.
- Công thức:
  $$\text{time\_available}(i) = \text{close\_time}(i + k) = \text{open\_time}(i + k) + 1\text{h}$$
- **Trường hợp hai đỉnh/đáy bằng nhau trong cửa sổ:**
  Nếu $High[i] = High[i+m]$ ($m \le k$), không xác nhận swing đơn lẻ tại $i$; cụm đỉnh bằng nhau được chuyển giao cho bộ phát hiện Equal Highs (EQH) thuộc Pool thanh khoản.

### 2.3. Cấu trúc dữ liệu đầu ra của Swing
Mỗi swing được lưu trữ dạng bản ghi với các trường:
- `swing_id`: Mã định danh số nguyên tăng dần.
- `swing_type`: `'HIGH'` hoặc `'LOW'`.
- `bar_index`: Chỉ số nến $i$ tạo đỉnh/đáy.
- `time_candle`: Thời gian mở của nến tạo đỉnh/đáy (`time_utc[i]`).
- `time_available`: Thời gian xác nhận đóng nến $i + k$ (`time_utc[i + k] + 1h`).
- `price`: Mức giá đỉnh ($High[i]$) hoặc đáy ($Low[i]$).
- `k`: Tham số lân cận đã dùng.

---

## 3. Cấu trúc thị trường: BOS & CHoCH (`src/backtest/smc/structure.py`)

### 3.1. Phá vỡ cấu trúc tiếp diễn (Break of Structure - BOS)
- **BOS tăng (Bullish BOS):** Xảy ra tại nến H1 $t$ khi:
  $$Close[t] > \text{SwingHigh}_{\text{last}}.\text{price}$$
  trong đó $\text{SwingHigh}_{\text{last}}$ là Swing High gần nhất thỏa mãn $\text{time\_available} \le \text{open\_time}[t]$.
  *(Lưu ý: Râu nến vượt qua nhưng giá đóng cửa nằm dưới $Close[t] \le SwingHigh$ KHÔNG tính là BOS, mà là tiềm năng Quét thanh khoản).*
- **BOS giảm (Bearish BOS):** Xảy ra tại nến H1 $t$ khi:
  $$Close[t] < \text{SwingLow}_{\text{last}}.\text{price}$$
  với $\text{SwingLow}_{\text{last}}$ thỏa mãn $\text{time\_available} \le \text{open\_time}[t]$.
- **Thời điểm khả dụng của BOS:** $\text{time\_available} = \text{close\_time}[t] = \text{open\_time}[t] + 1\text{h}$.

### 3.2. Thay đổi tính chất xu hướng (Change of Character - CHoCH)
- **Bullish CHoCH:** Đang trong cấu trúc giảm (chuỗi đỉnh/đáy thấp dần), nến H1 đóng cửa vượt lên trên đỉnh Swing High gần nhất tạo ra đáy thấp nhất vừa rồi.
- **Bearish CHoCH:** Đang trong cấu trúc tăng (chuỗi đỉnh/đáy cao dần), nến H1 đóng cửa xuyên thủng đáy Swing Low gần nhất tạo ra đỉnh cao nhất vừa rồi.

---

## 4. Fair Value Gap (FVG) & Nhật ký vòng đời (`src/backtest/smc/fvg.py`)

### 4.1. Định nghĩa hình học 3 nến liên tiếp
Xét bộ 3 nến H1 liên tiếp tại chỉ số $[i-2, i-1, i]$:
- **Bullish FVG (Gap tăng giá):**
  $$Low[i] > High[i-2]$$
  Vùng FVG: $[\text{bottom} = High[i-2], \text{top} = Low[i]]$.
- **Bearish FVG (Gap giảm giá):**
  $$High[i] < Low[i-2]$$
  Vùng FVG: $[\text{bottom} = High[i], \text{top} = Low[i-2]]$.

### 4.2. Bộ lọc kích thước tối thiểu
Để loại bỏ các vi khoảng trống nhiễu (vài cents):
$$\text{gap\_size} = |\text{top} - \text{bottom}| \ge \gamma \times ATR(H1, 14)_{[i-1]}$$
Tham số mặc định: $\gamma = 0,15$ (tối thiểu 15% của ATR H1).

### 4.3. Nhật ký vòng đời bất biến (Immutable Lifecycle Log)
FVG không được ghi đè trạng thái tại chỗ. Mọi thay đổi trạng thái được ghi thành các sự kiện riêng biệt kèm mốc thời gian:
1. `CREATED`: Tại thời điểm nến $i$ đóng cửa ($\text{time\_available} = \text{close\_time}[i]$).
2. `TESTED` (Chạm mép): Nến sau đó có giá chạm vào vùng gap nhưng chưa xuyên thủng mép xa.
3. `INVALIDATED` (Vô hiệu): Nến sau đó có giá **đóng cửa** xuyên thủng mép xa của FVG ($Close < \text{bottom}$ đối với Bullish FVG).
4. `EXPIRED` (Hết hạn): Tồn tại quá $M$ nến H1 (mặc định $M = 24$ nến = 1 ngày giao dịch) mà chưa được test.

Nhờ mô hình Event Log, tại bất kỳ thời điểm $t$, hệ thống có thể truy vấn chính xác danh sách các FVG còn hiệu lực tại $t$.

---

## 5. Pool thanh khoản & Quét thanh khoản (Liquidity Pools & Sweeps) (`src/backtest/smc/liquidity.py`)

### 5.1. Các nguồn Pool thanh khoản
1. **Swing High / Swing Low:** Các đỉnh/đáy đã xác nhận ($\text{time\_available} \le t$).
2. **Equal Highs / Equal Lows (EQH / EQL):** Hai đỉnh hoặc hai đáy lân cận cách nhau không quá $\varepsilon \times ATR(H1, 14)$ (mặc định $\varepsilon = 0,08$).
3. **Đỉnh/Đáy ngày hôm trước (Previous Day High/Low - PDH/PDL):** Khả dụng từ 00:00 UTC của ngày kế tiếp.
4. **Biên vùng Á (Asian High / Asian Low):** Khả dụng từ 07:00 UTC của chính ngày đó (đã có từ Bước 3).

### 5.2. Định nghĩa Quét thanh khoản (Liquidity Sweep)
Sweep là hiện tượng giá thò râu qua vùng thanh khoản để khớp lệnh chờ nhưng không duy trì được đà:
- **Sweep đỉnh (High Sweep):**
  $$High[t] > \text{Pool}_{\text{High}} \quad \text{VÀ} \quad Close[t] \le \text{Pool}_{\text{High}}$$
- **Sweep đáy (Low Sweep):**
  $$Low[t] < \text{Pool}_{\text{Low}} \quad \text{VÀ} \quad Close[t] \ge \text{Pool}_{\text{Low}}$$
- **Phân biệt với Phá vỡ (Breakout):**
  Nếu $Close[t] > \text{Pool}_{\text{High}}$, đây là Breakout / BOS, tuyệt đối **KHÔNG** ghi nhận là Sweep.
- **Thời điểm khả dụng:** $\text{time\_available} = \text{close\_time}[t]$.

---

## 6. Quyết định loại bỏ Order Block (OB) khỏi thiết kế ban đầu
- **Lý do:** Khái niệm Order Block (cây nến ngược màu cuối cùng trước đợt sóng mạnh) mang tính chủ quan cao nhất, có quá nhiều định nghĩa tùy biến (tính theo thân nến, cả râu nến, hay cụm nến) và làm tăng số bậc tự do (degrees of freedom).
- **Quyết định:** Thiết kế SMC ban đầu tập trung hoàn toàn vào **Liquidity Sweep + FVG + Structure**, loại bỏ hoàn toàn Order Block để giữ tính khách quan và giảm nguy cơ overfitting.

---

## 7. Ràng buộc chi phí đối với cấu trúc SMC (Cost Floor Rule)
Từ kết quả kiểm chứng Baseline PRIMARY:
- Chi phí spread và 2 lần trượt giá trung bình chiếm **$\approx 0,060$R** (với $R\_dist \approx 5-10$ USD).
- Nếu chiến lược SMC vào lệnh với Stop Loss quá chặt (ví dụ SL $= 1,5 - 2,0$ USD), chi phí thực tế sẽ đội lên:
  $$\text{cost\_R} = \frac{\text{spread} + 0,10}{R\_dist} = \frac{0,40 + 0,10}{2,0} = 0,25\text{R} \quad (25\% \text{ rủi ro})$$
  Điều này sẽ tiêu diệt bất kỳ lợi thế thống kê nào.
- **Ràng buộc chi phí bắt buộc trước khi thiết kế điểm vào lệnh:**
  $$R\_dist \ge \max\Big(0,5 \times ATR(H1, 14), \; 4 \times (\text{spread} + 2 \times \text{slip})\Big)$$
  Đảm bảo chi phí trong mọi lệnh không vượt quá $0,08$R.

---

## 8. Quy tắc hiệu chỉnh đa so sánh khi đăng ký thử nghiệm SMC ($N_{\text{trials}} = 2$)
Khi hoàn thiện các thành phần và đăng ký thiết kế SMC đầu tiên:
- Số thứ tự thử nghiệm thật: **$N_{\text{trials}} = 2$**.
- Ngưỡng $p$-value kiểm soát ngẫu nhiên hiệu chỉnh Bonferroni:
  $$p_{\text{threshold}} = \frac{0,05}{2} = \mathbf{0,0250}$$
- Giữ nguyên các tiêu chí: Tổng số lệnh $\ge 800$, Kỳ vọng ròng $\ge +0,05$R, Profit Factor $\ge 1,10$, $\ge 5/8$ năm lịch dương.
- Tính toán Deflated Sharpe Ratio (DSR) với $N = 2$.

---

## 9. Bộ tiêu chuẩn kiểm thử bắt buộc cho mọi thành phần SMC
Mỗi thành phần SMC khi lập trình phải vượt qua 4 bài kiểm tra:
1. **Test bất biến theo tiền tố (Prefix Invariance Test - BẮT BUỘC):**
   Chạy bộ phát hiện trên dữ liệu cắt ngắn đến thời điểm $t_1$, sau đó chạy trên toàn bộ dữ liệu đến $t_2 > t_1$. Mọi sự kiện có $\text{time\_available} \le t_1$ phải giống nhau từng bit.
2. **Test chuỗi giả tính tay (Hand-crafted synthetic tests):**
   Kiểm tra các ca biên: đỉnh bằng nhau, râu vượt nhưng thân không vượt, gap nhỏ hơn $\gamma \times ATR$.
3. **Thống kê tần suất xuất hiện theo năm:**
   Đếm số lượng sự kiện mỗi năm trên lớp Development (2018–2025) để kiểm tra tính ổn định và tính khả thi của quy mô mẫu ($\ge 800$ lệnh).
4. **Kiểm tra trực quan bằng biểu đồ:**
   Vẽ trực tiếp lên đồ thị nến H1 nhiều tuần liên tiếp để đối chiếu bằng mắt trước khi dùng.
