"""
Job Controller & State Machine Module (V3).
Manages lifecycle of execution jobs:
- Strict state machine preventing double-starts
- Cooperative boundary-based Pause/Resume with PAUSE_REQUESTED state
- Clean cancellation propagation with orphan cleanup
- Signal/event adapters for GUI threads
"""
from enum import Enum
import threading
import time
from typing import Optional, Callable, Dict, Any

from core.process_runner import CancellationToken


class JobState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class JobController:
    """Thread-safe lifecycle controller and state machine for rendering jobs."""

    def __init__(
        self,
        on_state_changed: Optional[Callable[[JobState, JobState], None]] = None,
        on_progress: Optional[Callable[[float, str], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None
    ):
        self._state = JobState.IDLE
        self._lock = threading.Lock()
        self.cancellation_token = CancellationToken()
        self._resume_event = threading.Event()
        self._resume_event.set()  # Unpaused by default

        self.on_state_changed = on_state_changed
        self.on_progress = on_progress
        self.on_log = on_log

        # Metric tracking
        self.current_video_index = 0
        self.total_videos = 0
        self.current_segment_index = 0
        self.total_segments = 0
        self.progress_pct = 0.0

    @property
    def state(self) -> JobState:
        with self._lock:
            return self._state

    def _transition(self, new_state: JobState):
        old_state = self._state
        self._state = new_state
        if self.on_state_changed and old_state != new_state:
            try:
                self.on_state_changed(old_state, new_state)
            except Exception:
                pass

    def request_start(self, total_videos: int = 1) -> bool:
        """
        Attempts to start a job.
        Strictly prevents double-starts if not in IDLE, COMPLETED, FAILED, or CANCELLED state.
        """
        with self._lock:
            if self._state in (JobState.STARTING, JobState.RUNNING, JobState.PAUSE_REQUESTED, JobState.PAUSED, JobState.CANCELLING):
                return False  # Double-start prevented!

            self.cancellation_token.reset()
            self._resume_event.set()
            self.total_videos = total_videos
            self.current_video_index = 0
            self.progress_pct = 0.0
            self._transition(JobState.STARTING)

        with self._lock:
            self._transition(JobState.RUNNING)
        return True

    def request_pause(self) -> bool:
        """Requests pausing at the next safe boundary."""
        with self._lock:
            if self._state != JobState.RUNNING:
                return False
            self._resume_event.clear()
            self._transition(JobState.PAUSE_REQUESTED)
            return True

    def check_boundary_and_pause(self) -> bool:
        """
        Called by worker loops at safe boundaries (e.g. between segment renders).
        If PAUSE_REQUESTED, transitions to PAUSED and blocks until resumed or cancelled.
        Returns False if cancelled while waiting.
        """
        with self._lock:
            if self._state == JobState.PAUSE_REQUESTED:
                self._transition(JobState.PAUSED)

        # Wait on resume event if paused
        while not self._resume_event.is_set():
            if self.cancellation_token.is_cancelled():
                return False
            time.sleep(0.1)

        return not self.cancellation_token.is_cancelled()

    def resume(self) -> bool:
        """Resumes a paused job."""
        with self._lock:
            if self._state not in (JobState.PAUSED, JobState.PAUSE_REQUESTED):
                return False
            self._resume_event.set()
            self._transition(JobState.RUNNING)
            return True

    def request_cancel(self) -> bool:
        """Requests immediate cancellation and signals child processes."""
        with self._lock:
            if self._state in (JobState.IDLE, JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
                return False
            self._transition(JobState.CANCELLING)
            self.cancellation_token.cancel()
            self._resume_event.set()  # Unblock any pause wait
            return True

    def finish(self, success: bool, error_msg: Optional[str] = None):
        """Finalizes the job into COMPLETED, CANCELLED, or FAILED."""
        with self._lock:
            if self.cancellation_token.is_cancelled() or self._state == JobState.CANCELLING:
                self._transition(JobState.CANCELLED)
            elif success:
                self._transition(JobState.COMPLETED)
            else:
                self._transition(JobState.FAILED)

    def log(self, level: str, message: str):
        if self.on_log:
            try:
                self.on_log(level, message)
            except Exception:
                pass

    def update_progress(self, pct: float, message: str):
        self.progress_pct = max(0.0, min(100.0, pct))
        if self.on_progress:
            try:
                self.on_progress(self.progress_pct, message)
            except Exception:
                pass
