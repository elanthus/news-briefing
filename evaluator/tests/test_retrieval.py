from __future__ import annotations

import unittest

from evaluator.retrieval import (
    PairLabel,
    classify_pairs,
    cosine,
    embedding_key,
    embedding_text,
)


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


if __name__ == "__main__":
    unittest.main()
