# TOOL TỰ ĐỘNG CHIA & GHÉP VIDEO + ÂM THANH NGẪU NHIÊN (AutoRandomVideoRemix v2.5)

Phần mềm đồ họa chuyên nghiệp (GUI) chạy trên Windows (Windows 10 / Windows 11), xây dựng trên nền tảng **Python, PySide6 và FFmpeg**. Ứng dụng tích hợp hai hệ thống xử lý độc lập nhưng kết hợp hoàn hảo để tự động sản xuất video dài hoàn chỉnh phục vụ làm nội dung YouTube, TikTok, Facebook Reels hoặc video minh họa lời bình.

---

## 🌟 NGUYÊN TẮC CỐT LÕI CỦA 2 HỆ THỐNG

### 🎬 PHẦN 1 — HỆ THỐNG XỬ LÝ VIDEO
* **Quy trình tuần tự bắt buộc:**
  $$\text{Quét Video} \longrightarrow \text{Chia Tuần Tự (Gộp Dư)} \longrightarrow \text{Tạo Kho Chung} \longrightarrow \text{Xáo Trộn} \longrightarrow \text{Bốc Đoạn} \longrightarrow \text{Ghép MP4}$$
* **Tuyệt đối KHÔNG chọn thời điểm bắt đầu ngẫu nhiên trên video nguồn:** Toàn bộ video nguồn được chia thành các mốc thời gian liên tiếp từ giây số 0 đến hết.
* **Quy tắc gộp phần dư bắt buộc:**
  - Các đoạn thông thường dài đúng 10 giây (hoặc theo cấu hình).
  - Nếu cuối video còn phần dư ngắn hơn độ dài đoạn, phần dư đó được gộp vào đoạn liền trước:
    - *Video 35s:* $10\text{s} + 10\text{s} + 15\text{s}$ (phần dư 5s gộp vào đoạn 3).
    - *Video 48s:* $10\text{s} + 10\text{s} + 10\text{s} + 18\text{s}$ (phần dư 8s gộp vào đoạn 4).
    - *Video 23s:* $10\text{s} + 13\text{s}$.
    - *Video 60s:* 6 đoạn, mỗi đoạn đúng 10s.
    - *Video 8s:* 1 đoạn 8s (không có đoạn trước để gộp).
    - *Video 15s:* 1 đoạn 15s (10s + 5s dư gộp thành 15s).
* **Kho đoạn ảo (Virtual Segment Pool):** Lưu danh sách mốc thời gian trong RAM, không cắt vật lý ra đĩa trước đó để tiết kiệm dung lượng ổ cứng và tránh hao mòn SSD.
* **Bốc ngẫu nhiên & Chống trùng:**
  - Mặc định bốc không hoàn lại (mỗi đoạn xuất hiện tối đa 1 lần trong 1 video đầu ra).
  - Tùy chọn hạn chế 2 đoạn từ cùng một video nguồn xuất hiện liền kề.
  - Tùy chọn loại trừ các đoạn đã từng xuất trong các phiên làm việc trước (`history/export_history.json`).
  - Tự động cắt ngắn đoạn cuối để đạt đúng 100% thời lượng yêu cầu.

---

### 🎵 PHẦN 2 — HỆ THỐNG ÂM THANH NGẪU NHIÊN
* **Nguyên tắc bắt buộc:** **Âm thanh tuyệt đối KHÔNG bị chia nhỏ thành từng đoạn 10 giây như video.** Mỗi file âm thanh (MP3, WAV, M4A, FLAC, AAC) được giữ nguyên vẹn 100% nội dung và thời lượng.
* **Xáo trộn theo chu kỳ (Shuffled Deck Cycles):**
  - Toàn bộ danh sách bài hát được xáo trộn ngẫu nhiên.
  - Mỗi bài chỉ xuất hiện 1 lần trong mỗi chu kỳ. Khi hết chu kỳ mà chưa đủ thời lượng video, hệ thống tự động xáo trộn chu kỳ mới.
* **Chống trùng bài liền kề qua chu kỳ (Anti-Adjacent Rule):**
  $$Track_{cycle\_k}[0] \ne Track_{cycle\_k-1}[-1]$$
  Bài đầu tiên của lượt mới không bao giờ trùng với bài cuối cùng của lượt trước. Nếu trùng, thuật toán tự động đổi vị trí với bài khác trong lượt.
