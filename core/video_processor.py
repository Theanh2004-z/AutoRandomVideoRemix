"""
FFmpeg Video Processor Engine.
Handles trimming, resolution & FPS normalization, aspect ratio fitting/cropping,
audio stripping / keeping / background music injection, seamless concatenation,
probe validation, and pause/cancel handling.
"""
from core.storage_cleanup import Workspace, SpaceGuard, stop_owned_process
import os
import shutil
import subprocess
import threading
import time
import uuid
from typing import List, Dict, Optional, Callable, Tuple, Any
from core.config import SegmentInfo, OutputConfig, VideoInfo, AudioPlaylistItem
from core.video_scanner import probe_video_file
from core.history_manager import HistoryManager
from core.audio_processor import AudioProcessorEngine
from core.filename_generator import get_next_output_path
from core.output_validator import OutputValidator
from core.output_commit import OutputCommit
from core.app_paths import get_scratch_dir

from PySide6.QtCore import QThread, Signal


from core.hardware_detector import detect_hardware, HardwareProfile
from core.segment_renderer import SegmentRenderer, encoder_options, cpu_filter, HardwareEncoderError, RenderCancelled


class VideoProcessorEngine:
    """Core processor that runs FFmpeg rendering jobs."""

    def __init__(
        self,
        output_config: OutputConfig,
        on_log: Optional[Callable[[str, str], None]] = None,
        on_segment_done: Optional[Callable[[int, int, SegmentInfo], None]] = None,
        on_progress: Optional[Callable[[float, str], None]] = None,
        render_cache: Optional[Any] = None,
        source_metadata: Optional[Dict[str, VideoInfo]] = None,
        on_video_progress: Optional[Callable[[float], None]] = None,
        on_audio_progress: Optional[Callable[[float], None]] = None,
        scratch_dir: Optional[str] = None
    ):
        self.config = output_config
        self.scratch_dir = scratch_dir
        self.on_log = on_log or (lambda lvl, msg: None)
        self.on_segment_done = on_segment_done or (lambda idx, total, seg: None)
        self.on_progress = on_progress or (lambda pct, msg: None)
        self.on_video_progress = on_video_progress or (lambda pct: None)
        self.on_audio_progress = on_audio_progress or (lambda pct: None)
        self.render_cache = render_cache
        if self.render_cache is None:
            try:
                from core.render_cache import RenderCache
                self.render_cache = RenderCache()
            except Exception:
                self.render_cache = None

        self._is_paused = False
        self._is_cancelled = False
        self._current_subprocess: Optional[subprocess.Popen] = None
        self._temp_dir: Optional[str] = None
        self._workspace = None
        self._segment_renderer = None
        self._audio_engine = None
        self.source_metadata = dict(source_metadata or {})
        self.disabled_gpu_pipelines = set()
        self.batch_render_disabled = False
        self.last_render_stats = {}

    def pause(self):
        self._is_paused = True
        self.on_log("INFO", "Tạm dừng tiến trình xử lý...")

    def resume(self):
        self._is_paused = False
        self.on_log("INFO", "Tiếp tục tiến trình xử lý...")

    def cancel(self):
        self._is_cancelled = True
        self.on_log("WARNING", "Đang hủy tiến trình...")
        if self._audio_engine:
            self._audio_engine.cancel()
        if self._segment_renderer:
            self._segment_renderer.cancel()
        if self._current_subprocess:
            try:
                from core.process_runner import ProcessRunner
                ProcessRunner.kill_process_tree(self._current_subprocess.pid)
            except Exception:
                try:
                    self._current_subprocess.kill()
                except Exception:
                    pass
        # Active tasks are joined before the render path removes its workspace.

    def _cleanup_temp_dir(self):
        if self._workspace:
            try:
                self._workspace.cleanup()
                self._workspace = None
            except OSError as error:
                self.on_log("WARNING", f"Chưa dọn hết tệp tạm; sẽ thử lại sau: {error}")

    def _build_filter_complex(self, target_w: int, target_h: int) -> str:
        """
        Builds scaling and aspect ratio video filter.
        - 'fit': keep aspect ratio and pad black borders.
        - 'crop': scale to fill and crop excess.
        """
        return cpu_filter(self.config)

    def _determine_encoder(self, force_cpu: bool = False) -> Tuple[str, List[str]]:
        """
        Determines encoder and flags based on hw_accel setting.
        Supports:
          - auto: Automatically detects NVIDIA (nvenc), AMD (amf), Intel (qsv), or CPU (libx264)
          - nvenc: NVIDIA GPU
          - amf: AMD Radeon GPU
          - qsv: Intel Arc / QuickSync GPU
          - cpu: Software libx264
        Returns: (encoder_name, list_of_encoder_options)
        """
        if force_cpu:
            target_encoder = "libx264"
            self.on_log("WARNING", "Chuyển sang chế độ CPU an toàn (libx264) để tiếp tục xử lý.")
        else:
            req = (getattr(self.config, "hw_accel", None) or "auto").lower()
            profile = detect_hardware() if req != 'cpu' else None

            if req == "auto":
                target_encoder = profile.active_encoder
                self.on_log("INFO", f"Phần cứng phát hiện: {profile.primary_gpu_name} -> Kích hoạt {profile.encoder_name_display}")
            elif req == "nvenc":
                if "h264_nvenc" in profile.supported_hardware_encoders:
                    target_encoder = "h264_nvenc"
                    self.on_log("INFO", f"Sử dụng NVIDIA NVENC ({profile.primary_gpu_name}).")
                else:
                    self.on_log("WARNING", "NVIDIA NVENC không khả dụng trên máy này. Tự động chuyển sang CPU.")
                    target_encoder = "libx264"
            elif req == "amf":
                if "h264_amf" in profile.supported_hardware_encoders:
                    target_encoder = "h264_amf"
                    self.on_log("INFO", f"Sử dụng AMD AMF ({profile.primary_gpu_name}).")
                else:
                    self.on_log("WARNING", "AMD AMF không khả dụng trên máy này. Tự động chuyển sang CPU.")
                    target_encoder = "libx264"
            elif req == "qsv":
                if "h264_qsv" in profile.supported_hardware_encoders:
                    target_encoder = "h264_qsv"
                    self.on_log("INFO", f"Sử dụng Intel QuickSync QSV ({profile.primary_gpu_name}).")
                else:
                    self.on_log("WARNING", "Intel QuickSync (QSV) không khả dụng trên máy này. Tự động chuyển sang CPU.")
                    target_encoder = "libx264"
            else:
                target_encoder = "libx264"
                self.on_log("INFO", "Sử dụng CPU encoder (libx264 đa luồng).")

        return target_encoder, encoder_options(self.config,target_encoder)

    def process_video_plan(self, plan, output_file_path, video_index, total_videos, audio_playlist=None):
        scratch = self.scratch_dir or get_scratch_dir()
        os.makedirs(scratch, exist_ok=True)
        output = os.path.dirname(os.path.abspath(output_file_path))
        os.makedirs(output, exist_ok=True)
        guard = SpaceGuard([scratch, output], self)
        self._staging_path = None
        try:
            if not guard.start():
                return False
            return self._process_video_plan(plan, output_file_path, video_index, total_videos, audio_playlist)
        finally:
            guard.close()
            if stop_owned_process(self):
                self._current_subprocess = None
                self._audio_engine = None
                self._cleanup_temp_dir()
                if self.render_cache and hasattr(self.render_cache, 'flush'):
                    try:
                        self.render_cache.flush()
                    except OSError:
                        pass

    def _process_video_plan(
        self,
        plan: List[SegmentInfo],
        output_file_path: str,
        video_index: int,
        total_videos: int,
        audio_playlist: Optional[List[AudioPlaylistItem]] = None
    ) -> bool:
        """
        Processes a single output video plan:
        1. Cuts and normalizes all segments to temp folder.
        2. Seamlessly concats segments.
        3. Injects Random Audio / BGM if configured.
        4. Validates output file with ffprobe.
        5. Cleans up temp folder.
        """
        if not plan:
            self.on_log("ERROR", "Danh sách đoạn cắt rỗng.")
            return False

        os.makedirs(os.path.dirname(os.path.abspath(output_file_path)), exist_ok=True)

        # Create unique temporary workspace in scratch_dir (SSD)
        temp_id = uuid.uuid4().hex[:8]
        scratch_base = self.scratch_dir or get_scratch_dir()
        os.makedirs(scratch_base, exist_ok=True)
        self._workspace = Workspace(scratch_base, "remix")
        self._temp_dir = str(self._workspace.path)

        target_w, target_h = self.config.width_height
        target_fps = self.config.fps
        encoder_name, encoder_opts = self._determine_encoder()

        total_segments = len(plan)
        temp_segment_files: List[str] = []

        self.on_video_progress(0.0)
        self.on_audio_progress(0.0)

        self.on_log("INFO", f"=== Bắt đầu tạo video #{video_index}/{total_videos}: {os.path.basename(output_file_path)} ===")
        self.on_log("INFO", f"Tổng số đoạn cắt: {total_segments} đoạn | Độ phân giải: {target_w}x{target_h} | FPS: {target_fps}")

        # Render independently, collecting paths by plan position rather than
        # completion order. Hardware encoder failure retries the entire batch
        # with one CPU profile so concatenation never mixes encoder settings.
        for attempt in range(2):
            renderer = SegmentRenderer(self,encoder_name,encoder_opts,self._temp_dir)
            self._segment_renderer = renderer
            try:
                temp_segment_files = renderer.render(plan,video_index,total_videos)
                self.last_render_stats = dict(renderer.stats)
                self.on_log('INFO',f"Xử lý đoạn: {renderer.stats['rendered']} đoạn mới, {renderer.stats['cache_hits']} đoạn từ cache, {renderer.stats['hardware_segments']} đoạn tăng tốc GPU.")
                break
            except HardwareEncoderError as error:
                if self._is_cancelled:
                    return False
                self.on_log('WARNING',f'Bộ mã hóa GPU không hoàn thành tác vụ. Đang làm lại lượt này bằng CPU để giữ cấu hình đồng nhất. {str(error)[-240:]}')
                encoder_name,encoder_opts = self._determine_encoder(force_cpu=True)
                for clip in plan: clip.status='Chờ xử lý'
            except RenderCancelled:
                return False
            except Exception as error:
                self.on_log('ERROR',f'Lỗi xử lý đoạn video: {error}')
                return False
            finally:
                self._segment_renderer = None
        else:
            return False

        # Step 7: Prepare Audio if configured (Random Audio Pool)
        temp_audio_file = None
        if self.config.audio_mode == "random_audio_pool":
            if not audio_playlist:
                self.on_log("ERROR", "Lỗi: Danh sách phát âm thanh ngẫu nhiên rỗng, không thể ghép.")
                return False
            self.on_audio_progress(0.0)
            self.on_progress(88.0, f"Video #{video_index}/{total_videos}: Đang chuẩn bị {len(audio_playlist)} bài âm thanh...")
            self.on_log("INFO", f"Chuẩn bị {len(audio_playlist)} bài âm thanh ngẫu nhiên từ Phần 2...")
            temp_audio_file = os.path.join(self._temp_dir, "temp_audio.wav")
            audio_engine = AudioProcessorEngine(
                on_log=self.on_log,
                on_progress=lambda p, m: (
                    self.on_progress(round(88.0 + p * 0.04, 1), m),
                    self.on_audio_progress(p)
                ),
                scratch_dir=self.scratch_dir
            )
            self._audio_engine = audio_engine
            if self._is_cancelled:
                audio_engine.cancel()
            audio_ok = audio_engine.process_audio_playlist(
                playlist=audio_playlist,
                output_file_path=temp_audio_file,
                output_format="wav"
            )
            self._audio_engine = None
            if self._is_cancelled or not (audio_ok and os.path.isfile(temp_audio_file)):
                self.on_log("ERROR", "Không thể chuẩn bị file âm thanh ngẫu nhiên từ kho nhạc.")
                return False
            self.on_audio_progress(100.0)

        # Step 8: Single-Pass Concat + Audio Muxing directly to destination staging file
        self.on_video_progress(0.0)
        expected_dur = sum(c.effective_duration for c in plan)
        self.on_progress(92.0, f"Video #{video_index}/{total_videos}: Đang ghép nối video và âm thanh...")
        self.on_log("INFO", "Ghép nối tất cả các đoạn video và âm thanh trong 1 lượt ghi duy nhất...")

        concat_list_file = os.path.join(self._temp_dir, "concat_list.txt")
        with open(concat_list_file, "w", encoding="utf-8") as f:
            for seg in temp_segment_files:
                safe_path = seg.replace("\\", "/").replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

        final_staging_path = OutputCommit.get_staging_path(output_file_path)
        self._workspace.track_staging(final_staging_path)

        # Build single-pass FFmpeg command
        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_file
        ]

        if self.config.audio_mode == "random_audio_pool" and temp_audio_file:
            concat_cmd.extend([
                "-i", temp_audio_file,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k"
            ])
        elif self.config.audio_mode == "bgm" and os.path.isfile(self.config.bgm_path):
            self.on_log("INFO", f"Chèn nhạc nền: {os.path.basename(self.config.bgm_path)}...")
            concat_cmd.extend([
                "-stream_loop", "-1",
                "-i", self.config.bgm_path,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-shortest"
            ])
        elif self.config.audio_mode == "keep":
            concat_cmd.extend([
                "-c", "copy"
            ])
        else:
            concat_cmd.extend([
                "-map", "0:v:0",
                "-c:v", "copy",
                "-an"
            ])

        concat_cmd.extend([
            "-progress", "pipe:1",
            "-stats_period", "0.5",
            final_staging_path
        ])

        try:
            self._current_subprocess = subprocess.Popen(
                concat_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

            sub = self._current_subprocess
            stderr_lines: List[str] = []

            def _drain_stderr():
                if not sub or not hasattr(sub, "stderr") or sub.stderr is None:
                    return
                try:
                    for line_bytes in iter(sub.stderr.readline, b''):
                        stderr_lines.append(line_bytes.decode('utf-8', errors='replace'))
                except Exception:
                    pass
                finally:
                    try:
                        sub.stderr.close()
                    except Exception:
                        pass

            stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

            start_concat_time = time.monotonic()
            if sub and hasattr(sub, "stdout") and sub.stdout is not None and hasattr(sub.stdout, "readline"):
                try:
                    for raw_line in iter(sub.stdout.readline, b''):
                        if self._is_cancelled:
                            break
                        while self._is_paused and not self._is_cancelled:
                            time.sleep(0.3)

                        line = raw_line.decode('utf-8', errors='replace').strip()
                        if '=' in line:
                            k, _, v = line.partition('=')
                            k = k.strip()
                            v = v.strip()
                            cur_sec = None
                            if k == 'out_time_us' and v.isdigit():
                                cur_sec = int(v) / 1_000_000.0
                            elif k == 'out_time' and ':' in v:
                                try:
                                    parts = v.split(':')
                                    if len(parts) == 3:
                                        cur_sec = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                                except Exception:
                                    pass

                            if cur_sec is not None and expected_dur > 0:
                                fraction = min(1.0, max(0.0, cur_sec / expected_dur))
                                concat_pct = round(92.0 + fraction * 5.5, 1)  # 92.0% -> 97.5%
                                elapsed = int(time.monotonic() - start_concat_time)
                                m_cur, s_cur = divmod(int(cur_sec), 60)
                                m_tot, s_tot = divmod(int(expected_dur), 60)
                                time_str = f"{m_cur:02d}:{s_cur:02d}/{m_tot:02d}:{s_tot:02d}"
                                self.on_progress(
                                    concat_pct,
                                    f"Video #{video_index}/{total_videos}: Đang ghép kết quả · {time_str} (đã ghép {elapsed}s)"
                                )
                                self.on_video_progress(round(fraction * 100.0, 1))
                finally:
                    try:
                        sub.stdout.close()
                    except Exception:
                        pass

            if sub:
                if hasattr(sub, "wait"):
                    sub.wait()
                elif hasattr(sub, "communicate"):
                    sub.communicate()
            stderr_thread.join(timeout=2.0)

            if self._is_cancelled:
                OutputCommit.discard_staging(final_staging_path)
                return False

            returncode = getattr(sub, "returncode", 0) if sub else 0
            if returncode != 0:
                err_text = "".join(stderr_lines).strip()
                tail_err = "\n".join(err_text.splitlines()[-5:]) if err_text else "Unknown error"
                self.on_log("ERROR", f"Lỗi ghép video và âm thanh: {tail_err}")
                OutputCommit.discard_staging(final_staging_path)
                return False

        except Exception as e:
            self.on_log("ERROR", f"Ngoại lệ khi ghép kết quả: {e}")
            OutputCommit.discard_staging(final_staging_path)
            return False

        # Step 9: Probe Validation & Atomic 2-Phase Publication (V3)
        self.on_video_progress(100.0)
        self.on_progress(98.0, f"Video #{video_index}/{total_videos}: Đang kiểm tra file xuất...")
        self.on_log("INFO", "Kiểm tra tính toàn vẹn của video đầu ra bằng OutputValidator...")

        target_fps = float(self.config.fps)
        val_result = OutputValidator.validate_video(
            file_path=final_staging_path,
            expected_duration=expected_dur,
            expected_width=target_w,
            expected_height=target_h,
            expected_fps=target_fps,
            require_audio=(self.config.audio_mode in ("random_audio_pool", "bgm")),
            duration_tolerance_sec=1.0
        )

        if not val_result.is_valid:
            self.on_log("ERROR", f"Kiểm tra chất lượng thất bại: {val_result.error_msg}")
            OutputCommit.discard_staging(final_staging_path)
            return False

        # Step 10: Atomic publication via OutputCommit (prevents overwrite)
        ok, commit_msg = OutputCommit.publish_file(
            staging_path=final_staging_path,
            final_destination_path=output_file_path,
            validation=val_result,
            allow_overwrite=False
        )
        if not ok:
            self.on_log("ERROR", commit_msg)
            return False

        dur_diff = abs((val_result.actual_duration or 0.0) - expected_dur)
        self.on_log(
            "SUCCESS",
            f"Kiểm tra & công bố thành công! File: {os.path.basename(output_file_path)} | "
            f"Thời lượng thực tế: {val_result.actual_duration:.2f}s | "
            f"Độ phân giải: {val_result.actual_width}x{val_result.actual_height} | "
            f"FPS: {val_result.actual_fps:.1f} | Sai lệch: {dur_diff:.2f}s"
        )

        # Flush cache size limit after batch completion
        if self.render_cache and hasattr(self.render_cache, 'flush'):
            try:
                self.render_cache.flush()
            except Exception:
                pass

        # Step 11: Clean up temp
        self.on_video_progress(100.0)
        if self.config.audio_mode in ("random_audio_pool", "bgm"):
            self.on_audio_progress(100.0)
        self.on_progress(100.0, f"Hoàn tất video #{video_index}/{total_videos}!")
        return True


class VideoProcessorWorker(QThread):
    """
    Qt QThread Worker for background execution without blocking the GUI.
    Supports Video rendering, Video + Random Audio muxing, and Standalone Audio export.
    """
    batch_started = Signal(int)  # total videos / tracks
    video_started = Signal(int, int, str)  # cur_idx, total_idx, output_path
    segment_progress = Signal(int, int, object)  # cur_seg, total_seg, ClipSegment
    overall_progress = Signal(float, str)  # percent, status message
    video_progress = Signal(float)  # percent (0.0 - 100.0)
    audio_progress = Signal(float)  # percent (0.0 - 100.0)
    log_emitted = Signal(str, str)  # level, message
    video_completed = Signal(int, str)  # video_idx, output_path
    batch_finished = Signal(int, int)  # success_count, total_count
    error_occurred = Signal(str)

    def __init__(
        self,
        batch_plans: List[List[SegmentInfo]],
        output_config: OutputConfig,
        audio_playlists: Optional[List[List[AudioPlaylistItem]]] = None,
        record_history: bool = False,
        render_cache: Optional[Any] = None,
        job_id: Optional[str] = None,
        history_repo: Optional[Any] = None,
        parent=None,
        *,
        source_metadata: Optional[Dict[str, VideoInfo]] = None,
        scratch_dir: Optional[str] = None
    ):
        super().__init__(parent)
        self.batch_plans = batch_plans
        self.output_config = output_config
        self.audio_playlists = audio_playlists or []
        self.record_history = record_history
        self.render_cache = render_cache
        self.job_id = job_id
        self.history_repo = history_repo
        self.source_metadata = dict(source_metadata or {})
        self.scratch_dir = scratch_dir
        self._cancel_requested = False
        self.engine: Optional[VideoProcessorEngine] = None
        self.audio_engine: Optional[AudioProcessorEngine] = None

    def pause(self):
        if self.engine:
            self.engine.pause()
        if self.audio_engine:
            self.audio_engine.pause()

    def resume(self):
        if self.engine:
            self.engine.resume()
        if self.audio_engine:
            self.audio_engine.resume()

    def cancel(self):
        self._cancel_requested = True
        if self.engine:
            self.engine.cancel()
        if self.audio_engine:
            self.audio_engine.cancel()

    def run(self):
        out_dir = self.output_config.output_dir or os.getcwd()
        os.makedirs(out_dir, exist_ok=True)
        reserved_paths: set = set()

        # Case 1: Standalone Audio Export
        if self.output_config.export_target == "audio_only":
            if not self.audio_playlists or not any(self.audio_playlists):
                self.error_occurred.emit("Không có playlist âm thanh để xuất.")
                self.batch_finished.emit(0, 1)
                return

            total_audios = len(self.audio_playlists)
            self.batch_started.emit(total_audios)
            fmt = getattr(self.output_config, "audio_output_format", "mp3")
            success_count = 0

            self.audio_engine = AudioProcessorEngine(
                on_log=lambda lvl, msg: self.log_emitted.emit(lvl, msg),
                on_progress=lambda pct, msg: (
                    self.overall_progress.emit(pct, msg),
                    self.audio_progress.emit(pct)
                ),
                scratch_dir=self.scratch_dir
            )
            if self._cancel_requested:
                self.audio_engine.cancel()

            for a_idx, playlist in enumerate(self.audio_playlists):
                if self.audio_engine and self.audio_engine._is_cancelled:
                    break

                audio_num = a_idx + 1
                out_path = get_next_output_path(
                    output_dir=out_dir,
                    ext=fmt,
                    prefix=self.output_config.output_prefix,
                    reserved_paths=reserved_paths
                )
                self.video_started.emit(audio_num, total_audios, out_path)
                self.video_progress.emit(0.0)
                self.audio_progress.emit(0.0)

                ok = self.audio_engine.process_audio_playlist(
                    playlist=playlist,
                    output_file_path=out_path,
                    output_format=fmt
                )
                if ok:
                    self.audio_progress.emit(100.0)
                    success_count += 1
                    self.video_completed.emit(audio_num, out_path)
                else:
                    if self.audio_engine and self.audio_engine._is_cancelled:
                        self.log_emitted.emit("WARNING", f"Đã dừng xử lý file âm thanh #{audio_num}.")
                        break
                    else:
                        self.error_occurred.emit(f"Lỗi xuất file âm thanh #{audio_num}.")

            self.batch_finished.emit(success_count, total_audios)
            return

        # Case 2: Video or Video + Random Audio Combined
        total_videos = len(self.batch_plans)
        self.batch_started.emit(total_videos)
        success_count = 0

        self.engine = VideoProcessorEngine(
            output_config=self.output_config,
            on_log=lambda lvl, msg: self.log_emitted.emit(lvl, msg),
            on_segment_done=lambda idx, tot, seg: self.segment_progress.emit(idx, tot, seg),
            on_progress=lambda pct, msg: self.overall_progress.emit(pct, msg),
            render_cache=self.render_cache,
            source_metadata=self.source_metadata,
            on_video_progress=lambda pct: self.video_progress.emit(pct),
            on_audio_progress=lambda pct: self.audio_progress.emit(pct),
            scratch_dir=self.scratch_dir
        )
        if self._cancel_requested:
            self.engine.cancel()

        try:
            for v_i, plan in enumerate(self.batch_plans):
                if self.engine._is_cancelled:
                    break

                video_idx = v_i + 1
                out_path = get_next_output_path(
                    output_dir=out_dir,
                    ext="mp4",
                    prefix=self.output_config.output_prefix,
                    reserved_paths=reserved_paths
                )
                self.video_started.emit(video_idx, total_videos, out_path)
                self.video_progress.emit(0.0)
                self.audio_progress.emit(0.0)

                audio_pl = self.audio_playlists[v_i] if v_i < len(self.audio_playlists) else (
                    self.audio_playlists[0] if self.audio_playlists else None
                )

                ok = self.engine.process_video_plan(
                    plan=plan,
                    output_file_path=out_path,
                    video_index=video_idx,
                    total_videos=total_videos,
                    audio_playlist=audio_pl
                )

                if ok:
                    self.video_progress.emit(100.0)
                    if audio_pl or self.output_config.audio_mode == "bgm":
                        self.audio_progress.emit(100.0)
                    success_count += 1
                    # Commit history to SQLite and remove this plan's reservations (V02)
                    if self.history_repo and self.job_id:
                        try:
                            self.history_repo.commit_job_history(self.job_id, plan)
                        except Exception as hist_err:
                            self.log_emitted.emit("WARNING", f"Lỗi lưu lịch sử SQLite: {hist_err}")
                    if self.record_history:
                        try:
                            added = HistoryManager().record_exported_segments(plan)
                            if added > 0:
                                self.log_emitted.emit("INFO", f"Đã lưu {added} đoạn vào lịch sử export_history.json.")
                        except Exception:
                            pass
                    self.video_completed.emit(video_idx, out_path)
                else:
                    if self.engine._is_cancelled:
                        self.log_emitted.emit("WARNING", f"Đã dừng xử lý video #{video_idx}.")
                        break
                    else:
                        self.error_occurred.emit(f"Xử lý video #{video_idx} thất bại.")
        finally:
            # Clean up any uncommitted reservations if job failed, was cancelled, or finished (V02)
            if self.history_repo and self.job_id:
                try:
                    self.history_repo.release_reservations(self.job_id)
                except Exception:
                    pass

        self.batch_finished.emit(success_count, total_videos)

