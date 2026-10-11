"""Offline fake CLI processes exercise byte bounds, deadlines, and cleanup."""

import ctypes
import ctypes.wintypes
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_runner import providers
from agent_runner.models import ProviderError


def child(code):
    return [sys.executable, "-S", "-c", code]


class CaptureTests(unittest.TestCase):
    def capture(self, command, prompt="", **kwargs):
        processes = []
        collector = kwargs.pop("collector", providers._capture_cli)
        popen = subprocess.Popen

        def launch(*args, **options):
            process = popen(*args, **options)
            processes.append(process)
            return process

        with patch.object(providers.subprocess, "Popen", side_effect=launch):
            try:
                return collector(command, prompt, **kwargs)
            finally:
                for process in processes:
                    self.assertIsNotNone(process.poll())
                    self.assertTrue(process.stdout.closed)
                    self.assertTrue(process.stderr.closed)
                    self.assertFalse(any(thread.name.startswith(f"cli-capture-{process.pid}-")
                                         for thread in threading.enumerate()))

    def test_normal_capture_uses_utf8_input_and_replacement_decoding(self):
        prompt = "Unicode corpus text: café and hair space"
        completed = self.capture(child(
            "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()+b'\\xff'); "
            "sys.stderr.buffer.write(b'\\xff')"), prompt, timeout=3)
        self.assertEqual(completed.stdout, prompt + "�")
        self.assertEqual(completed.stderr, "�")
        self.assertEqual(completed.returncode, 0)

    def test_each_stream_is_bounded_during_collection(self):
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream):
                with self.assertRaises(providers._CLIOutputLimitError):
                    self.capture(child(
                        f"import sys,time; sys.{stream}.buffer.write(b'sensitive '*100000); "
                        f"sys.{stream}.flush(); time.sleep(30)"), timeout=3,
                        stdout_limit=128, stderr_limit=64)

    def test_exact_limits_are_allowed_and_next_byte_is_rejected(self):
        completed = self.capture(child(
            "import sys; sys.stdout.buffer.write(b'x'*128); sys.stderr.buffer.write(b'y'*64)"),
            timeout=3, stdout_limit=128, stderr_limit=64)
        self.assertEqual((len(completed.stdout), len(completed.stderr)), (128, 64))
        with self.assertRaises(providers._CLIOutputLimitError):
            self.capture(child("import sys; sys.stdout.buffer.write(b'x'*129)"),
                         timeout=3, stdout_limit=128, stderr_limit=64)

    def test_reader_failure_between_flag_check_and_process_poll_is_rejected(self):
        # Release collectors from poll(), after the supervisor checks its flags.
        # Both threads then finish before poll returns an already-exited child.
        for broken_pipe in (False, True):
            with self.subTest(broken_pipe=broken_pipe):
                release = threading.Event()
                popen = subprocess.Popen
                ready = providers._pipe_ready_bytes

                def launch(*args, popen=popen, release=release, **kwargs):
                    process = popen(*args, **kwargs)
                    process.wait(timeout=3)  # Tiny output fits without a reader.
                    poll = process.poll

                    def poll_after_readers():
                        release.set()
                        for reader in threading.enumerate():
                            if reader.name.startswith(f"cli-capture-{process.pid}-"):
                                reader.join(timeout=3)
                                self.assertFalse(reader.is_alive())
                        return poll()

                    process.poll = poll_after_readers
                    return process

                def gated_ready(stream, capacity, release=release, broken_pipe=broken_pipe, ready=ready):
                    if not release.wait(timeout=3):
                        raise AssertionError("supervisor never polled the child")
                    if broken_pipe:
                        raise OSError("synthetic pipe failure")
                    return ready(stream, capacity)

                with (patch.object(providers.subprocess, "Popen", side_effect=launch),
                      patch.object(providers, "_pipe_ready_bytes", side_effect=gated_ready)):
                    expected = OSError if broken_pipe else providers._CLIOutputLimitError
                    with self.assertRaises(expected):
                        providers._capture_cli(child("import os; os.write(1, b'x'*9)"), "",
                                               timeout=3, stdout_limit=8)

    def test_timeout_cleans_up_process_and_readers_without_pipe_output(self):
        started = time.perf_counter()
        with self.assertRaises(subprocess.TimeoutExpired):
            self.capture(child("import time; time.sleep(30)"), timeout=0.2)
        self.assertLess(time.perf_counter() - started, 3)

    @unittest.skipUnless(os.name == "posix", "POSIX session cleanup")
    def test_timeout_cleans_up_descendant_holding_pipes_after_leader_exits(self):
        started = time.perf_counter()
        command = child(
            "import subprocess,sys; subprocess.Popen([sys.executable,'-S','-c',"
            "'import time; time.sleep(30)']); print('leader done',flush=True)")
        with self.assertRaises(subprocess.TimeoutExpired):
            self.capture(command, timeout=0.3)
        self.assertLess(time.perf_counter() - started, 3)

    @unittest.skipUnless(os.name == "posix", "POSIX session cleanup")
    def test_overflow_kills_descendant_after_leader_and_readers_exit(self):
        class ReadersFinishedOverflow(providers._CLIOutputLimitError):
            def __init__(self, message):
                # Force the race: both readers finish before failure cleanup.
                time.sleep(0.05)
                super().__init__(message)

        descendant = (
            "import sys,time; sys.stderr.close(); time.sleep(0.2); "
            "sys.stdout.buffer.write(b'x'*129); sys.stdout.flush(); time.sleep(30)"
        )
        with tempfile.TemporaryDirectory() as directory:
            leader_path = Path(directory) / "leader.pid"
            command = child(
                f"import os,subprocess,sys; open({str(leader_path)!r},'w').write(str(os.getpid())); "
                f"subprocess.Popen([sys.executable,'-S','-c',{descendant!r}])"
            )
            try:
                with (patch.object(providers, "_CLIOutputLimitError", ReadersFinishedOverflow),
                      patch.object(providers, "_stop_cli", wraps=providers._stop_cli) as stop):
                    with self.assertRaises(ReadersFinishedOverflow):
                        self.capture(command, timeout=3, stdout_limit=128, stderr_limit=64)
                stop.assert_called_once()
                self.assertIsNotNone(stop.call_args.args[0].returncode)
            finally:
                # Also clean up when testing a regression against the old code.
                if leader_path.exists() and not stop.called:
                    try:
                        os.killpg(int(leader_path.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_overflow_returns_sanitized_typed_failure_without_retry(self):
        capture = providers._capture_cli

        def bounded(command, prompt, **kwargs):
            return self.capture(command, prompt, **kwargs, stdout_limit=128, stderr_limit=64,
                                collector=capture)

        command = child("import sys; sys.stderr.buffer.write(b'sensitive secret '*100000)")
        with patch.object(providers, "_capture_cli", side_effect=bounded) as run:
            with self.assertRaises(ProviderError) as raised:
                providers._run_cli(command, "private prompt", timeout=3)
        error = raised.exception
        self.assertFalse(error.transient)
        self.assertTrue(error.ambiguous_completion)
        self.assertTrue(error.output_truncated)
        self.assertEqual(error.failure.code, "output_truncated")
        self.assertNotIn("sensitive", str(error.record()))
        self.assertNotIn("private prompt", str(error.record()))
        self.assertEqual(run.call_count, 1)

    @unittest.skipUnless(os.name == "posix", "POSIX cleanup error")
    def test_cleanup_error_preserves_typed_overflow_and_reports_sanitized_failure(self):
        capture = providers._capture_cli

        def bounded(command, prompt, **kwargs):
            return self.capture(command, prompt, **kwargs, stdout_limit=128, stderr_limit=64,
                                collector=capture)

        with (patch.object(providers.os, "killpg", side_effect=PermissionError("private OS detail")),
              patch.object(providers, "_capture_cli", side_effect=bounded) as run):
            with self.assertRaises(ProviderError) as raised:
                providers._run_cli(child("import sys; sys.stdout.buffer.write(b'x'*129)"), "", timeout=3)
        error = raised.exception
        self.assertTrue(error.output_truncated)
        self.assertTrue(error.ambiguous_completion)
        self.assertEqual(error.failure.code, "output_truncated")
        self.assertIn("CLI descendant cleanup failed", str(error.record()))
        self.assertNotIn("private OS detail", str(error.record()))
        self.assertEqual(run.call_count, 1)

    @unittest.skipUnless(os.name == "posix", "POSIX cleanup error")
    def test_group_cleanup_error_still_kills_and_reaps_direct_process(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(providers.os, "killpg", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                providers._stop_cli(process)
        process.kill.assert_called_once()
        process.wait.assert_called_once_with(timeout=providers._CLI_CLEANUP_SECONDS)

    def test_expired_deadline_does_not_start_a_cli(self):
        with patch.object(providers.subprocess, "Popen") as launch:
            with self.assertRaises(subprocess.TimeoutExpired):
                providers._capture_cli(child("pass"), "prompt", timeout=0)
        launch.assert_not_called()

    def test_reads_request_only_remaining_capacity_plus_one_byte(self):
        reads = []
        popen = subprocess.Popen

        class Pipe:
            def __init__(self, stream):
                self.stream = stream

            def fileno(self):
                return self.stream.fileno()

            def read(self, capacity):
                chunk = self.stream.read(capacity)
                reads.append((capacity, len(chunk)))
                return chunk

            def close(self):
                self.stream.close()

        def launch(*args, **kwargs):
            process = popen(*args, **kwargs)
            process.stdout = Pipe(process.stdout)
            return process

        with patch.object(providers.subprocess, "Popen", side_effect=launch):
            with self.assertRaises(providers._CLIOutputLimitError):
                providers._capture_cli(child("import sys; sys.stdout.buffer.write(b'x'*1000000)"),
                                       "", timeout=3, stdout_limit=128, stderr_limit=64)
        self.assertTrue(reads)
        self.assertLessEqual(sum(size for _, size in reads), 129)
        self.assertTrue(all(capacity <= 129 for capacity, _ in reads))

    def test_windows_pipe_readiness_is_bounded_and_fails_closed(self):
        stream = Mock()
        stream.fileno.return_value = 123
        native = Mock()
        native.get_osfhandle.return_value = 456

        def ready(handle, buffer, size, read, available, left):
            available._obj.value = 1000
            return True

        peek = Mock(side_effect=ready)
        kernel = Mock(PeekNamedPipe=peek)
        with (patch.object(providers.os, "name", "nt"),
              patch.dict(sys.modules, {"msvcrt": native}),
              patch.object(ctypes, "WinDLL", return_value=kernel, create=True),
              patch.object(ctypes, "get_last_error", return_value=109, create=True)):
            self.assertEqual(providers._pipe_ready_bytes(stream, 129), 129)
            peek.side_effect = None
            peek.return_value = False
            self.assertEqual(providers._pipe_ready_bytes(stream, 129), 0)
            with patch.object(ctypes, "get_last_error", return_value=5):
                with self.assertRaisesRegex(OSError, "readiness check failed"):
                    providers._pipe_ready_bytes(stream, 129)

    def test_timeout_is_ambiguous_and_never_retried(self):
        with patch.object(providers, "_capture_cli", side_effect=subprocess.TimeoutExpired([], 1)) as run:
            with self.assertRaises(ProviderError) as raised:
                providers._run_cli(child("pass"), "prompt", timeout=1)
        self.assertTrue(raised.exception.ambiguous_completion)
        self.assertEqual(run.call_count, 1)

    def test_failure_diagnostics_do_not_expose_provider_stderr(self):
        with self.assertRaises(ProviderError) as raised:
            providers._run_cli(child("import sys; sys.stderr.write('private key'); sys.exit(1)"),
                               "prompt", timeout=3)
        self.assertNotIn("private key", str(raised.exception.record()))


if __name__ == "__main__":
    unittest.main()
