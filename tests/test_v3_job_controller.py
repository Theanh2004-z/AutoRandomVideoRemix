"""
Unit tests for C06 — ProcessRunner, Cancellation, and JobController State Machine.
"""
import unittest
import sys
import os
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.process_runner import ProcessRunner, CancellationToken, ProcessResult
from core.job_controller import JobController, JobState


class TestV3JobController(unittest.TestCase):
    # -------------------------------------------------------------
    # ProcessRunner Tests
    # -------------------------------------------------------------
    def test_process_runner_success(self):
        res = ProcessRunner.run(["ffmpeg", "-version"], timeout_sec=5.0)
        self.assertEqual(res.returncode, 0)
        self.assertFalse(res.is_cancelled)
        self.assertGreater(res.duration_sec, 0.0)

    def test_process_runner_cancellation(self):
        token = CancellationToken()
        # Launch a heavy ffmpeg command generating test video for 300s
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=300:size=1280x720:rate=60",
            "-f", "null", "-"
        ]

        def cancel_after_delay():
            time.sleep(0.1)
            token.cancel()

        threading.Thread(target=cancel_after_delay, daemon=True).start()
        res = ProcessRunner.run(cmd, cancellation_token=token, timeout_sec=10.0)

        self.assertTrue(res.is_cancelled)
        self.assertLess(res.duration_sec, 4.0)

    # -------------------------------------------------------------
    # JobController Tests
    # -------------------------------------------------------------
    def test_state_machine_happy_path(self):
        state_history = []
        controller = JobController(on_state_changed=lambda old, new: state_history.append((old, new)))

        self.assertEqual(controller.state, JobState.IDLE)
        ok = controller.request_start(total_videos=2)
        self.assertTrue(ok)
        self.assertEqual(controller.state, JobState.RUNNING)

        controller.finish(success=True)
        self.assertEqual(controller.state, JobState.COMPLETED)

        # Check recorded transitions
        self.assertIn((JobState.IDLE, JobState.STARTING), state_history)
        self.assertIn((JobState.STARTING, JobState.RUNNING), state_history)
        self.assertIn((JobState.RUNNING, JobState.COMPLETED), state_history)

    def test_double_start_prevention(self):
        controller = JobController()
        self.assertTrue(controller.request_start())
        # Second start attempt while already running must be rejected!
        self.assertFalse(controller.request_start())
        controller.finish(success=True)

    def test_pause_resume_cycle(self):
        controller = JobController()
        controller.request_start()
        self.assertEqual(controller.state, JobState.RUNNING)

        # Request pause
        self.assertTrue(controller.request_pause())
        self.assertEqual(controller.state, JobState.PAUSE_REQUESTED)

        # Worker calls check_boundary_and_pause() in background thread
        worker_resumed = threading.Event()

        def worker_loop():
            # At segment boundary
            controller.check_boundary_and_pause()
            worker_resumed.set()

        t = threading.Thread(target=worker_loop, daemon=True)
        t.start()

        # Give small delay to transition to PAUSED
        time.sleep(0.15)
        self.assertEqual(controller.state, JobState.PAUSED)
        self.assertFalse(worker_resumed.is_set())

        # Resume job
        self.assertTrue(controller.resume())
        self.assertEqual(controller.state, JobState.RUNNING)

        # Worker should now be unblocked
        t.join(timeout=1.0)
        self.assertTrue(worker_resumed.is_set())

        controller.finish(success=True)

    def test_cancellation_flow(self):
        controller = JobController()
        controller.request_start()

        self.assertTrue(controller.request_cancel())
        self.assertTrue(controller.cancellation_token.is_cancelled())
        self.assertEqual(controller.state, JobState.CANCELLING)

        controller.finish(success=False)
        self.assertEqual(controller.state, JobState.CANCELLED)


if __name__ == "__main__":
    unittest.main()
