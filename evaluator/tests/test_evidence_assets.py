"""Evidence release-asset packing and fail-closed fetch coverage (offline)."""
from __future__ import annotations

import email.message
import hashlib
import io
import tarfile
import tempfile
import unittest
import urllib.request
from pathlib import Path

from evaluator.evidence_assets import (
    Asset,
    Config,
    EvidenceAssetError,
    HttpsOnlyRedirect,
    extract_verified,
    fetch,
    load_config,
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


class RedirectPolicyTest(unittest.TestCase):
    def _redirect(self, newurl: str) -> urllib.request.Request | None:
        request = urllib.request.Request("https://github.com/o/r/releases/download/t/a.tar.gz")
        return HttpsOnlyRedirect().redirect_request(
            request, io.BytesIO(), 302, "Found", email.message.Message(), newurl
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
