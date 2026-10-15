# TOOL TỰ ĐỘNG CHIA & GHÉP VIDEO + ÂM THANH NGẪU NHIÊN (AutoRandomVideoRemix v3.0)

Phần mềm đồ họa chuyên nghiệp (GUI) chạy trên Windows (Windows 10 / Windows 11 64-bit), xây dựng trên nền tảng **Python, PySide6, SQLite và FFmpeg**. Ứng dụng tích hợp hai hệ thống xử lý độc lập nhưng kết hợp hoàn hảo để tự động sản xuất video dài hoàn chỉnh phục vụ làm nội dung YouTube, TikTok, Facebook Reels hoặc video minh họa lời bình với độ ổn định và tính nhất quán cao.

---

## 🌟 ĐẶC TẢ KIẾN TRÚC V3.0 (CÁC ĐIỂM NÂNG CẤP VƯỢT TRỘI)

### 1. Phép tính Thời gian Chính xác Số Nguyên (Integer Timebase)
- Quy đổi toàn bộ mốc thời gian video sang **số khung hình nguyên (integer frames)** và âm thanh sang **mẫu nguyên (integer audio samples)** qua module `core.timebase`.
- Loại bỏ hoàn toàn hiện tượng trôi dấu phẩy động (float drift) tích lũy qua hàng trăm đoạn clip.
- Hậu điều kiện: Không bao giờ sinh clip rỗng (0-frame), đoạn cuối được cắt gọt chuẩn xác tới từng frame hình.

### 2. Định danh Nguồn Xác định & Bất biến (Media Catalog & Plan Manifest)
- Nhận diện file nguồn độc lập với tên file: Dùng dấu vân tay nội dung (content fingerprint, kích thước, thời lượng) tạo `AssetFingerprint` và `MediaAsset`. Hai file cùng nội dung khác tên dùng chung định danh; hai file cùng tên khác nội dung nhận ID riêng biệt.
- Sắp xếp kho đoạn ổn định (deterministic sort order), độc lập hoàn toàn với thứ tự quét của hệ điều hành.
- Xuất bản hợp đồng kế hoạch `PlanManifest` có chữ ký băm SHA-256: Cùng seed + snapshot nguồn cho ra 100% cùng kết quả kế hoạch.

### 3. Ba Chính sách Chống Trùng Video Linh hoạt
Hỗ trợ 3 chính sách chọn đoạn qua giao diện và cấu hình:
- **`UNIQUE_BATCH` (Chống trùng cả đợt - Khuyên dùng):** Mỗi đoạn video trong kho chỉ xuất hiện tối đa 1 lần trong toàn bộ đợt batch $K$ video.
- **`UNIQUE_OUTPUT` (Chống trùng từng video):** Mỗi đoạn chỉ xuất hiện tối đa 1 lần trong 1 video đầu ra; cho phép tái sử dụng ở các video khác trong đợt nếu kho đã cạn.
- **`CYCLE_OUTPUT` (Bốc có hoàn lại):** Tự động xáo trộn và tái sử dụng kho đoạn khi kho ngắn hơn thời lượng yêu cầu.

### 4. Chính sách Tránh Nguồn Liền kề (Anti-Consecutive Policy)
- **`SOFT`:** Ưu tiên bốc đoạn từ video nguồn khác với đoạn liền trước; nếu kho chỉ còn các đoạn cùng nguồn thì chấp nhận để đảm bảo đủ thời lượng.
- **`STRICT`:** Bắt buộc 100% đoạn kế tiếp phải khác nguồn.
- **`OFF`:** Cho phép các đoạn từ cùng video đứng liền nhau.

### 5. Kho Lưu trữ Lịch sử SQLite Chuẩn hóa (`export_history.db`)
- Thay thế file JSON cũ bằng cơ sở dữ liệu SQLite trong chế độ WAL (`PRAGMA journal_mode=WAL;`), đảm bảo an toàn ACID và hiệu năng đọc/ghi cực nhanh.
- Lưu trữ các khoảng thời gian thực sự đã dùng `[start_sec, end_sec]` theo `asset_id`.
- Kiểm tra chồng lấn chính xác: `MAX(start, A) < MIN(end, B) - tolerance`.
- Tự động giữ chỗ (reservation locks) theo batch trong transaction; nếu tiến trình hủy bỏ thì tự động hoàn lại giữ chỗ.
- Tự động di trú (migration) an toàn từ file `export_history.json` cũ kèm bản sao lưu `.bak`.

### 6. Điều phối Tiến trình & Máy Trạng thái 9 Bước (`JobController`)
- Máy trạng thái 9 bước (`PENDING`, `PLANNING`, `RESERVING`, `PREPARING`, `RENDERING`, `VALIDATING`, `COMMITTING`, `COMPLETED`, `FAILED`).
- Chống kích hoạt đúp (double-start guard lock).
- Hỗ trợ tạm dừng / tiếp tục an toàn tại ranh giới đoạn (cooperative boundary pause).
- Đóng cửa sổ và hủy tiến trình an toàn với `ProcessRunner.kill_process_tree` (`taskkill /F /T /PID`), cam kết **không bao giờ để lại tiến trình FFmpeg con mồ côi**.