* **2 Chế độ quản lý thời lượng âm thanh:**
  - **Chế độ A (Cắt bài cuối để khớp thời lượng):** Bốc các bài nguyên vẹn, chỉ cắt ngắn phần đuôi của bài cuối cùng để khớp đúng 100% thời lượng mục tiêu.
  - **Chế độ B (Giữ nguyên toàn bộ bài):** Giữ nguyên vẹn tất cả bài hát, không cắt xén bất kỳ giây nào.

---

### 🚀 3 CHẾ ĐỘ XUẤT (EXPORT TARGETS)
1. **🎬 Ghép Video + Âm thanh ngẫu nhiên:** Kết hợp video nền từ Phần 1 và âm thanh ngẫu nhiên từ Phần 2 thành file MP4 hoàn chỉnh (tắt hoàn toàn âm thanh gốc của clip).
2. **🎵 Chỉ xuất Âm thanh ngẫu nhiên:** Ghép nối tiếp các bài hát trong kho thành một file âm thanh duy nhất (`.mp3`, `.m4a` hoặc `.wav`).
3. **🎞 Chỉ xuất Video (Tắt tiếng):** Ghép các đoạn video thành file MP4 không có tiếng.

---

## 📁 CẤU TRÚC THƯ MỤC DỰ ÁN

```
e:\TOOL\AutoRandomVideoRemix/
│
├── app.py                      # Điểm khởi chạy giao diện GUI chính
├── run.bat                     # File kích đúp 1-click để khởi chạy trên Windows
├── build_exe.bat               # File đóng gói ứng dụng thành file EXE độc lập
├── requirements.txt            # Danh sách thư viện Python cần thiết
├── user_settings.json          # Lưu tự động cấu hình & thư mục đã chọn (không cần chọn lại)
├── README.md                   # Tài liệu hướng dẫn sử dụng chi tiết
│
├── core/                       # Lõi xử lý logic & thuật toán
│   ├── config.py               # Data Models: VideoInfo, SegmentInfo, AudioInfo, AudioPlaylistItem
│   ├── settings_manager.py     # Tự động ghi nhớ thư mục video, nhạc, output & cấu hình
│   ├── segment_slicer.py       # Thuật toán chia video tuần tự & gộp phần dư
│   ├── pool_manager.py         # Quản lý kho đoạn video, xáo trộn & lập kế hoạch ghép
│   ├── history_manager.py      # Lưu trữ & loại trừ lịch sử đoạn video đã xuất (export_history.json)
│   ├── video_scanner.py        # Quét và đọc thông số video bằng ffprobe
│   ├── video_processor.py      # Render video qua FFmpeg (NVENC/CPU), chuẩn hóa & mux audio
│   ├── audio_scanner.py        # Quét và phân tích metadata file âm thanh bằng ffprobe
│   ├── audio_pool_manager.py   # Quản lý chu kỳ xáo trộn âm thanh, chống trùng bài liền kề
│   └── audio_processor.py      # Ghép nối & chuẩn hóa file âm thanh độc lập qua FFmpeg
│
├── ui/                         # Giao diện người dùng đồ họa (PySide6 Fluent Design)
│   ├── main_window.py          # Cửa sổ chính, điều phối toàn bộ luồng xử lý
│   ├── styles.py               # Định kiểu giao diện Fluent UI hiện đại
│   └── components/
│       ├── source_panel.py     # Khu vực A: Thư mục video nguồn & thống kê số đoạn sau chia
│       ├── audio_panel.py      # Khu vực B: Kho âm thanh ngẫu nhiên (Phần 2)
│       ├── cut_config_panel.py # Khu vực C: Cấu hình cắt video, gộp dư, chống trùng & lịch sử
│       ├── output_config_panel.py # Khu vực D: Độ phân giải, FPS, tăng tốc GPU/CPU
│       ├── progress_panel.py   # Khu vực E: Nút điều khiển, tiến độ, nhật ký Realtime (Logs)
│       └── cut_table_view.py   # Khu vực F: Bảng 3 tab (Kho đoạn, Kế hoạch video, Playlist nhạc)
│
├── tests/                      # Bộ kiểm thử tự động toàn diện
│   └── test_full_system.py     # 15 unit tests kiểm chứng 100% các trường hợp biên
│
└── history/                    # Thư mục lưu lịch sử chống trùng xuất video
    └── export_history.json     # Dữ liệu các đoạn đã xuất qua các phiên làm việc
```

