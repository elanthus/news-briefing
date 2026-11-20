"""Evidence release-asset packing and fail-closed fetch coverage (offline)."""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import tarfile
import tempfile
import unittest
import urllib.request
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any

from evaluator.evidence_assets import (
    Asset,
    Config,
    EvidenceAssetError,
    HttpsOnlyRedirect,
    extract_verified,
    fetch,
    load_config,
    main,
    pack,
    pack_bytes,
)

BUNDLE = "demo-evidence"
LARGE = {"ledger.json": b'{"rows": []}\n', "manifest.json": b'{"results": []}\n'}
SMALL = {"metadata.json": b"{}\n", "report.md": b"# Report\n"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_bundle(root: Path, large: bool = True) -> None:
    bundle = root / BUNDLE
    bundle.mkdir(parents=True)
    files = {**SMALL, **(LARGE if large else {})}
    for name, data in files.items():
        (bundle / name).write_bytes(data)
    sums = {**SMALL, **LARGE}
    (bundle / "SHA256SUMS").write_text(
        "".join(f"{_sha(data)}  {name}\n" for name, data in sorted(sums.items())), encoding="utf-8"
    )


def _sums() -> dict[str, str]:
    return {name: _sha(data) for name, data in {**SMALL, **LARGE}.items()}


def _asset(archive: bytes) -> Asset:
    return Asset(BUNDLE, f"{BUNDLE}.tar.gz", _sha(archive), tuple(sorted(LARGE)))


def _raw_archive(members: list[tarfile.TarInfo], payloads: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for info in members:
            data = payloads.get(info.name)
            archive.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


class PackTest(unittest.TestCase):
    def test_pack_is_byte_reproducible_regardless_of_input_order(self) -> None:
        first = pack_bytes(BUNDLE, list(LARGE.items()))
        second = pack_bytes(BUNDLE, list(reversed(LARGE.items())))
        self.assertEqual(first, second)
        with tarfile.open(fileobj=io.BytesIO(first), mode="r:gz") as archive:
            members = archive.getmembers()
        self.assertEqual([m.name for m in members], [f"{BUNDLE}/ledger.json", f"{BUNDLE}/manifest.json"])
        self.assertTrue(all(m.mtime == 0 and m.uid == 0 and m.gid == 0 for m in members))

    def test_pack_then_fetch_assembles_complete_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_bundle(root / "full")
            _write_bundle(root / "tree", large=False)
            archive = pack_bytes(BUNDLE, list(LARGE.items()))
            config = Config("o/r", "t", root / "full", root / "out", (_asset(archive),))
            pack(config, root / "assets")
            self.assertEqual((root / "assets" / f"{BUNDLE}.tar.gz").read_bytes(), archive)
            fetched = fetch(Config("o/r", "t", root / "tree", root / "out", (_asset(archive),)), root / "assets")
            self.assertEqual(fetched, [root / "out" / BUNDLE])
            names = sorted(path.name for path in (root / "out" / BUNDLE).iterdir())
            self.assertEqual(names, ["SHA256SUMS", "ledger.json", "manifest.json", "metadata.json", "report.md"])

    def test_repository_config_is_valid(self) -> None:
        config = load_config()
        self.assertTrue(config.assets)
        for asset in config.assets:
            self.assertTrue((config.source_root / asset.bundle / "SHA256SUMS").is_file())


class ConfigShapeTest(unittest.TestCase):
    """A malformed config is a typed error, not a bare KeyError or TypeError."""

    ASSET: dict[str, Any] = {
        "bundle": BUNDLE,
        "name": f"{BUNDLE}.tar.gz",
        "sha256": "a" * 64,
        "files": ["ledger.json"],
    }
    VALID: dict[str, Any] = {
        "repository": "o/r",
        "tag": "t",
        "source_root": "tree",
        "output_root": "out",
        "assets": [ASSET],
    }

    def _load(self, payload: object) -> Config:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence-assets.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return load_config(path, Path(directory))

    def test_valid_shape_loads(self) -> None:
        self.assertEqual([asset.bundle for asset in self._load(self.VALID).assets], [BUNDLE])

    def test_missing_keys_name_the_key(self) -> None:
        for key in self.VALID:
            payload = {name: value for name, value in self.VALID.items() if name != key}
            with self.subTest(key=key), self.assertRaisesRegex(
                EvidenceAssetError, f"invalid evidence asset config .*: missing key '{key}'"
            ):
                self._load(payload)
        asset = {name: value for name, value in self.ASSET.items() if name != "sha256"}
        with self.assertRaisesRegex(EvidenceAssetError, "missing key 'sha256'"):
            self._load({**self.VALID, "assets": [asset]})

    def test_wrong_container_types_are_rejected(self) -> None:
        payloads: tuple[object, ...] = (
            [],
            {**self.VALID, "assets": "parity"},
            {**self.VALID, "assets": [["bundle"]]},
            {**self.VALID, "assets": [{**self.ASSET, "files": 3}]},
            {**self.VALID, "source_root": 3},
        )
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaisesRegex(
                EvidenceAssetError, "invalid evidence asset config"
            ):
                self._load(payload)

    def test_wrong_value_types_and_escaping_roots_are_rejected(self) -> None:
        payloads: tuple[tuple[object, str], ...] = (
            ({**self.VALID, "assets": {"a": self.ASSET}}, "assets must be a list"),
            ({**self.VALID, "assets": [{**self.ASSET, "files": "ledger.json"}]}, "asset files must be a list"),
            ({**self.VALID, "assets": [{**self.ASSET, "files": {"ledger.json": 1}}]}, "asset files must be a list"),
            ({**self.VALID, "repository": 3}, "repository must be owner/name"),
            ({**self.VALID, "repository": "o/r/../x"}, "repository must be owner/name"),
            ({**self.VALID, "source_root": "/etc"}, "source_root must stay inside the repository"),
            ({**self.VALID, "output_root": "../out"}, "output_root must stay inside the repository"),
            ({**self.VALID, "output_root": "out\x00"}, "output_root must be a relative directory"),
            ({**self.VALID, "source_root": ""}, "source_root must be a relative directory"),
        )
        for payload, message in payloads:
            with self.subTest(payload=payload), self.assertRaisesRegex(
                EvidenceAssetError, f"invalid evidence asset config .*: {message}"
            ):
                self._load(payload)

    def test_cli_reports_a_malformed_config_as_one_line(self) -> None:
        for command in (["fetch"], ["pack", "--output-dir", "assets"]):
            with self.subTest(command=command[0]), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "evidence-assets.json"
                path.write_text(json.dumps({**self.VALID, "repository": 3}), encoding="utf-8")
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    status = main(["--config", str(path), *command])
                self.assertEqual(status, 1)
                lines = stderr.getvalue().strip().splitlines()
                self.assertEqual(len(lines), 1)
                self.assertRegex(lines[0], r"^error: invalid evidence asset config .*: repository must be owner/name")


class RedirectPolicyTest(unittest.TestCase):
    def _redirect(self, newurl: str) -> urllib.request.Request | None:
        request = urllib.request.Request("https://github.com/o/r/releases/download/t/a.tar.gz")
        return HttpsOnlyRedirect().redirect_request(
            request, io.BytesIO(), 302, "Found", http.client.HTTPMessage(), newurl
        )

    def test_refuses_redirect_to_http(self) -> None:
        with self.assertRaisesRegex(EvidenceAssetError, "non-HTTPS redirect"):
            self._redirect("http://objects.example/a.tar.gz")

    def test_allows_redirect_to_https(self) -> None:
        followed = self._redirect("https://objects.example/a.tar.gz")
        self.assertIsNotNone(followed)
        assert followed is not None
        self.assertEqual(followed.full_url, "https://objects.example/a.tar.gz")


class ExtractValidationTest(unittest.TestCase):
    def test_rejects_archive_hash_mismatch(self) -> None:
        archive = pack_bytes(BUNDLE, list(LARGE.items()))
        asset = Asset(BUNDLE, "x.tar.gz", "0" * 64, tuple(sorted(LARGE)))
        with self.assertRaisesRegex(EvidenceAssetError, "archive sha256"):
            extract_verified(asset, archive, _sums())

    def test_rejects_member_that_does_not_match_sha256sums(self) -> None:
        archive = pack_bytes(BUNDLE, [("ledger.json", b"tampered"), ("manifest.json", LARGE["manifest.json"])])
        with self.assertRaisesRegex(EvidenceAssetError, "does not match SHA256SUMS"):
            extract_verified(_asset(archive), archive, _sums())

    def test_rejects_path_traversal_member(self) -> None:
        info = tarfile.TarInfo("../ledger.json")
        info.size = len(LARGE["ledger.json"])
        archive = _raw_archive([info], {"../ledger.json": LARGE["ledger.json"]})
        with self.assertRaisesRegex(EvidenceAssetError, "unexpected member"):
            extract_verified(_asset(archive), archive, _sums())

    def test_rejects_symlink_member(self) -> None:
        info = tarfile.TarInfo(f"{BUNDLE}/ledger.json")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        archive = _raw_archive([info], {})
        with self.assertRaisesRegex(EvidenceAssetError, "non-regular member"):
            extract_verified(_asset(archive), archive, _sums())

    def test_rejects_missing_and_duplicate_members(self) -> None:
        archive = pack_bytes(BUNDLE, [("ledger.json", LARGE["ledger.json"])])
        with self.assertRaisesRegex(EvidenceAssetError, "missing members"):
            extract_verified(_asset(archive), archive, _sums())
        info = tarfile.TarInfo(f"{BUNDLE}/ledger.json")
        info.size = len(LARGE["ledger.json"])
        duplicate = _raw_archive([info, info], {info.name: LARGE["ledger.json"]})
        with self.assertRaisesRegex(EvidenceAssetError, "unexpected member"):
            extract_verified(_asset(duplicate), duplicate, _sums())

    def test_fetch_writes_nothing_when_any_member_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_bundle(root / "tree", large=False)
            (root / "assets").mkdir()
            archive = pack_bytes(BUNDLE, [("ledger.json", b"tampered"), ("manifest.json", LARGE["manifest.json"])])
            (root / "assets" / f"{BUNDLE}.tar.gz").write_bytes(archive)
            config = Config("o/r", "t", root / "tree", root / "out", (_asset(archive),))
            with self.assertRaises(EvidenceAssetError):
                fetch(config, root / "assets")
            self.assertEqual(list((root / "out").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