### 7. Xuất 2 Giai đoạn Nguyên tử & Tự Phục hồi Sự cố (Atomic 2-Phase Commit)
- Mọi video render được ghi trước vào file tạm staging: `_staging_<uuid>.mp4`.
- Kiểm thử xác thực tự động qua `OutputValidator`: Kiểm tra container, video stream, audio stream, độ phân giải, fps, sai số thời lượng (< 0.5s), và giải mã thử 1 giây đầu.
- Chỉ khi video vượt qua kiểm thử mới được thăng hạng nguyên tử (`os.replace`) thành file đích. Nếu lỗi, tự động xóa sạch file rác.
- Module `JobRecovery` quét nhật ký khi khởi động để tự động dọn dẹp các tệp staging mồ côi nếu máy tính bị sập nguồn đột ngột.

### 8. Bộ nhớ Đệm Render (Render Cache)
- Tự động tính khóa băm SHA-256 cho từng đoạn cắt chuẩn hóa dựa trên nguồn, khoảng thời gian và hồ sơ render.
- Khi lặp lại cùng đoạn trong các đợt sau hoặc render lại video, hệ thống tái sử dụng ngay đoạn đã cắt, tăng tốc độ xử lý lên gấp nhiều lần.
- Tự động thu dọn dung lượng theo giải thuật LRU (Least Recently Used) khi vượt quá giới hạn (mặc định 5 GB).

### 9. Dự toán Dung lượng Đĩa Trước Khi Xuất (Pre-flight Disk Space Estimator)
- Tự động tính toán dung lượng đĩa cần thiết cho toàn bộ đợt xuất (bao gồm cả file tạm cắt đoạn, file ghép và file xuất bản hoàn chỉnh với hệ số an toàn 2.0x).
- Kiểm tra dung lượng trống thực tế của ổ đĩa đích với vùng đệm an toàn 500 MB, cảnh báo sớm người dùng nếu ổ đĩa không đủ chỗ.

---

## 📁 CẤU TRÚC THƯ MỤC DỰ ÁN

