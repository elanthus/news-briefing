"""Pack and fetch the large public evidence files published as GitHub release assets.

Each bundle directory under `docs/results/` keeps `SHA256SUMS`, `metadata.json`,
and `report.md` in the tree. The remaining bundle files live in one `.tar.gz`
release asset per bundle. `fetch` downloads each asset, checks the archive hash
recorded in `evaluator/evidence-assets.json`, checks every member against the
in-tree `SHA256SUMS`, and only then writes complete bundles under the configured
output root, where `python3 -m evaluator verify-public-run` can read them.
"""

from __future__ import annotations

import argparse
import email.message
import gzip
import hashlib
import io
import json
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO, Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "evaluator" / "evidence-assets.json"
MAX_ASSET_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
TIMEOUT_SECONDS = 60
IN_TREE_FILES = ("SHA256SUMS", "metadata.json", "report.md")


class EvidenceAssetError(ValueError):
    """An evidence asset or its configuration failed validation."""


@dataclass(frozen=True)
class Asset:
    bundle: str
    name: str
    sha256: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    repository: str
    tag: str
    source_root: Path
    output_root: Path
    assets: tuple[Asset, ...]

    def url(self, asset: Asset) -> str:
        return f"https://github.com/{self.repository}/releases/download/{self.tag}/{asset.name}"


