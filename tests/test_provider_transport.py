"""Offline and loopback regressions for provider bounds and accounting."""

import http.server
import io
import json
import os
import socket
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from agent_runner.http_transport import ResponseLimitError, bounded_request
from agent_runner.models import GenerationRequest, ProviderError
from agent_runner.providers import OpenRouterProvider, _post_chat_completion, _urlopen
from evaluator.adapters import NvidiaAdapter, ProviderRequestError


class Response(io.BytesIO):
    headers = {"x-request-id": "request-1"}


class ProviderBoundsTests(unittest.TestCase):
    def test_success_and_error_reads_use_limits_and_close(self):
        for status in (None, 429):
            stream = Response(b"x" * 20)
            value = (stream if status is None else urllib.error.HTTPError(
                "https://example.invalid", status, "error", Response.headers, stream))
            with self.subTest(status=status), patch(
                "agent_runner.providers._urlopen",
                **({"return_value": value} if status is None else {"side_effect": value}),
            ) as opened:
                with self.assertRaises(ResponseLimitError) as caught:
                    bounded_request(urllib.request.Request("https://example.invalid"),
                                    deadline=time.perf_counter() + 1, opener=opened,
                                    max_bytes=10, error_max_bytes=10)
                self.assertEqual(caught.exception.status, status)
                self.assertEqual(caught.exception.request_id, "request-1")
                self.assertTrue(stream.closed)
                opened.assert_called_once()

    def test_oversized_error_is_not_retried_or_exposed(self):
        error = urllib.error.HTTPError("https://example.invalid", 429, "error", {},
                                       io.BytesIO(b"secret" * 20_000))
        with patch("agent_runner.providers._urlopen", side_effect=error) as opened:
            with self.assertRaises(ProviderError) as caught:
                _post_chat_completion("provider", "https://example.invalid", {}, b"{}", timeout=1)
        self.assertFalse(caught.exception.transient)
        self.assertEqual(caught.exception.status_code, 429)
        self.assertNotIn("secret", str(caught.exception))
        opened.assert_called_once()

    def test_ambiguous_paid_reset_is_not_retried(self):
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "test"}), patch(
            "agent_runner.providers._urlopen", side_effect=ConnectionResetError("reset")
        ) as opened:
            with self.assertRaises(ProviderRequestError) as caught:
                NvidiaAdapter("test", timeout=1).generate("prompt")
        self.assertTrue(caught.exception.ambiguous_completion)
        opened.assert_called_once()

    def test_invalid_metadata_retains_valid_paid_observations(self):
        invalid_tokens = (True, -1, "12", 1.5, {}, [])
        invalid_costs = (True, -1, "0.1", float("nan"), float("inf"), 10**1000)
        for field, values in (("prompt_tokens", invalid_tokens), ("cost", invalid_costs),
                              ("id", (False, 12, {}, "", "x" * 257, "bad\nidentifier"))):
            for value in values:
                payload = {"id": "generation-1", "choices": [{"message": {"content": "{}"}}],
                           "usage": {"prompt_tokens": 12, "completion_tokens": 5, "cost": 0.1}}
                (payload if field == "id" else payload["usage"])[field] = value
                with self.subTest(field=field, value=str(value)[:30]), patch.dict(
                    os.environ, {"OPENROUTER_API_KEY": "test"}
                ), patch("agent_runner.providers._urlopen", return_value=Response(json.dumps(payload).encode())):
                    with self.assertRaises(ProviderError) as caught:
                        OpenRouterProvider("test").generate(GenerationRequest("prompt", {}, 1, "trace"))
                failure = caught.exception
                self.assertFalse(failure.transient)
                self.assertEqual(failure.output_tokens, 5)
                self.assertEqual(failure.input_tokens, None if field == "prompt_tokens" else 12)
                self.assertEqual(failure.cost_usd, None if field == "cost" else 0.1)
                self.assertEqual(failure.provider_request_id, None if field == "id" else "generation-1")
                self.assertTrue(failure.invalid_metadata)

    def test_missing_billing_is_distinct_from_observed_zero(self):
        for usage in ({}, {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0}):
            payload = {"choices": [{"message": {"content": "{}"}}], "usage": usage}
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test"}), patch(
                "agent_runner.providers._urlopen", return_value=Response(json.dumps(payload).encode())
            ):
                result = OpenRouterProvider("test").generate(GenerationRequest("prompt", {}, 1, "trace"))
            self.assertEqual(result.input_tokens, usage.get("prompt_tokens"))
            self.assertEqual(result.output_tokens, usage.get("completion_tokens"))
            self.assertEqual(result.cost_usd, usage.get("cost"))

    def test_invalid_output_retains_known_billing(self):
        for choices in ([], [{"message": {"content": "not json"}}]):
            payload = {"id": "gen", "choices": choices,
                       "usage": {"prompt_tokens": 12, "completion_tokens": 5, "cost": 0.1}}
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test"}), patch(
                "agent_runner.providers._urlopen", return_value=Response(json.dumps(payload).encode())
            ), self.assertRaises(ProviderError) as caught:
                OpenRouterProvider("test").generate(GenerationRequest("prompt", {}, 1, "trace"))
            self.assertEqual(caught.exception.cost_usd, 0.1)
            self.assertEqual(caught.exception.input_tokens, 12)
            self.assertEqual(caught.exception.provider_request_id, "gen")


