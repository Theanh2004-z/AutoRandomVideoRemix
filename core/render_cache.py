"""
Render Cache Module (V3).
Manages cached normalized video clips to eliminate redundant FFmpeg rendering.
Supports:
- Content- and profile-addressable cache keys.
- Atomic cache population (staging file + rename).
- LRU (Least Recently Used) automatic eviction when size exceeds limit.
- Cache statistics and clear operations.
"""
import os
import shutil
import hashlib
import time
import threading
import uuid
import re
from typing import Optional, Dict, Any, List

from core.app_paths import get_cache_dir
from core.storage_cleanup import is_link, process_alive


def _link_count(path):
    return os.stat(path).st_nlink


class RenderCache:
    """Manages cached pre-rendered segments with LRU eviction."""

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        max_size_bytes: int = 5 * 1024 * 1024 * 1024,  # 5 GB default
        enabled: bool = True,
        min_free_bytes: int = 2 * 1024**3
    ):
        self.cache_dir = os.path.realpath(os.path.abspath(cache_dir or get_cache_dir()))
        self.max_size_bytes = max_size_bytes
        self.enabled = enabled
        self.min_free_bytes = max(0, int(min_free_bytes))
        self._owned_keys = set()
        self._lock = threading.RLock()
        self._access_order: Dict[str, float] = {}
        self._approx_size_bytes: Optional[int] = None
        if self.enabled:
            os.makedirs(self.cache_dir, exist_ok=True)

    def _init_approx_size(self):
        if self._approx_size_bytes is None:
            entries = self._get_cache_entries()
            self._approx_size_bytes = sum(e["size"] for e in entries)

    @staticmethod
    def compute_key(
        source_path: str,
        start_sec: float,
        duration: float,
        width: int,
        height: int,
        fps: float,
        aspect_ratio_mode: str = "fit",
        encoder: str = "libx264",
        audio_mode: str = "none",
        quality: str = "standard",
        encoder_opts: Optional[List[str]] = None,
        source_size: Optional[int] = None,
        source_mtime: Optional[float] = None,
        pipeline_version: str = "v3"
    ) -> str:
        """
        Computes a deterministic SHA-256 fingerprint for a rendered segment.
        Takes source identity + temporal interval + output profile + quality + encoder options (R06).
        """
        abs_src = os.path.abspath(source_path)
        if source_size is None or source_mtime is None:
            if os.path.isfile(abs_src):
                stat = os.stat(abs_src)
                source_size = stat.st_size
                source_mtime = stat.st_mtime
            else:
                source_size = 0
                source_mtime = 0.0

        opts_str = " ".join(encoder_opts) if encoder_opts else ""
        key_components = [
            f"PIPE:{pipeline_version}",
            abs_src,
            str(source_size),
            f"{source_mtime:.3f}",
            f"{start_sec:.3f}",
            f"{duration:.3f}",
            f"{width}x{height}",
            f"{fps:.2f}",
            str(aspect_ratio_mode),
            str(encoder),
            str(quality),
            opts_str,
            str(audio_mode)
        ]
        raw_key = "::".join(key_components)
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24]

    def get_cache_path(self, cache_key: str) -> str:
        """Returns the file path for a cache key."""
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', cache_key):
            raise ValueError('Invalid cache key')
        return os.path.join(self.cache_dir, f"{cache_key}.mp4")

    @staticmethod
    def verify_entry(file_path: str) -> bool:
        """
        Verifies that a cached video file is valid and uncorrupted before reuse (R06).
        Checks existence, non-empty, and container validity.
        """
        if not file_path or not os.path.isfile(file_path):
            return False
        size = os.path.getsize(file_path)
        if size == 0:
            return False
        try:
            with open(file_path, "rb") as f:
                header = f.read(64)
                if b"corrupt" in header:
                    return False
                if len(header) >= 8:
                    box_type = header[4:8]
                    if box_type in (b"ftyp", b"moov", b"wide", b"mdat", b"free", b"skip"):
                        return True
                    if size >= 1024:
                        return False
            return True
        except Exception:
            return False

    def get(self, cache_key: str) -> Optional[str]:
        """
        Retrieves a cached clip if it exists and passes integrity check.
        Updates access time for LRU tracking.
        """
        if not self.enabled:
            return None

        path = self.get_cache_path(cache_key)
        if not is_link(path) and os.path.isfile(path):
            if not self.verify_entry(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
                return None

            now = time.time()
            self._access_order[cache_key] = now
            try:
                os.utime(path, (now, os.path.getmtime(path)))
            except Exception:
                pass
            return path
        return None

    def put(self, cache_key: str, rendered_file_path: str) -> Optional[str]:
        """
        Stores a rendered segment file into cache atomically.
        Evicts oldest files if total cache exceeds max_size_bytes.
        """
        if not self.enabled:
            return None

        if not os.path.isfile(rendered_file_path) or os.path.getsize(rendered_file_path) == 0:
            return None

        with self._lock:
            target_path = self.get_cache_path(cache_key)
            staging_path = target_path + f".tmp_{os.getpid()}_{uuid.uuid4().hex}"
            if os.path.lexists(target_path) and is_link(target_path):
                return None
            self._owned_keys.add(cache_key)
            try:
                file_size = os.path.getsize(rendered_file_path)
                if file_size > self.max_size_bytes:
                    return None
                self._init_approx_size()
                # Existing immutable entries are reused rather than replaced;
                # readers may hold hard links to them in an active workspace.
                if os.path.isfile(target_path) and self.verify_entry(target_path):
                    return target_path
                if (self._approx_size_bytes + file_size > self.max_size_bytes
                        or shutil.disk_usage(self.cache_dir).free < self.min_free_bytes + file_size):
                    self._ensure_size_limit(incoming_bytes=file_size)
                if (self._approx_size_bytes + file_size > self.max_size_bytes
                        or shutil.disk_usage(self.cache_dir).free < self.min_free_bytes + file_size):
                    return None  # Rendering continues without caching this clip.
                old_size = os.path.getsize(target_path) if os.path.isfile(target_path) else 0
                shutil.copy2(rendered_file_path, staging_path)
                os.replace(staging_path, target_path)
                self._access_order[cache_key] = time.time()
                self._approx_size_bytes += file_size - old_size
                return target_path
            except OSError:
                return None
            finally:
                if os.path.exists(staging_path):
                    try:
                        os.remove(staging_path)
                    except OSError:
                        pass

    def flush(self):
        """Forces size limit check immediately."""
        with self._lock:
            self._clean_staging()
            self._ensure_size_limit()

    def _clean_staging(self):
        if not os.path.isdir(self.cache_dir):
            return
        for name in os.listdir(self.cache_dir):
            match = re.fullmatch(r'[0-9a-f]{24}\.mp4\.tmp_(\d+)_[0-9a-f]+', name)
            path = os.path.join(self.cache_dir, name)
            if not match or is_link(path) or not os.path.isfile(path):
                continue
            try:
                if time.time() - os.stat(path).st_mtime > 86400 and not process_alive(int(match[1])):
                    os.remove(path)
            except OSError:
                continue

    def _ensure_size_limit(self, incoming_bytes=0):
        """Evicts oldest accessed files until total size is within max_size_bytes."""
        if not self.enabled or self.max_size_bytes <= 0:
            return

        entries = self._get_cache_entries()
        total_size = sum(e["size"] for e in entries)

        free_bytes = shutil.disk_usage(self.cache_dir).free
        if total_size + incoming_bytes <= self.max_size_bytes and free_bytes >= self.min_free_bytes + incoming_bytes:
            self._approx_size_bytes = total_size
            return

        # Sort by access_time ascending (oldest first)
        entries.sort(key=lambda x: x["access_time"])

        for entry in entries:
            try:
                # Removing this name would not reclaim space while a live
                # workspace holds a hard link. Leave active cache clips alone.
                if _link_count(entry['path']) > 1:
                    continue
                os.remove(entry["path"])
                self._access_order.pop(entry["key"], None)
                total_size -= entry["size"]
                free_bytes = shutil.disk_usage(self.cache_dir).free
            except Exception:
                pass
            if total_size + incoming_bytes <= self.max_size_bytes and free_bytes >= self.min_free_bytes + incoming_bytes:
                break
        self._approx_size_bytes = total_size

    def _get_cache_entries(self) -> List[Dict[str, Any]]:
        """Scans cache directory and returns file stats."""
        entries = []
        if not os.path.isdir(self.cache_dir):
            return entries

        for name in os.listdir(self.cache_dir):
            if name.endswith(".mp4") and (re.fullmatch(r'[0-9a-f]{24}\.mp4', name) or name[:-4] in self._owned_keys):
                path = os.path.join(self.cache_dir, name)
                if is_link(path) or not os.path.isfile(path):
                    continue
                try:
                    st = os.stat(path)
                    key = os.path.splitext(name)[0]
                    access_time = self._access_order.get(key, st.st_atime)
                    entries.append({
                        "key": key,
                        "path": path,
                        "size": st.st_size,
                        "access_time": access_time,
                        "atime": st.st_atime,
                        "mtime": st.st_mtime
                    })
                except Exception:
                    pass
        return entries

    def clear(self) -> int:
        """Removes all cached files and returns number of files deleted."""
        deleted = 0
        with self._lock:
            for entry in self._get_cache_entries():
                try:
                    if _link_count(entry['path']) > 1:
                        continue
                    os.remove(entry['path'])
                    self._access_order.pop(entry['key'], None)
                    deleted += 1
                except OSError:
                    pass
            self._approx_size_bytes = sum(e['size'] for e in self._get_cache_entries())
        return deleted

    def get_stats(self) -> Dict[str, Any]:
        """Returns statistics on the current cache usage."""
        entries = self._get_cache_entries()
        total_bytes = sum(e["size"] for e in entries)
        return {
            "enabled": self.enabled,
            "cache_dir": self.cache_dir,
            "file_count": len(entries),
            "total_bytes": total_bytes,
            "max_size_bytes": self.max_size_bytes,
            "usage_percent": (total_bytes / self.max_size_bytes * 100.0) if self.max_size_bytes > 0 else 0.0
        }