---

## 🖥️ HƯỚNG DẪN THAO TÁC TRÊN GIAO DIỆN

### Bước 1: Quét Video nguồn
1. Tại **Khu vực A (Thư mục video nguồn)**, nhấn **Chọn thư mục...** để trỏ đến thư mục chứa video (hỗ trợ MP4, MOV, MKV, AVI...).
2. Nhấn nút **Quét video**.
3. Hệ thống hiển thị số video hợp lệ, tổng thời lượng và **Tổng số đoạn sau khi chia** dự kiến.

### Bước 2: Quét Âm thanh nguồn (Phần 2)
1. Tại **Phần 2 (Kho âm thanh ngẫu nhiên)**, nhấn **Chọn thư mục...** để chọn thư mục chứa bài hát (MP3, WAV, M4A, AAC, FLAC...).
2. Nhấn nút **Quét âm thanh**.
3. Chọn chế độ thời lượng:
   * **Chế độ A:** Cắt bài cuối để khớp chính xác thời lượng video.
   * **Chế độ B:** Giữ nguyên toàn bộ các bài không cắt xén.
4. Chọn **Mục tiêu xuất**:
   * *Ghép Video + Âm thanh ngẫu nhiên (MP4)*
   * *Chỉ xuất Âm thanh ngẫu nhiên (MP3/M4A/WAV)*
   * *Chỉ xuất Video (không âm thanh)*

### Bước 3: Cấu hình cắt & xuất video
1. Nhập thời lượng mỗi đoạn (mặc định 10 giây).
2. Tích chọn **Gộp phần dư** (mặc định BẬT).
3. Nhập tổng thời lượng video đầu ra (ví dụ: 30 phút).
4. Tùy chọn **Loại trừ các đoạn đã từng xuất trong lịch sử** nếu không muốn lặp lại nội dung đã làm trước đây.

### Bước 4: Xem trước kế hoạch ghép
* Nhấn **📦 Tạo kho video** $\to$ Hệ thống tạo kho đoạn ảo và hiển thị tại Tab 1 (*Kho đoạn*).
* Nhấn **🔀 Xáo trộn kho** $\to$ Xáo trộn thứ tự các đoạn.
* Nhấn **🎵 Tạo playlist nhạc** $\to$ Tạo danh sách phát bài hát không trùng liền kề tại Tab 3 (*Playlist Âm thanh*).
* Nhấn **📋 Xem kế hoạch** $\to$ Hiển thị thứ tự ghép nối chi tiết tại Tab 2 (*Kế hoạch ghép*).

### Bước 5: Bắt đầu xuất video & Điều khiển
* Nhấn nút lớn màu xanh: **BẮT ĐẦU XUẤT (RENDER)**.
* Trong quá trình render:
  - Nhấn **⏸ Tạm dừng** để dừng tạm thời (nút chuyển sang **▶ Tiếp tục**).
  - Nhấn **⏹ Hủy bỏ** để dừng an toàn và tự động dọn dẹp các file tạm trên ổ cứng.
  - Theo dõi thanh tiến trình tổng thể và console nhật ký chi tiết.

---

## 🛠️ HƯỚNG DẪN CÀI ĐẶT & CHẠY

### 1. Yêu cầu môi trường
* Windows 10 hoặc Windows 11 (64-bit).
* Python 3.10+ (Đã tích hợp sẵn tại `e:\TOOL\python\python.exe`).
* FFmpeg và FFprobe trong biến môi trường PATH hoặc đặt tại `C:\bin\`.

### 2. Chạy ứng dụng trực tiếp
* Kích đúp vào file:
  ```
  e:\TOOL\AutoRandomVideoRemix\run.bat
  ```

### 3. Chạy bằng dòng lệnh
```powershell
& "e:\TOOL\python\python.exe" "app.py"
```

### 4. Chạy bộ kiểm thử tự động
```powershell
& "e:\TOOL\python\python.exe" -m unittest tests/test_full_system.py
```

### 5. Đóng gói ứng dụng thành file EXE độc lập
Kích đúp vào file:
```
e:\TOOL\AutoRandomVideoRemix\build_exe.bat
```
File thực thi `.exe` sau khi đóng gói sẽ nằm tại:
`dist\AutoRandomVideoRemix\AutoRandomVideoRemix.exe`.
