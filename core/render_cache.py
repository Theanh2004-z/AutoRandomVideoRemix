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
from typing import Optional, Dict, Any, List

from core.app_paths import get_cache_dir


class RenderCache:
    """Manages cached pre-rendered segments with LRU eviction."""

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        max_size_bytes: int = 5 * 1024 * 1024 * 1024,  # 5 GB default
        enabled: bool = True
    ):
        self.cache_dir = cache_dir or get_cache_dir()
        self.max_size_bytes = max_size_bytes
        self.enabled = enabled
        self._access_order: Dict[str, float] = {}
        if self.enabled:
            os.makedirs(self.cache_dir, exist_ok=True)

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
        source_size: Optional[int] = None,
        source_mtime: Optional[float] = None
    ) -> str:
        """
        Computes a deterministic SHA-256 fingerprint for a rendered segment.
        Takes source identity + temporal interval + output profile.
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

        key_components = [
            abs_src,
            str(source_size),
            f"{source_mtime:.3f}",
            f"{start_sec:.3f}",
            f"{duration:.3f}",
            f"{width}x{height}",
            f"{fps:.2f}",
            str(aspect_ratio_mode),
            str(encoder),
            str(audio_mode)
        ]
        raw_key = "::".join(key_components)
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24]

    def get_cache_path(self, cache_key: str) -> str:
        """Returns the file path for a cache key."""
        return os.path.join(self.cache_dir, f"{cache_key}.mp4")

    def get(self, cache_key: str) -> Optional[str]:
        """
        Retrieves a cached clip if it exists and is valid.
        Updates access time for LRU tracking.
        """
        if not self.enabled:
            return None

        path = self.get_cache_path(cache_key)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
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

        target_path = self.get_cache_path(cache_key)
        staging_path = target_path + f".tmp_{os.getpid()}_{int(time.time()*1000)}"

        try:
            shutil.copy2(rendered_file_path, staging_path)
            os.replace(staging_path, target_path)
            self._access_order[cache_key] = time.time()
            self._ensure_size_limit()
            return target_path
        except Exception:
            if os.path.exists(staging_path):
                try:
                    os.remove(staging_path)
                except Exception:
                    pass
            return None

    def _ensure_size_limit(self):
        """Evicts oldest accessed files until total size is within max_size_bytes."""
        if not self.enabled or self.max_size_bytes <= 0:
            return

        entries = self._get_cache_entries()
        total_size = sum(e["size"] for e in entries)

        if total_size <= self.max_size_bytes:
            return

        # Sort by access_time ascending (oldest first)
        entries.sort(key=lambda x: x["access_time"])

        for entry in entries:
            try:
                os.remove(entry["path"])
                self._access_order.pop(entry["key"], None)
                total_size -= entry["size"]
            except Exception:
                pass
            if total_size <= self.max_size_bytes:
                break

    def _get_cache_entries(self) -> List[Dict[str, Any]]:
        """Scans cache directory and returns file stats."""
        entries = []
        if not os.path.isdir(self.cache_dir):
            return entries

        for name in os.listdir(self.cache_dir):
            if name.endswith(".mp4") and not name.startswith(".tmp_"):
                path = os.path.join(self.cache_dir, name)
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
        self._access_order.clear()
        if not os.path.isdir(self.cache_dir):
            return 0

        for name in os.listdir(self.cache_dir):
            path = os.path.join(self.cache_dir, name)
            try:
                if os.path.isfile(path) or os.path.islink(path):
                    os.remove(path)
                    deleted += 1
            except Exception:
                pass
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