class ProviderDeadlineTests(unittest.TestCase):
    def test_trickled_headers_and_success_and_error_bodies_obey_total_deadline(self):
        for mode in ("headers", "success", "error"):
            received = []

            class Handler(http.server.BaseHTTPRequestHandler):
                response_mode = mode
                calls = received

                def do_POST(self):
                    self.calls.append(self.path)
                    self.rfile.read(int(self.headers["Content-Length"]))
                    try:
                        if self.response_mode == "headers":
                            wire = b"HTTP/1.1 200 OK\r\nX-Slow: " + b"x" * 100
                        else:
                            self.send_response(429 if self.response_mode == "error" else 200)
                            self.send_header("Content-Length", "100")
                            self.send_header("x-request-id", "slow-request")
                            self.end_headers()
                            wire = b"x" * 100
                        for byte in wire:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(0.02)
                    except (OSError, ValueError):
                        pass

                def log_message(self, *_args):
                    pass

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with self.subTest(mode=mode):
                    started = time.perf_counter()
                    with self.assertRaises(ProviderError) as caught:
                        _post_chat_completion("provider", f"http://127.0.0.1:{server.server_port}",
                                              {}, b"{}", timeout=1)
                    elapsed = time.perf_counter() - started
                    self.assertLess(elapsed, 1.35)
                    self.assertTrue(caught.exception.ambiguous_completion)
                    self.assertEqual(caught.exception.attempts, 1)
                    self.assertEqual(received, ["/"])
                    if mode != "headers":
                        self.assertEqual(caught.exception.provider_request_id, "slow-request")
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_delayed_dns_cannot_transmit_after_deadline(self):
        received = threading.Event()
        resolver_finished = threading.Event()
        resolve = socket.getaddrinfo

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                received.set()
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args):
                pass

        def delayed_resolve(*args, **kwargs):
            time.sleep(0.2)
            result = resolve(*args, **kwargs)
            resolver_finished.set()
            return result

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch("socket.getaddrinfo", side_effect=delayed_resolve):
                with self.assertRaises(TimeoutError):
                    bounded_request(urllib.request.Request(
                        f"http://127.0.0.1:{server.server_port}", data=b"{}"),
                        deadline=time.perf_counter() + 0.05, opener=_urlopen)
                self.assertTrue(resolver_finished.wait(1))
                self.assertFalse(received.wait(0.1))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
