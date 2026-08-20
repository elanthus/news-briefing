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
    load_embedding_cache,
    load_pairs,
    markdown_study,
    run_study,
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

    def test_study_compares_thresholds_with_the_production_heuristic(self) -> None:
        prefix = "a" * 60
        exact = _pair("exact", "same title", "same title")
        paraphrase = _pair("paraphrase", "Mayor approves transit plan", "City leader backs rail proposal")
        collision = _pair("collision", prefix + "alpha", prefix + "beta")
        collision["label"] = "distinct"
        collision["stratum"] = "hard_negative"
        separate = _pair("separate", "Volcano update", "Quarterly software earnings")
        separate["label"] = "distinct"
        separate["stratum"] = "clear_negative"
        pairs = [exact, paraphrase, collision, separate]
        vectors: dict[str, list[float]] = {}
        pair_vectors = {
            "exact": ([1.0, 0.0], [1.0, 0.0]),
            "paraphrase": ([1.0, 0.0], [0.8, 0.6]),
            "collision": ([1.0, 0.0], [0.0, 1.0]),
            "separate": ([1.0, 0.0], [0.7, 0.7]),
        }
        for pair in pairs:
            left, right = pair_vectors[pair["id"]]
            vectors[embedding_key(embedding_text(pair["left"]))] = left
            vectors[embedding_key(embedding_text(pair["right"]))] = right

        study = run_study(pairs, vectors, thresholds=[0.80, 0.95])

        self.assertEqual(study["pair_count"], 4)
        self.assertEqual(study["chosen_threshold"], 0.80)
        self.assertEqual(study["embedding_results"][0]["metrics"]["f1"], 1.0)
        self.assertEqual(study["embedding_results"][1]["metrics"]["recall"], 0.5)
        self.assertEqual(
            study["heuristic_metrics"],
            {
                "tp": 1,
                "fp": 1,
                "tn": 1,
                "fn": 1,
                "precision": 0.5,
                "recall": 0.5,
                "f1": 0.5,
            },
        )

    def test_pair_loader_rejects_label_stratum_mismatch(self) -> None:
        payload = {
            "pairs": [
                {
                    **_pair("mismatch", "left", "right"),
                    "label": "distinct",
                    "stratum": "duplicate",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pairs.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inconsistent"):
                load_pairs(path)


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
        response = _Response(
            {
                "data": [
                    {"index": 1, "embedding": [0, 1]},
                    {"index": 0, "embedding": [1, 0]},
                ]
            }
        )
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
        rate_limit = urllib.error.HTTPError(EMBEDDINGS_ENDPOINT, 429, "rate limited", headers, None)
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
        pairs_payload = json.loads((FIXTURES / "dedup-pairs.json").read_text(encoding="utf-8"))
        cache = json.loads((FIXTURES / "dedup-embeddings.json").read_text(encoding="utf-8"))
        cached = cache["embeddings"]
        self.assertEqual(cache["schema_version"], 1)
        self.assertEqual(cache["model"], "openai/text-embedding-3-small")
        self.assertEqual(cache["dimensions"], 512)
        self.assertEqual(len(cached), 82)
        self.assertTrue(all(len(key) == 64 and int(key, 16) >= 0 for key in cached))
        for pair in pairs_payload["pairs"]:
            for side in ("left", "right"):
                key = embedding_key(embedding_text(pair[side]))
                self.assertIn(key, cached, f"missing {pair['id']} {side}")
        serialized = json.dumps(cache).lower()
        self.assertNotIn("openrouter_api_key", serialized)
        self.assertNotIn("bearer ", serialized)

    def test_cache_loader_rejects_unknown_schema_and_non_sha_key(self) -> None:
        payload = {
            "schema_version": 2,
            "model": "embedding/model",
            "generated_on": "2026-08-20",
            "dimensions": 2,
            "embeddings": {"g" * 64: [1.0, 0.0]},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema_version"):
                load_embedding_cache(path)

            payload["schema_version"] = 1
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid vector"):
                load_embedding_cache(path)


class CommittedStudyTests(unittest.TestCase):
    def test_committed_fixture_has_required_distribution_and_provenance(self) -> None:
        payload = json.loads((FIXTURES / "dedup-pairs.json").read_text(encoding="utf-8"))
        self.assertEqual(
            payload["label_provenance"],
            "machine-proposed-2026-08-20, owner review pending",
        )
        counts: dict[str, int] = {}
        for pair in payload["pairs"]:
            counts[pair["stratum"]] = counts.get(pair["stratum"], 0) + 1
        self.assertEqual(
            counts,
            {"duplicate": 20, "clear_negative": 20, "hard_negative": 20},
        )

    def test_offline_study_and_report_are_reproducible(self) -> None:
        pairs_path = FIXTURES / "dedup-pairs.json"
        pairs = load_pairs(pairs_path)
        cache = load_embedding_cache(FIXTURES / "dedup-embeddings.json")
        study = run_study(pairs, cache["embeddings"])

        self.assertEqual(study["pair_count"], 60)
        self.assertEqual(study["chosen_threshold"], 0.70)
        self.assertAlmostEqual(study["chosen_embedding_metrics"]["f1"], 0.95)
        self.assertEqual(study["heuristic_metrics"]["f1"], 0.0)

        payload = json.loads(pairs_path.read_text(encoding="utf-8"))
        rendered = markdown_study(
            study,
            pairs,
            cache,
            payload["label_provenance"],
        )
        committed = (FIXTURES.parent / "results" / "dedup-study.md").read_text(encoding="utf-8")
        self.assertEqual(rendered, committed)


if __name__ == "__main__":
    unittest.main()
