"""
Process Runner Module (V3).
Centralized, robust subprocess execution engine for FFmpeg and ffprobe with:
- Thread-safe cancellation token
- Guaranteed tree termination on Windows (no orphan child processes)
- Bounded stderr ring-buffer to prevent memory leakage
- Watchdog timeout protection
"""
import os
import sys
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import List, Optional, Callable, Deque


class CancellationToken:
    """Thread-safe cooperative cancellation token."""

    def __init__(self):
        self._is_cancelled = threading.Event()
        self._callbacks: List[Callable[[], None]] = []
        self._lock = threading.Lock()

    def cancel(self):
        with self._lock:
            self._is_cancelled.set()
            for cb in self._callbacks:
                try:
                    cb()
                except Exception:
                    pass

    def is_cancelled(self) -> bool:
        return self._is_cancelled.is_set()

    def register_callback(self, cb: Callable[[], None]):
        with self._lock:
            if self._is_cancelled.is_set():
                cb()
            else:
                self._callbacks.append(cb)

    def reset(self):
        with self._lock:
            self._is_cancelled.clear()
            self._callbacks.clear()


@dataclass
class ProcessResult:
    """Result of an executed subprocess."""
    returncode: int
    stdout: str
    stderr: str
    is_cancelled: bool
    duration_sec: float

    @property
    def is_success(self) -> bool:
        return self.returncode == 0 and not self.is_cancelled


class ProcessRunner:
    """Robust runner executing CLI tools with tree termination and memory safety."""

    @staticmethod
    def kill_process_tree(pid: int, proc: Optional[subprocess.Popen] = None):
        """Forcefully terminates a process and all its child processes on Windows."""
        if proc:
            try:
                proc.kill()
            except Exception:
                pass

        if pid <= 0:
            return

        if os.name == 'nt':
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=1.5
                )
            except Exception:
                pass

        try:
            os.kill(pid, 9)
        except Exception:
            pass

    @classmethod
    def run(
        cls,
        cmd: List[str],
        cancellation_token: Optional[CancellationToken] = None,
        timeout_sec: Optional[float] = None,
        cwd: Optional[str] = None,
        max_stderr_lines: int = 100
    ) -> ProcessResult:
        """
        Executes a CLI command synchronously with watchdog and cancellation.
        """
        if cancellation_token and cancellation_token.is_cancelled():
            return ProcessResult(
                returncode=-1,
                stdout="",
                stderr="Process was cancelled before starting.",
                is_cancelled=True,
                duration_sec=0.0
            )

        start_time = time.time()
        stderr_buffer: Deque[str] = deque(maxlen=max_stderr_lines)

        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            cwd=cwd,
            creationflags=creation_flags,
            text=True,
            encoding="utf-8",
            errors="replace"
        )

        def on_cancel():
            cls.kill_process_tree(proc.pid, proc=proc)

        if cancellation_token:
            cancellation_token.register_callback(on_cancel)

        # Background thread to continuously read stderr without blocking
        def read_stderr():
            try:
                if proc.stderr:
                    for line in iter(proc.stderr.readline, ''):
                        if line:
                            stderr_buffer.append(line.rstrip('\r\n'))
            except Exception:
                pass
            finally:
                try:
                    if proc.stderr and not proc.stderr.closed:
                        proc.stderr.close()
                except Exception:
                    pass

        reader_thread = threading.Thread(target=read_stderr, daemon=True)
        reader_thread.start()

        timed_out = False
        try:
            proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            timed_out = True
            cls.kill_process_tree(proc.pid, proc=proc)
            try:
                proc.wait(timeout=2.0)
            except Exception:
                pass

        reader_thread.join(timeout=1.0)
        try:
            if proc.stderr and not proc.stderr.closed:
                proc.stderr.close()
        except Exception:
            pass
        elapsed = round(time.time() - start_time, 3)

        is_cancelled = cancellation_token.is_cancelled() if cancellation_token else False
        stderr_output = "\n".join(stderr_buffer)
        if timed_out:
            stderr_output += f"\n[ERROR] Process timed out after {timeout_sec}s."

        return ProcessResult(
            returncode=proc.returncode if not is_cancelled else -1,
            stdout="",
            stderr=stderr_output,
            is_cancelled=is_cancelled,
            duration_sec=elapsed
        )
