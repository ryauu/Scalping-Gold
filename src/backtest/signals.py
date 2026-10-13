"""Mô-đun signals.py - Nhận diện vùng Á và sinh tín hiệu phá vỡ (Breakout).

Theo PROTOCOL.md mục 4 (Baseline PRIMARY):
- Vùng Á: cao/thấp M1 từ 00:00 đến 07:00 UTC.
- Cửa sổ vào lệnh: 07:00 – 13:00 UTC. Tối đa 1 lệnh/ngày (hướng phá vỡ đầu tiên).
- Tín hiệu: nến M1 ĐÓNG CỬA vượt biên ± 0,1 × ATR(H1, 14); vào lệnh ở giá MỞ nến M1 kế tiếp.
"""

from __future__ import annotations

# Sẽ được hiện thực hóa ở các bước tiếp theo
