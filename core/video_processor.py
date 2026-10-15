"""
FFmpeg Video Processor Engine.
Handles trimming, resolution & FPS normalization, aspect ratio fitting/cropping,
audio stripping / keeping / background music injection, seamless concatenation,
probe validation, and pause/cancel handling.
"""
import os
import shutil
import subprocess
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

from PySide6.QtCore import QThread, Signal


from core.hardware_detector import detect_hardware, HardwareProfile


class VideoProcessorEngine:
    """Core processor that runs FFmpeg rendering jobs."""

    def __init__(
        self,
        output_config: OutputConfig,
        on_log: Optional[Callable[[str, str], None]] = None,
        on_segment_done: Optional[Callable[[int, int, SegmentInfo], None]] = None,
        on_progress: Optional[Callable[[float, str], None]] = None,
        render_cache: Optional[Any] = None
    ):
        self.config = output_config
        self.on_log = on_log or (lambda lvl, msg: None)
        self.on_segment_done = on_segment_done or (lambda idx, total, seg: None)
        self.on_progress = on_progress or (lambda pct, msg: None)
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

    def pause(self):
        self._is_paused = True
        self.on_log("INFO", "Tạm dừng tiến trình xử lý...")

    def resume(self):
        self._is_paused = False
        self.on_log("INFO", "Tiếp tục tiến trình xử lý...")

    def cancel(self):
        self._is_cancelled = True
        self.on_log("WARNING", "Đang hủy tiến trình...")
        if self._current_subprocess:
            try:
                from core.process_runner import ProcessRunner
                ProcessRunner.kill_process_tree(self._current_subprocess.pid)
            except Exception:
                try:
                    self._current_subprocess.kill()
                except Exception:
                    pass
        self._cleanup_temp_dir()

    def _cleanup_temp_dir(self):
        if self._temp_dir and os.path.isdir(self._temp_dir):
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
            except Exception as e:
                self.on_log("WARNING", f"Không thể xóa thư mục tạm: {e}")

    def _build_filter_complex(self, target_w: int, target_h: int) -> str:
        """
        Builds scaling and aspect ratio video filter.
        - 'fit': keep aspect ratio and pad black borders.
        - 'crop': scale to fill and crop excess.
        """
        if self.config.aspect_ratio_mode == "crop":
            # Scale to cover then crop
            return (
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
                f"crop={target_w}:{target_h},"
                f"setsar=1,format=yuv420p"
            )
        else:
            # Fit & pad (default)
            return (
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
                f"setsar=1,format=yuv420p"
            )

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
            profile = detect_hardware()
            req = (getattr(self.config, "hw_accel", None) or "auto").lower()

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

        # Quality mapping per encoder type
        if target_encoder == "h264_nvenc":
            cq_map = {"standard": "24", "high": "19", "very_high": "16"}
            cq_val = cq_map.get(self.config.quality, "19")
            opts = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", cq_val]
        elif target_encoder == "h264_amf":
            qp_map = {"standard": "24", "high": "20", "very_high": "17"}
            qp_val = qp_map.get(self.config.quality, "20")
            opts = ["-c:v", "h264_amf", "-usage", "transcoding", "-quality", "speed", "-rc", "cqp", "-qp_p", qp_val, "-qp_i", qp_val]
        elif target_encoder == "h264_qsv":
            gq_map = {"standard": "25", "high": "21", "very_high": "18"}
            gq_val = gq_map.get(self.config.quality, "21")
            opts = ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", gq_val]
        else:
            crf_map = {"standard": "23", "high": "19", "very_high": "16"}
            crf_val = crf_map.get(self.config.quality, "19")
            opts = ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf_val, "-threads", "0"]

        return target_encoder, opts

    def process_video_plan(
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

        # Create unique temporary workspace
        temp_id = uuid.uuid4().hex[:8]
        self._temp_dir = os.path.join(
            os.path.dirname(os.path.abspath(output_file_path)),
            f"_temp_remix_{temp_id}"
        )
        os.makedirs(self._temp_dir, exist_ok=True)

        target_w, target_h = self.config.width_height
        target_fps = self.config.fps
        vf_string = self._build_filter_complex(target_w, target_h)
        encoder_name, encoder_opts = self._determine_encoder()

        total_segments = len(plan)
        temp_segment_files: List[str] = []

        self.on_log("INFO", f"=== Bắt đầu tạo video #{video_index}/{total_videos}: {os.path.basename(output_file_path)} ===")
        self.on_log("INFO", f"Tổng số đoạn cắt: {total_segments} đoạn | Độ phân giải: {target_w}x{target_h} | FPS: {target_fps}")

        # Step 5 & 6: Cut & Normalize each segment
        for i, clip in enumerate(plan):
            # Check pause/cancel
            while self._is_paused and not self._is_cancelled:
                time.sleep(0.3)

            if self._is_cancelled:
                self.on_log("WARNING", "Tiến trình đã bị hủy bởi người dùng.")
                self._cleanup_temp_dir()
                return False

            seg_filename = f"seg_{i:05d}.mp4"
            seg_path = os.path.join(self._temp_dir, seg_filename)

            # Inform status
            clip.status = "Đang cắt"
            pct = round((i / total_segments) * 85.0, 1)
            self.on_progress(pct, f"Video #{video_index}/{total_videos}: Cắt đoạn {i+1}/{total_segments} ({clip.source_video_name})")

            # Check render cache first
            cache_key = None
            if self.render_cache and self.render_cache.enabled:
                try:
                    cache_key = self.render_cache.compute_key(
                        source_path=clip.source_video_path,
                        start_sec=clip.start_sec,
                        duration=clip.effective_duration,
                        width=target_w,
                        height=target_h,
                        fps=target_fps,
                        aspect_ratio_mode=self.config.aspect_ratio_mode,
                        encoder=encoder_name,
                        audio_mode=self.config.audio_mode,
                        quality=getattr(self.config, "quality", "standard"),
                        encoder_opts=encoder_opts
                    )
                    cached_file = self.render_cache.get(cache_key)
                    if cached_file and self.render_cache.verify_entry(cached_file):
                        shutil.copy2(cached_file, seg_path)
                        clip.status = "Hoàn tất"
                        self.on_log("INFO", f"[{i+1}/{total_segments}] ⚡ Cache hit: {clip.source_video_name} ({clip.formatted_start}-{clip.formatted_end})")
                        self.on_segment_done(i + 1, total_segments, clip)
                        temp_segment_files.append(seg_path)
                        continue
                except Exception:
                    pass

            self.on_log(
                "INFO",
                f"[{i+1}/{total_segments}] Cắt {clip.source_video_name} "
                f"từ {clip.formatted_start} đến {clip.formatted_end} ({clip.effective_duration:.3f}s)..."
            )

            # Build FFmpeg segment command
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(clip.start_sec),
                "-t", str(clip.effective_duration),
                "-i", clip.source_video_path,
                "-vf", vf_string,
                "-r", str(target_fps),
            ]
            cmd.extend(encoder_opts)

            # Audio handling during segment cut
            if self.config.audio_mode == "keep":
                # Normalize audio to AAC 48k stereo
                cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"])
            else:
                # 'none', 'bgm', or 'random_audio_pool': Strip audio stream entirely (-an)
                cmd.append("-an")

            cmd.append(seg_path)

            try:
                self._current_subprocess = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )
                _, stderr = self._current_subprocess.communicate()

                if self._is_cancelled:
                    self._cleanup_temp_dir()
                    return False

                if self._current_subprocess.returncode != 0:
                    err_text = stderr.decode("utf-8", errors="replace") if stderr else "Unknown error"
                    if encoder_name != "libx264":
                        self.on_log("WARNING", f"GPU Encoder ({encoder_name}) gặp sự cố tại đoạn {i+1}: {err_text[:120]}... Đang tự động chuyển sang CPU Fallback (libx264) để hoàn thành đoạn!")
                        encoder_name, encoder_opts = self._determine_encoder(force_cpu=True)
                        # Recompute cache key with actual CPU encoder settings (R06)
                        if self.render_cache and self.render_cache.enabled:
                            try:
                                cache_key = self.render_cache.compute_key(
                                    source_path=clip.source_video_path,
                                    start_sec=clip.start_sec,
                                    duration=clip.effective_duration,
                                    width=target_w,
                                    height=target_h,
                                    fps=target_fps,
                                    aspect_ratio_mode=self.config.aspect_ratio_mode,
                                    encoder=encoder_name,
                                    audio_mode=self.config.audio_mode,
                                    quality=getattr(self.config, "quality", "standard"),
                                    encoder_opts=encoder_opts
                                )
                            except Exception:
                                pass
                        fallback_cmd = [
                            "ffmpeg", "-y",
                            "-ss", str(clip.start_sec),
                            "-t", str(clip.effective_duration),
                            "-i", clip.source_video_path,
                            "-vf", vf_string,
                            "-r", str(target_fps),
                        ]
                        fallback_cmd.extend(encoder_opts)
                        if self.config.audio_mode == "keep":
                            fallback_cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"])
                        else:
                            fallback_cmd.append("-an")
                        fallback_cmd.append(seg_path)

                        self._current_subprocess = subprocess.Popen(
                            fallback_cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                        )
                        _, stderr_fb = self._current_subprocess.communicate()
                        if self._current_subprocess.returncode != 0:
                            err_fb = stderr_fb.decode("utf-8", errors="replace") if stderr_fb else "CPU error"
                            self.on_log("ERROR", f"Lỗi cắt đoạn {i+1} bằng CPU Fallback: {err_fb[:300]}")
                            clip.status = "Lỗi"
                            self._cleanup_temp_dir()
                            return False
                    else:
                        self.on_log("ERROR", f"Lỗi cắt đoạn {i+1} ({clip.source_video_name}): {err_text[:300]}")
                        clip.status = "Lỗi"
                        self._cleanup_temp_dir()
                        return False

                clip.status = "Hoàn tất"
                if self.render_cache and self.render_cache.enabled and cache_key:
                    try:
                        self.render_cache.put(cache_key, seg_path)
                    except Exception:
                        pass
                temp_segment_files.append(seg_path)
                self.on_segment_done(i + 1, total_segments, clip)

            except Exception as e:
                self.on_log("ERROR", f"Ngoại lệ khi cắt đoạn {i+1}: {e}")
                clip.status = "Lỗi"
                self._cleanup_temp_dir()
                return False

        # Step 7: Concatenate all segments seamlessly
        self.on_progress(88.0, f"Video #{video_index}/{total_videos}: Đang ghép nối các đoạn video...")
        self.on_log("INFO", "Ghép nối tất cả các đoạn video thành file hoàn chỉnh...")

        concat_list_file = os.path.join(self._temp_dir, "concat_list.txt")
        with open(concat_list_file, "w", encoding="utf-8") as f:
            for seg in temp_segment_files:
                safe_path = seg.replace("\\", "/").replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

        merged_intermediate = os.path.join(self._temp_dir, "merged_raw.mp4")
        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_file,
            "-c", "copy",
            merged_intermediate
        ]

        try:
            self._current_subprocess = subprocess.Popen(
                concat_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            _, stderr = self._current_subprocess.communicate()

            if self._is_cancelled:
                self._cleanup_temp_dir()
                return False

            if self._current_subprocess.returncode != 0:
                err_text = stderr.decode("utf-8", errors="replace") if stderr else "Unknown error"
                tail_err = "\n".join(err_text.strip().splitlines()[-5:])
                self.on_log("ERROR", f"Lỗi ghép concat video: {tail_err}")
                self._cleanup_temp_dir()
                return False

        except Exception as e:
            self.on_log("ERROR", f"Ngoại lệ khi ghép concat: {e}")
            self._cleanup_temp_dir()
            return False

        # Step 8: Audio Handling & Muxing (Random Audio Pool / BGM / None)
        final_temp_path = os.path.join(self._temp_dir, "final_ready.mp4")

        if self.config.audio_mode == "random_audio_pool" and audio_playlist:
            self.on_progress(92.0, f"Video #{video_index}/{total_videos}: Đang ghép {len(audio_playlist)} bài âm thanh ngẫu nhiên...")
            self.on_log("INFO", f"Ghép {len(audio_playlist)} bài âm thanh ngẫu nhiên từ Phần 2 vào video...")
            temp_audio_file = os.path.join(self._temp_dir, "temp_audio.wav")
            audio_engine = AudioProcessorEngine(
                on_log=self.on_log,
                on_progress=lambda p, m: self.on_progress(round(88.0 + p * 0.08, 1), m)
            )
            audio_ok = audio_engine.process_audio_playlist(
                playlist=audio_playlist,
                output_file_path=temp_audio_file,
                output_format="wav"
            )
            if audio_ok and os.path.isfile(temp_audio_file):
                self.on_log("INFO", "Mux âm thanh ngẫu nhiên vào file video MP4...")
                mux_cmd = [
                    "ffmpeg", "-y",
                    "-i", merged_intermediate,
                    "-i", temp_audio_file,
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k",
                    final_temp_path
                ]
                try:
                    self._current_subprocess = subprocess.Popen(
                        mux_cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                    )
                    _, stderr = self._current_subprocess.communicate()
                    if self._current_subprocess.returncode != 0:
                        err_text = stderr.decode("utf-8", errors="replace") if stderr else "Unknown error"
                        self.on_log("ERROR", f"Lỗi mux âm thanh vào video: {err_text[:300]}")
                        self._cleanup_temp_dir()
                        return False
                except Exception as e:
                    self.on_log("ERROR", f"Ngoại lệ khi mux âm thanh vào video: {e}")
                    self._cleanup_temp_dir()
                    return False
            else:
                self.on_log("ERROR", "Không thể chuẩn bị file âm thanh ngẫu nhiên từ kho nhạc.")
                self._cleanup_temp_dir()
                return False

        elif self.config.audio_mode == "random_audio_pool" and not audio_playlist:
            self.on_log("ERROR", "Lỗi: Danh sách phát âm thanh ngẫu nhiên rỗng, không thể ghép.")
            self._cleanup_temp_dir()
            return False

        elif self.config.audio_mode == "bgm" and os.path.isfile(self.config.bgm_path):
            self.on_progress(95.0, f"Video #{video_index}/{total_videos}: Đang chèn nhạc nền...")
            self.on_log("INFO", f"Chèn nhạc nền: {os.path.basename(self.config.bgm_path)}...")
            bgm_cmd = [
                "ffmpeg", "-y",
                "-i", merged_intermediate,
                "-stream_loop", "-1",
                "-i", self.config.bgm_path,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                final_temp_path
            ]
            try:
                self._current_subprocess = subprocess.Popen(
                    bgm_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )
                _, stderr = self._current_subprocess.communicate()
                if self._current_subprocess.returncode != 0:
                    err_text = stderr.decode("utf-8", errors="replace") if stderr else "Unknown error"
                    self.on_log("ERROR", f"Lỗi chèn nhạc nền: {err_text[:300]}")
                    self._cleanup_temp_dir()
                    return False
            except Exception as e:
                self.on_log("ERROR", f"Ngoại lệ khi chèn nhạc nền: {e}")
                self._cleanup_temp_dir()
                return False
        else:
            shutil.move(merged_intermediate, final_temp_path)

        # Step 9: Probe Validation & Atomic 2-Phase Publication (V3)
        self.on_progress(98.0, f"Video #{video_index}/{total_videos}: Đang kiểm tra file xuất...")
        self.on_log("INFO", "Kiểm tra tính toàn vẹn của video đầu ra bằng OutputValidator...")

        expected_dur = sum(c.effective_duration for c in plan)
        target_fps = float(self.config.fps)
        val_result = OutputValidator.validate_video(
            file_path=final_temp_path,
            expected_duration=expected_dur,
            expected_width=target_w,
            expected_height=target_h,
            expected_fps=target_fps,
            require_audio=(self.config.audio_mode in ("random_audio_pool", "bgm")),
            duration_tolerance_sec=1.0
        )

        if not val_result.is_valid:
            self.on_log("ERROR", f"Kiểm tra chất lượng thất bại: {val_result.error_msg}")
            OutputCommit.discard_staging(final_temp_path)
            self._cleanup_temp_dir()
            return False

        # Step 10: Atomic publication via OutputCommit (prevents overwrite)
        ok, commit_msg = OutputCommit.publish_file(
            staging_path=final_temp_path,
            final_destination_path=output_file_path,
            validation=val_result,
            allow_overwrite=False
        )
        if not ok:
            self.on_log("ERROR", commit_msg)
            self._cleanup_temp_dir()
            return False

        dur_diff = abs((val_result.actual_duration or 0.0) - expected_dur)
        self.on_log(
            "SUCCESS",
            f"Kiểm tra & công bố thành công! File: {os.path.basename(output_file_path)} | "
            f"Thời lượng thực tế: {val_result.actual_duration:.2f}s | "
            f"Độ phân giải: {val_result.actual_width}x{val_result.actual_height} | "
            f"FPS: {val_result.actual_fps:.1f} | Sai lệch: {dur_diff:.2f}s"
        )

        # Step 11: Clean up temp
        self._cleanup_temp_dir()
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
        parent=None
    ):
        super().__init__(parent)
        self.batch_plans = batch_plans
        self.output_config = output_config
        self.audio_playlists = audio_playlists or []
        self.record_history = record_history
        self.render_cache = render_cache
        self.job_id = job_id
        self.history_repo = history_repo
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
                on_progress=lambda pct, msg: self.overall_progress.emit(pct, msg)
            )

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

                ok = self.audio_engine.process_audio_playlist(
                    playlist=playlist,
                    output_file_path=out_path,
                    output_format=fmt
                )
                if ok:
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
            render_cache=self.render_cache
        )

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