def _plain_name(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise EvidenceAssetError(f"{label} must be a plain file name: {value!r}")
    return value


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and not set(value) - set("0123456789abcdef")


def load_config(path: Path = CONFIG_PATH, root: Path = ROOT) -> Config:
    raw = json.loads(path.read_text(encoding="utf-8"))
    assets = []
    for item in raw["assets"]:
        files = tuple(_plain_name(name, "asset file") for name in item["files"])
        if len(set(files)) != len(files) or set(files) & set(IN_TREE_FILES):
            raise EvidenceAssetError(f"asset {item['name']} has duplicate or in-tree files")
        if not _is_sha256(item["sha256"]):
            raise EvidenceAssetError(f"asset {item['name']} has an invalid sha256")
        assets.append(
            Asset(
                bundle=_plain_name(item["bundle"], "bundle"),
                name=_plain_name(item["name"], "asset name"),
                sha256=item["sha256"],
                files=files,
            )
        )
    return Config(
        repository=raw["repository"],
        tag=_plain_name(raw["tag"], "tag"),
        source_root=root / raw["source_root"],
        output_root=root / raw["output_root"],
        assets=tuple(assets),
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_sums(bundle_dir: Path) -> dict[str, str]:
    """Return `{file name: sha256}` from a bundle's in-tree `SHA256SUMS`."""
    sums: dict[str, str] = {}
    for line in (bundle_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        digest, separator, name = line.partition("  ")
        if not separator or not _is_sha256(digest):
            raise EvidenceAssetError(f"invalid SHA256SUMS line in {bundle_dir}: {line!r}")
        sums[_plain_name(name, "SHA256SUMS entry")] = digest
    return sums


def pack_bytes(bundle: str, files: Sequence[tuple[str, bytes]]) -> bytes:
    """Build a reproducible gzip tar: sorted members, fixed mtime, owner, and mode."""
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, data in sorted(files):
            info = tarfile.TarInfo(f"{_plain_name(bundle, 'bundle')}/{_plain_name(name, 'member')}")
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(data))
    gz_buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=gz_buffer, mtime=0, compresslevel=9) as stream:
        stream.write(tar_buffer.getvalue())
    return gz_buffer.getvalue()


def pack(config: Config, output_dir: Path) -> dict[str, dict[str, Any]]:
    """Write one archive per bundle from `config.source_root` and return sizes and hashes."""
    output_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for asset in config.assets:
        bundle_dir = config.source_root / asset.bundle
        data = pack_bytes(asset.bundle, [(name, (bundle_dir / name).read_bytes()) for name in asset.files])
        (output_dir / asset.name).write_bytes(data)
        results[asset.name] = {"bytes": len(data), "sha256": _sha256(data)}
    return results


def extract_verified(asset: Asset, archive_bytes: bytes, sums: dict[str, str]) -> dict[str, bytes]:
    """Return member contents only if the archive and every member match the recorded hashes."""
    actual = _sha256(archive_bytes)
    if actual != asset.sha256:
        raise EvidenceAssetError(f"{asset.name}: archive sha256 {actual} does not match {asset.sha256}")
    expected = {f"{asset.bundle}/{name}": name for name in asset.files}
    contents: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            for member in archive:
                if not member.isreg():
                    raise EvidenceAssetError(f"{asset.name}: non-regular member {member.name!r}")
                name = expected.get(member.name)
                if name is None or name in contents:
                    raise EvidenceAssetError(f"{asset.name}: unexpected member {member.name!r}")
                if member.size > MAX_MEMBER_BYTES:
                    raise EvidenceAssetError(f"{asset.name}: member {member.name!r} is too large")
                stream = archive.extractfile(member)
                if stream is None:
                    raise EvidenceAssetError(f"{asset.name}: unreadable member {member.name!r}")
                data = stream.read()
                if sums.get(name) != _sha256(data):
                    raise EvidenceAssetError(f"{asset.name}: {name} does not match SHA256SUMS")
                contents[name] = data
    except (tarfile.TarError, OSError, EOFError) as error:
        raise EvidenceAssetError(f"{asset.name}: invalid archive: {error}") from error
    missing = sorted(set(asset.files) - set(contents))
    if missing:
        raise EvidenceAssetError(f"{asset.name}: missing members {missing}")
    return contents


class HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse any redirect hop whose target is not HTTPS, before it is requested."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: email.message.Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not newurl.startswith("https://"):
            raise EvidenceAssetError(f"refusing non-HTTPS redirect to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str) -> bytes:
    """Download `url` over HTTPS; every redirect hop and the final URL must be HTTPS."""
    if not url.startswith("https://"):
        raise EvidenceAssetError(f"refusing non-HTTPS asset URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "news-briefing-evidence-assets"})
    opener = urllib.request.build_opener(HttpsOnlyRedirect)
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        if not response.geturl().startswith("https://"):
            raise EvidenceAssetError(f"refusing non-HTTPS redirect for {url}")
        data: bytes = response.read(MAX_ASSET_BYTES + 1)
    if len(data) > MAX_ASSET_BYTES:
        raise EvidenceAssetError(f"asset exceeds {MAX_ASSET_BYTES} bytes: {url}")
    return data


def fetch(config: Config, asset_dir: Path | None = None) -> list[Path]:
    """Assemble every verified bundle under `config.output_root`.

    With `asset_dir`, archives are read from local files instead of the release.
    Nothing is written outside a temporary directory until every asset verifies.
    """
    config.output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=config.output_root, prefix=".fetch-") as staging_name:
        staging = Path(staging_name)
        for asset in config.assets:
            source_dir = config.source_root / asset.bundle
            sums = read_sums(source_dir)
            if set(sums) != (set(asset.files) | set(IN_TREE_FILES)) - {"SHA256SUMS"}:
                raise EvidenceAssetError(f"{asset.bundle}: SHA256SUMS does not match the configured file list")
            archive_bytes = (
                (asset_dir / asset.name).read_bytes() if asset_dir is not None else download(config.url(asset))
            )
            contents = extract_verified(asset, archive_bytes, sums)
            for name in IN_TREE_FILES:
                data = (source_dir / name).read_bytes()
                if name != "SHA256SUMS" and sums[name] != _sha256(data):
                    raise EvidenceAssetError(f"{asset.bundle}: in-tree {name} does not match SHA256SUMS")
                contents[name] = data
            bundle_dir = staging / asset.bundle
            bundle_dir.mkdir()
            for name, data in contents.items():
                (bundle_dir / name).write_bytes(data)
        placed = []
        for asset in config.assets:
            destination = config.output_root / asset.bundle
            if destination.exists():
                shutil.rmtree(destination)
            (staging / asset.bundle).rename(destination)
            placed.append(destination)
    return placed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m evaluator.evidence_assets", description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)
    pack_parser = subparsers.add_parser("pack", help="write reproducible release archives")
    pack_parser.add_argument("--source-root", type=Path, help="directory holding the full bundles")
    pack_parser.add_argument("--output-dir", type=Path, required=True)
    fetch_parser = subparsers.add_parser("fetch", help="download, verify, and assemble the bundles")
    fetch_parser.add_argument("--asset-dir", type=Path, help="read archives from this directory instead")
    fetch_parser.add_argument("--output-root", type=Path, help="override the configured output root")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "pack":
            if args.source_root is not None:
                config = replace(config, source_root=args.source_root)
            print(json.dumps(pack(config, args.output_dir), indent=2, sort_keys=True))
        else:
            if args.output_root is not None:
                config = replace(config, output_root=args.output_root)
            for path in fetch(config, args.asset_dir):
                print(f"verified {path}")
    except (EvidenceAssetError, OSError, KeyError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
