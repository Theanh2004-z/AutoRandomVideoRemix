"""Readiness rules for the guided desktop workflow, independent of Qt."""
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class WorkflowInputs:
    target: str
    video_count: int
    video_seconds: float
    audio_count: int
    audio_seconds: float
    duration: float
    batch_count: int
    unique_policy: str
    output_dir: str
    scanning_video: bool = False
    scanning_audio: bool = False
    audio_repeats: bool = True


@dataclass(frozen=True)
class ReadinessCheck:
    title: str
    detail: str
    state: str


@dataclass(frozen=True)
class WorkflowReadiness:
    sources_ready: bool
    can_prepare: bool
    checks: Tuple[ReadinessCheck, ...]

    @property
    def next_action(self) -> str:
        return next((c.detail for c in self.checks if c.state == "action"),
                    "Lập kế hoạch để kiểm tra thứ tự ghép và thời lượng thực tế.")


def evaluate_readiness(value: WorkflowInputs) -> WorkflowReadiness:
    if value.target == "none":
        return WorkflowReadiness(False, False, (ReadinessCheck("Nguồn xuất", "Tích Video hoặc Âm thanh để chọn nội dung cần xuất.", "action"),))
    checks = []
    video_needed = value.target != "audio_only"
    audio_needed = value.target in ("audio_only", "video_with_random_audio")
    if video_needed:
        state = "action" if value.scanning_video or value.video_count == 0 else "ready"
        detail = ("Đang quét video. Chờ hoàn tất để tiếp tục." if value.scanning_video else
                  "Chọn thư mục và quét video để tiếp tục." if not value.video_count else
                  f"Đã có {value.video_count} video hợp lệ.")
        checks.append(ReadinessCheck("Video nguồn", detail, state))
    if audio_needed:
        state = "action" if value.scanning_audio or value.audio_count == 0 else "ready"
        detail = ("Đang quét âm thanh. Chờ hoàn tất để tiếp tục." if value.scanning_audio else
                  "Chọn thư mục và quét âm thanh để tiếp tục." if not value.audio_count else
                  f"Đã có {value.audio_count} bài nhạc hợp lệ.")
        checks.append(ReadinessCheck("Âm thanh", detail, state))
    sources_ready = all(c.state != "action" for c in checks)
    if value.duration <= 0 or value.batch_count < 1:
        checks.append(ReadinessCheck("Thời lượng và số lượng", "Đặt thời lượng lớn hơn 0 và ít nhất một đầu ra.", "action"))
    else:
        checks.append(ReadinessCheck("Thời lượng và số lượng", f"Tạo {value.batch_count} đầu ra theo thời lượng đã chọn.", "ready"))
    if video_needed and value.video_count and not value.scanning_video:
        demand = value.duration * (value.batch_count if value.unique_policy == "unique_batch" else 1)
        if value.unique_policy != "cycle_output" and value.video_seconds + 0.05 < demand:
            checks.append(ReadinessCheck("Độ dài kho video", "Kho video chưa đủ với cách chống trùng đang chọn. Thêm video, giảm thời lượng hoặc chọn cho phép lặp ở bước Cấu hình.", "action"))
        elif value.unique_policy == "cycle_output":
            checks.append(ReadinessCheck("Cách dùng video", "Được phép xáo trộn và dùng lại đoạn khi hết kho.", "warning"))
        else:
            checks.append(ReadinessCheck("Độ dài kho video", "Tổng nguồn đáp ứng dự kiến. Việc phân bổ và lịch sử sẽ được kiểm tra khi lập kế hoạch.", "ready"))
    if audio_needed and value.audio_count and not value.scanning_audio:
        if not value.audio_repeats and value.audio_seconds + 0.00001 < value.duration:
            checks.append(ReadinessCheck("Độ dài kho nhạc", "Nhạc chưa đủ và đang tắt lặp lượt. Thêm nhạc hoặc bật lặp lượt trong tùy chọn âm thanh.", "action"))
        elif value.audio_count == 1 and value.audio_seconds < value.duration:
            checks.append(ReadinessCheck("Lặp bài nhạc", "Chỉ có một bài: bài này sẽ được lặp để đủ thời lượng.", "warning"))
    checks.append(ReadinessCheck("Nơi lưu kết quả", "Đã chọn thư mục đầu ra." if value.output_dir.strip() else "Chọn thư mục lưu kết quả ở bước Cấu hình.", "ready" if value.output_dir.strip() else "action"))
    return WorkflowReadiness(sources_ready, all(c.state != "action" for c in checks), tuple(checks))


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    parts = ([f"{hours} giờ"] if hours else []) + ([f"{minutes} phút"] if minutes else [])
    if seconds or not parts:
        parts.append(f"{seconds} giây")
    return " ".join(parts)