```
e:\TOOL\AutoRandomVideoRemix/
│
├── app.py                      # Điểm khởi chạy ứng dụng chính (GUI)
├── run.bat                     # File kích đúp 1-click để chạy trên Windows
├── build_exe.bat               # File đóng gói ứng dụng thành file EXE độc lập
├── AutoRandomVideoRemix.spec   # Cấu hình PyInstaller chuẩn hóa (bundle bin/ & exclude history)
├── requirements.txt            # Danh sách dependency Python
├── user_settings.json          # Lưu tự động cấu hình giao diện của người dùng
├── README.md                   # Tài liệu hướng dẫn sử dụng & kiến trúc v3.0
│
├── bin/                        # Bộ công cụ FFmpeg & ffprobe đi kèm (EXE & DLLs)
│   ├── ffmpeg.exe
│   ├── ffprobe.exe
│   └── *.dll
│
├── core/                       # Lõi xử lý logic & thuật toán V3
│   ├── app_paths.py            # Quản lý đường dẫn ứng dụng, DB, settings, cache
│   ├── timebase.py             # Phép tính frame và audio sample chính xác số nguyên
│   ├── errors.py               # Cây phân cấp ngoại lệ chuẩn hóa
│   ├── policies.py             # Enum chính sách: UniquePolicy, AntiConsecutivePolicy, AudioMatchMode
│   ├── models.py               # NormalizedCutConfig, AssetFingerprint, ConfigAdapterV2
│   ├── media_catalog.py        # MediaCatalog, CatalogSnapshot, CatalogSegment
│   ├── plan_manifest.py        # Hợp đồng kế hoạch PlanManifest & PlanClip
│   ├── video_planner.py        # Thuật toán lập timeline video số nguyên frame
│   ├── audio_planner.py        # Thuật toán xáo trộn chu kỳ âm thanh số nguyên sample
│   ├── export_planner.py       # Điều phối video + audio thành PlanManifest
│   ├── history_repository.py   # Kho lưu trữ SQLite, truy vấn khoảng & khóa giữ chỗ
│   ├── history_migration.py    # Tự động di trú an toàn từ export_history.json cũ
│   ├── history_manager.py      # Lớp tương thích lịch sử cho GUI
│   ├── process_runner.py       # Quản lý subprocess, kill process tree, buffer stderr
│   ├── job_controller.py       # Máy trạng thái 9 bước, cooperative boundary pause
│   ├── output_validator.py     # Kiểm định chất lượng file video sau render
│   ├── output_commit.py        # Xuất 2 giai đoạn nguyên tử (staging -> os.replace)
│   ├── job_recovery.py         # Quét và dọn dẹp file tạm sau sự cố sập nguồn
│   ├── render_cache.py         # Bộ nhớ đệm đoạn cắt với cơ chế dọn dẹp LRU
│   ├── disk_estimator.py       # Dự toán dung lượng ổ đĩa trước khi xuất
│   ├── hardware_detector.py    # Nhận diện GPU NVIDIA (NVENC), AMD (AMF), Intel (QSV)
│   ├── video_processor.py      # Engine FFmpeg render video đa chế độ
│   ├── audio_processor.py      # Engine FFmpeg render và ghép nối âm thanh
│   ├── segment_slicer.py       # Thuật toán chia video tuần tự & gộp phần dư
│   ├── pool_manager.py         # SegmentPool tương thích ngược
│   └── audio_pool_manager.py   # AudioPool tương thích ngược
│
├── ui/                         # Giao diện người dùng đồ họa (PySide6 Fluent Design)
│   ├── main_window.py          # Cửa sổ chính, điều phối toàn bộ luồng xử lý V3
│   ├── styles.py               # Định kiểu Fluent UI hiện đại
│   └── components/
│       ├── source_panel.py     # Khu vực A: Thư mục video nguồn & thống kê số đoạn
│       ├── audio_panel.py      # Khu vực B: Kho âm thanh ngẫu nhiên & chế độ xuất
│       ├── cut_config_panel.py # Khu vực C: Bộ chọn chính sách V3, thời lượng, lịch sử
│       ├── output_config_panel.py # Khu vực D: Độ phân giải, FPS, tăng tốc GPU/CPU
│       ├── progress_panel.py   # Khu vực E: Tiến độ, điều khiển Pause/Cancel, nhật ký
│       └── cut_table_view.py   # Khu vực F: Bảng 3 tab (Kho đoạn, Kế hoạch, Playlist)
│
├── tests/                      # Bộ kiểm thử tự động toàn diện (111 Tests)
│   ├── fixtures_media.py       # Generator sinh video và audio tổng hợp qua FFmpeg lavfi
│   ├── test_v3_regression_cases.py  # 11 ca kiểm thử hồi quy bảo đảm tính đúng đặc tả
│   ├── test_v3_domain_models.py     # Kiểm thử timebase, models và config adapter
│   ├── test_v3_catalog_manifest.py  # Kiểm thử Catalog, Snapshot hash và Manifest
│   ├── test_v3_video_planner.py     # Kiểm thử thuật toán VideoPlanner số nguyên frame
│   ├── test_v3_audio_export_planner.py # Kiểm thử AudioPlanner và ExportPlanner
│   ├── test_v3_persistence.py       # Kiểm thử SQLite repository, overlap và migration
│   ├── test_v3_job_controller.py    # Kiểm thử máy trạng thái và cooperative pause
│   ├── test_v3_render_validation_recovery.py # Kiểm thử validator, 2-phase commit
│   ├── test_v3_ui_integration.py    # Kiểm thử tích hợp UI, chính sách và huỷ kế hoạch
│   └── test_v3_cache_and_estimator.py # Kiểm thử render cache, LRU và dự toán đĩa
│
└── history/                    # Thư mục lưu trữ lịch sử SQLite
    └── export_history.db       # Cơ sở dữ liệu SQLite chống trùng phiên làm việc
```

---

## 🛠️ HƯỚNG DẪN CÀI ĐẶT & CHẠY

### 1. Yêu cầu môi trường
* Windows 10 hoặc Windows 11 (64-bit).
* Python 3.10+ (Đã tích hợp sẵn tại `e:\TOOL\python\python.exe`).
* Thư viện: `PySide6`, `QFluentWidgets` (cài qua `requirements.txt`).
* FFmpeg và ffprobe đã được tích hợp sẵn trong thư mục `bin/` của dự án.

### 2. Chạy ứng dụng trực tiếp
* Kích đúp vào file:
  ```
  e:\TOOL\AutoRandomVideoRemix\run.bat
  ```
* Hoặc chạy qua dòng lệnh PowerShell:
  ```powershell
  & "e:\TOOL\python\python.exe" "app.py"
  ```

### 3. Chạy Toàn Bộ Bộ Kiểm Thử (111 Tests)
Để xác nhận tính ổn định và tính đúng đắn của toàn bộ hệ thống:
```powershell
& "e:\TOOL\python\python.exe" -m unittest discover -s tests -p "test_*.py"
```

### 4. Đóng Gói File EXE Độc Lập cho Windows
Kích đúp vào file:
```
e:\TOOL\AutoRandomVideoRemix\build_exe.bat
```
File thực thi `.exe` độc lập hoàn chỉnh sẽ được tạo tại:
`dist\AutoRandomVideoRemix\AutoRandomVideoRemix.exe` (đã đóng gói sẵn FFmpeg, ffprobe, DLL và giao diện Fluent UI, không mang theo dữ liệu cá nhân).
