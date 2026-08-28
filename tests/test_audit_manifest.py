from __future__ import annotations

import hashlib
import json
import unittest

import corpus_schema
from agent_runner.output import project_corpus
from audit_manifest import build_audit_manifest
from tests.test_briefing_output import fixture_contract


class AuditManifestTests(unittest.TestCase):
    def test_manifest_uses_projection_ids_and_contains_no_source_text(self) -> None:
        corpus, _config, _projected, _output = fixture_contract()
        raw = (json.dumps(corpus, ensure_ascii=False) + "\n").encode("utf-8")

        manifest = build_audit_manifest(corpus, raw)

        projection = project_corpus(corpus)
        expected_ids = [
            item["item_ref"]
            for items in projection.document["categories"].values()
            for item in items
        ]
        self.assertEqual(
            [item["item_id"] for item in manifest["items"]], expected_ids
        )
        self.assertEqual(manifest["corpus_sha256"], hashlib.sha256(raw).hexdigest())
        rendered = json.dumps(manifest, ensure_ascii=False)
        for item in corpus["categories"][next(iter(corpus["categories"]))]:
            self.assertNotIn(item["title"], rendered)
            if item.get("summary"):
                self.assertNotIn(item["summary"], rendered)

    def test_hashes_cover_exact_utf8_title_and_optional_excerpt_bytes(self) -> None:
        corpus, _config, _projected, _output = fixture_contract()
        category = next(iter(corpus["categories"]))
        first = corpus["categories"][category][0]
        first["title"] = "Exact title — café"
        first["summary"] = "Exact excerpt\nwith a second line"
        second = corpus["categories"][category][1]
        second.pop("summary", None)
        raw = json.dumps(corpus, ensure_ascii=False).encode("utf-8")

        manifest = build_audit_manifest(corpus, raw)

        self.assertEqual(
            manifest["items"][0]["title_sha256"],
            hashlib.sha256(first["title"].encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            manifest["items"][0]["excerpt_sha256"],
            hashlib.sha256(first["summary"].encode("utf-8")).hexdigest(),
        )
        self.assertIsNone(manifest["items"][1]["excerpt_sha256"])

    def test_destinations_are_canonicalized_and_keep_their_kind(self) -> None:
        corpus, _config, _projected, _output = fixture_contract()
        category = next(iter(corpus["categories"]))
        first = corpus["categories"][category][0]
        first["url"] = "HTTPS://Example.COM/story/?utm_source=test&b=2&a=1#fragment"
        first["discussion"] = "https://news.ycombinator.com/item?id=123"
        raw = json.dumps(corpus).encode("utf-8")

        manifest = build_audit_manifest(corpus, raw)

        self.assertEqual(
            manifest["items"][0]["canonical_urls"],
            [
                {"kind": "article", "url": "https://example.com/story?a=1&b=2"},
                {
                    "kind": "discussion",
                    "url": "https://news.ycombinator.com/item?id=123",
                },
            ],
        )

    def test_invalid_corpus_is_refused(self) -> None:
        corpus, _config, _projected, _output = fixture_contract()
        corpus["categories"][next(iter(corpus["categories"]))][0]["url"] = ""
        self.assertTrue(corpus_schema.validate_corpus(corpus))
        with self.assertRaisesRegex(ValueError, "corpus violates its schema"):
            build_audit_manifest(corpus, b"{}")


if __name__ == "__main__":
    unittest.main()
