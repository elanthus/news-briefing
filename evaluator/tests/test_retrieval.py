from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import evaluator.__main__ as evaluator_cli
from evaluator.retrieval import (
    EMBEDDINGS_ENDPOINT,
    PairLabel,
    classify_pairs,
    cosine,
    embed_texts,
    embedding_key,
    embedding_text,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _pair(pair_id: str, left_title: str, right_title: str) -> PairLabel:
    return {
        "id": pair_id,
        "left": {"title": left_title, "summary": "left summary", "url": "https://a.test"},
        "right": {"title": right_title, "summary": "right summary", "url": "https://b.test"},
        "label": "duplicate",
        "stratum": "duplicate",
        "rationale": "test pair",
    }


class CosineTests(unittest.TestCase):
    def test_known_vectors(self) -> None:
        self.assertAlmostEqual(cosine([1.0, 0.0], [0.0, 1.0]), 0.0)
        self.assertAlmostEqual(cosine([3.0, 4.0], [3.0, 4.0]), 1.0)

    def test_zero_vector_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "zero vector"):
            cosine([0.0, 0.0], [1.0, 0.0])

    def test_mismatched_dimensions_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "same dimension"):
            cosine([1.0], [1.0, 0.0])


class PairClassificationTests(unittest.TestCase):
    def test_classifies_each_pair_from_precomputed_vectors(self) -> None:
        similar = _pair("similar", "same story a", "same story b")
        distinct = _pair("distinct", "story one", "story two")
        vectors = {
            embedding_key(embedding_text(similar["left"])): [1.0, 0.0],
            embedding_key(embedding_text(similar["right"])): [0.8, 0.2],
            embedding_key(embedding_text(distinct["left"])): [1.0, 0.0],
            embedding_key(embedding_text(distinct["right"])): [0.0, 1.0],
        }

        self.assertEqual(
            classify_pairs([similar, distinct], vectors, threshold=0.90),
            {"similar": True, "distinct": False},
        )


class _Response:
    def __init__(self, payload: dict[str, object]):
        self.payload = json.dumps(payload).encode("utf-8")
        self.headers = Message()

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class EmbeddingFetchTests(unittest.TestCase):
    def test_posts_one_batch_and_restores_response_index_order(self) -> None:
        response = _Response({
            "data": [
                {"index": 1, "embedding": [0, 1]},
                {"index": 0, "embedding": [1, 0]},
            ]
        })
        with patch("evaluator.retrieval.urllib.request.urlopen", return_value=response) as opened:
            vectors = embed_texts(["first", "second"], "embedding/model", "test-key")

        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0]])
        request = opened.call_args.args[0]
        self.assertIsInstance(request, urllib.request.Request)
        self.assertEqual(request.full_url, EMBEDDINGS_ENDPOINT)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(
            json.loads(request.data or b"{}"),
            {
                "dimensions": 512,
                "input": ["first", "second"],
                "model": "embedding/model",
            },
        )

    def test_retries_rate_limit_and_honors_retry_after(self) -> None:
        headers = Message()
        headers["Retry-After"] = "1"
        rate_limit = urllib.error.HTTPError(
            EMBEDDINGS_ENDPOINT, 429, "rate limited", headers, None
        )
        response = _Response({"data": [{"index": 0, "embedding": [1, 0]}]})
        with (
            patch(
                "evaluator.retrieval.urllib.request.urlopen",
                side_effect=[rate_limit, response],
            ),
            patch("evaluator.retrieval.time.sleep") as sleep,
        ):
            self.assertEqual(
                embed_texts(["first"], "embedding/model", "test-key"),
                [[1.0, 0.0]],
            )
        sleep.assert_called_once_with(1.0)

    def test_cli_writes_a_credential_free_cache(self) -> None:
        fake_cache = {
            "schema_version": 1,
            "model": "embedding/model",
            "generated_on": "2026-08-20",
            "dimensions": 2,
            "text_representation": "UTF-8 title, newline, then summary",
            "embeddings": {"a" * 64: [1.0, 0.0]},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / ".env"
            env_file.write_text("OPENROUTER_API_KEY=test-key\n", encoding="utf-8")
            output = root / "cache.json"
            argv = [
                "python3 -m evaluator",
                "dedup-study",
                "--fetch-embeddings",
                "--env-file",
                str(env_file),
                "--embeddings",
                str(output),
                "--model",
                "embedding/model",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.dict(os.environ, {}, clear=True),
                patch(
                    "evaluator.__main__.build_embedding_cache",
                    return_value=fake_cache,
                ) as build,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(evaluator_cli.main(), 0)

            written = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(written, fake_cache)
            self.assertNotIn("test-key", output.read_text(encoding="utf-8"))
            self.assertEqual(build.call_args.args[1:3], ("embedding/model", "test-key"))

    def test_committed_cache_covers_every_fixture_text(self) -> None:
        pairs_payload = json.loads(
            (FIXTURES / "dedup-pairs.json").read_text(encoding="utf-8")
        )
        cache = json.loads(
            (FIXTURES / "dedup-embeddings.json").read_text(encoding="utf-8")
        )
        cached = cache["embeddings"]
        for pair in pairs_payload["pairs"]:
            for side in ("left", "right"):
                key = embedding_key(embedding_text(pair[side]))
                self.assertIn(key, cached, f"missing {pair['id']} {side}")


if __name__ == "__main__":
    unittest.main()
