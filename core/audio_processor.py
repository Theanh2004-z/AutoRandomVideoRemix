"""
Audio Processor Engine — renders, trims, standardizes and concatenates audio playlists.
Produces standalone audio files (MP3, M4A, WAV) or temporary audio for video muxing.
"""
import os
import shutil
import subprocess
import time
import uuid
from typing import List, Optional, Callable
from core.config import AudioPlaylistItem


class AudioProcessorEngine:
    """Core processor that runs FFmpeg audio jobs."""

    def __init__(
        self,
        on_log: Optional[Callable[[str, str], None]] = None,
        on_progress: Optional[Callable[[float, str], None]] = None
    ):
        self.on_log = on_log or (lambda lvl, msg: None)
        self.on_progress = on_progress or (lambda pct, msg: None)

        self._is_paused = False
        self._is_cancelled = False
        self._current_subprocess: Optional[subprocess.Popen] = None
        self._temp_dir: Optional[str] = None

    def pause(self):
        self._is_paused = True
        self.on_log("INFO", "Tạm dừng xử lý âm thanh...")

    def resume(self):
        self._is_paused = False
        self.on_log("INFO", "Tiếp tục xử lý âm thanh...")

    def cancel(self):
        self._is_cancelled = True
        self.on_log("WARNING", "Đang hủy tiến trình âm thanh...")
        if self._current_subprocess:
            try:
                self._current_subprocess.kill()
            except Exception:
                pass
        self._cleanup_temp_dir()

    def _cleanup_temp_dir(self):
        if self._temp_dir and os.path.isdir(self._temp_dir):
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
            except Exception:
                pass

    def process_audio_playlist(
        self,
        playlist: List[AudioPlaylistItem],
        output_file_path: str,
        target_sample_rate: int = 48000,
        output_format: str = "mp3"
    ) -> bool:
        """
        Renders the playlist into a single audio file.
        1. Trims and standardizes each track to normalized wav/aac in temp folder.
        2. Seamlessly concatenates all tracks.
        3. Encodes to the target format (mp3 / m4a / wav).
        """
        if not playlist:
            self.on_log("ERROR", "Danh sách bài hát rỗng.")
            return False

        if isinstance(target_sample_rate, str) and not target_sample_rate.isdigit():
            output_format = target_sample_rate
            target_sample_rate = 48000
        else:
            try:
                target_sample_rate = int(target_sample_rate)
            except Exception:
                target_sample_rate = 48000

        os.makedirs(os.path.dirname(os.path.abspath(output_file_path)), exist_ok=True)

        temp_id = uuid.uuid4().hex[:8]
        self._temp_dir = os.path.join(
            os.path.dirname(os.path.abspath(output_file_path)),
            f"_temp_audio_{temp_id}"
        )
        os.makedirs(self._temp_dir, exist_ok=True)

        total_tracks = len(playlist)
        self.on_log("INFO", f"=== Bắt đầu ghép {total_tracks} bài âm thanh ngẫu nhiên ===")

        temp_track_files: List[str] = []

        # Step 1: Normalize & Trim each track to 48kHz stereo WAV for lossless, artifact-free concat
        for i, item in enumerate(playlist):
            while self._is_paused and not self._is_cancelled:
                time.sleep(0.3)

            if self._is_cancelled:
                self._cleanup_temp_dir()
                return False

            item.status = "Đang xử lý"
            pct = round((i / total_tracks) * 85.0, 1)
            self.on_progress(pct, f"Chuẩn hóa bài {i+1}/{total_tracks}: {item.source_audio_name}")

            norm_filename = f"track_{i:04d}.wav"
            norm_path = os.path.join(self._temp_dir, norm_filename)

            cmd = ["ffmpeg", "-y"]
            if item.is_trimmed:
                cmd.extend(["-ss", "0", "-t", str(item.effective_duration)])
                self.on_log(
                    "INFO",
                    f"[{i+1}/{total_tracks}] Cắt ngắn bài cuối: {item.source_audio_name} "
                    f"-> {item.effective_duration:.2f}s (gốc: {item.duration:.2f}s)"
                )
            else:
                self.on_log(
                    "INFO",
                    f"[{i+1}/{total_tracks}] Giữ nguyên bài: {item.source_audio_name} "
                    f"({item.duration:.2f}s, Lượt #{item.cycle_index})"
                )

            cmd.extend([
                "-i", item.source_audio_path,
                "-ar", str(target_sample_rate),
                "-ac", "2",
                "-c:a", "pcm_s16le",
                norm_path
            ])

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
                    err = stderr.decode('utf-8', errors='replace') if stderr else "Unknown error"
                    tail_err = "\n".join(err.strip().splitlines()[-5:])
                    self.on_log("ERROR", f"Lỗi chuẩn hóa bài {item.source_audio_name}: {tail_err}")
                    item.status = "Lỗi"
                    self._cleanup_temp_dir()
                    return False

                item.status = "Hoàn tất"
                temp_track_files.append(norm_path)

            except Exception as e:
                self.on_log("ERROR", f"Ngoại lệ khi xử lý bài {item.source_audio_name}: {e}")
                item.status = "Lỗi"
                self._cleanup_temp_dir()
                return False

        # Step 2: Seamless concat
        self.on_progress(88.0, "Đang ghép nối các bài hát...")
        concat_list_file = os.path.join(self._temp_dir, "audio_concat_list.txt")
        with open(concat_list_file, "w", encoding="utf-8") as f:
            for tf in temp_track_files:
                # Use forward slashes to prevent FFmpeg Windows path escape issues
                safe_path = tf.replace("\\", "/").replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

        merged_wav = os.path.join(self._temp_dir, "merged.wav")
        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_file,
            "-c", "copy",
            merged_wav
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
                err = stderr.decode('utf-8', errors='replace') if stderr else "Unknown error"
                tail_err = "\n".join(err.strip().splitlines()[-5:])
                self.on_log("ERROR", f"Lỗi ghép concat audio: {tail_err}")
                self._cleanup_temp_dir()
                return False

        except Exception as e:
            self.on_log("ERROR", f"Ngoại lệ ghép concat audio: {e}")
            self._cleanup_temp_dir()
            return False

        # Step 3: Final export encoding
        self.on_progress(95.0, f"Đang xuất file âm thanh định dạng {output_format.upper()}...")
        fmt = output_format.lower()

        if fmt == "wav":
            # merged.wav is already standardized 48kHz 16-bit PCM WAV — direct copy is instant & lossless!
            try:
                if os.path.isfile(output_file_path):
                    try:
                        os.remove(output_file_path)
                    except Exception:
                        pass
                shutil.copy2(merged_wav, output_file_path)
            except Exception as e:
                self.on_log("ERROR", f"Lỗi lưu file âm thanh WAV: {e}")
                self._cleanup_temp_dir()
                return False
        else:
            export_cmd = ["ffmpeg", "-y", "-i", merged_wav]
            if fmt == "mp3":
                export_cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k"])
            elif fmt in ("m4a", "aac"):
                export_cmd.extend(["-c:a", "aac", "-b:a", "256k"])
            else:
                export_cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k"])
            export_cmd.append(output_file_path)

            try:
                self._current_subprocess = subprocess.Popen(
                    export_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )
                _, stderr = self._current_subprocess.communicate()

                if self._is_cancelled:
                    self._cleanup_temp_dir()
                    return False

                if self._current_subprocess.returncode != 0:
                    err = stderr.decode('utf-8', errors='replace') if stderr else "Unknown error"
                    tail_err = "\n".join(err.strip().splitlines()[-5:])
                    self.on_log("ERROR", f"Lỗi xuất file audio: {tail_err}")
                    self._cleanup_temp_dir()
                    return False

            except Exception as e:
                self.on_log("ERROR", f"Ngoại lệ xuất file audio: {e}")
                self._cleanup_temp_dir()
                return False

        if not os.path.isfile(output_file_path) or os.path.getsize(output_file_path) < 1000:
            self.on_log("ERROR", "File âm thanh xuất rỗng hoặc không tồn tại.")
            self._cleanup_temp_dir()
            return False

        self._cleanup_temp_dir()
        self.on_progress(100.0, "Ghép nối âm thanh hoàn tất!")
        self.on_log("SUCCESS", f"✓ Đã xuất thành công: {os.path.basename(output_file_path)}")
        return True
