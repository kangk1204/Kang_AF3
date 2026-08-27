#!/usr/bin/env python3
"""Build and verify AlphaFold 3 database roots without third-party packages.

The supported reduced configuration is an *MSA overlay*, not a standalone DB.
The overlay contains seven sequence databases.  A complete official DB root is
passed second so AF3 resolves ``pdb_seqres`` and ``mmcif_files`` from it.

Examples::

    python3 scripts/af3_db.py reduce \
        --source ~/public_databases_full \
        --output ~/public_databases_reduced

    python3 scripts/af3_db.py verify \
        --db-dir ~/public_databases_reduced \
        --db-dir ~/public_databases_full
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Mapping, Sequence


FORMAT_VERSION = 1
MANIFEST_NAME = "af3_db_manifest.json"
OVERLAY_KIND = "af3_reduced_msa_overlay"
FULL_MANIFEST_NAME = "af3_full_db_manifest.json"
FULL_FORMAT_VERSION = 1
FULL_KIND = "af3_full_database_seal"
CUSTOM_FULL_KIND = "af3_custom_full_database_seal"
FULL_CONTENT_ALGORITHM = "af3-required-content-v1"
FULL_BINDING_ALGORITHM = "af3-local-binding-v1"
MMCIF_CONTENT_ALGORITHM = "sha256sum-list-v1"
EXPECTED_AF3_BIN_BYTES = 1_146_811_260
# 고정 크기를 벗어난 릴리스를 쓰려면 이 환경변수로 명시한다. 조용히 넘어가지 않고
# 경고를 남긴다 (af3_check.sh 의 AF3_MODEL_SHA256 과 같은 성격의 탈출구다).
MODEL_BYTES_ENV = "AF3_MODEL_BYTES"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
PINNED_MMCIF_ENTRIES = 195_858
PINNED_MMCIF_SHA256 = "c5512426e160df6dfa9533175f4eef3ec31539faa9aa14b2127d0f8d22cf3458"
PINNED_FULL_FILES: dict[str, tuple[int, str]] = {
    "bfd-first_non_consensus_sequences.fasta": (18_171_626_364, "fd87dca06401b03f4ac3c59a82dac14db491a7933ed6abaa19e14e02c6eb1af5"),
    "mgy_clusters_2022_05.fa": (128_579_703_018, "9e7f50956c19cbcd8181dc5e9d7d6eebc08257cc858fc07d3ec88fd6b48dbbc9"),
    "uniref90_2022_05.fa": (71_821_260_491, "f0c61e13a6f71ec2b19e44d35acb531ed3a06a4a839fc12feb80d3adf883c049"),
    "uniprot_all_2021_04.fa": (108_447_942_931, "76f32efd5c6ba73857b0beb3bf1ff823cf0dbef3d876c70d80ee387db13a169d"),
    "pdb_seqres_2022_09_28.fasta": (232_899_463, "1b3bc853322c32f2eea818065b8f569a18d25a52326a8d2c2c3de85752e55fe1"),
    "nt_rna_2023_02_23_clust_seq_id_90_cov_80_rep_seq.fasta": (80_977_012_680, "14c05ac0827c9bf06a37acfc4b3dd1d66e48d5a5f713c0de68611aa7fedc00f9"),
    "rfam_14_9_clust_seq_id_90_cov_80_rep_seq.fasta": (228_433_680, "55ef718071244ad7433678ba249aaeb67707b499f0189a38edadca8d64972318"),
    "rnacentral_active_seq_id_90_cov_80_linclust.fasta": (13_860_314_914, "6c33f15c48d2ac8d7d42a8699ff2e7bd6a4816f8a074157522d3c5b591f927eb"),
}

MSA_FASTA_NAMES = (
    "bfd-first_non_consensus_sequences.fasta",
    "mgy_clusters_2022_05.fa",
    "uniref90_2022_05.fa",
    "uniprot_all_2021_04.fa",
    "nt_rna_2023_02_23_clust_seq_id_90_cov_80_rep_seq.fasta",
    "rfam_14_9_clust_seq_id_90_cov_80_rep_seq.fasta",
    "rnacentral_active_seq_id_90_cov_80_linclust.fasta",
)

REQUIRED_ENTRIES = MSA_FASTA_NAMES + (
    "pdb_seqres_2022_09_28.fasta",
    "mmcif_files",
)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def _atomic_write_json(path: Path, value: object) -> None:
    """Write one new regular JSON file durably without following a link."""

    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to replace existing seal path: {path}")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _regular_file_binding(path: Path) -> dict[str, object]:
    info = os.lstat(path)
    if not os.path.isfile(path) or os.path.islink(path):
        raise ValueError(f"full DB entry is not a regular file: {path}")
    return {
        "bytes": info.st_size,
        "device": info.st_dev,
        "inode": info.st_ino,
        "mtime_ns": info.st_mtime_ns,
    }


def _mmcif_binding(path: Path) -> dict[str, object]:
    """Return a payload-free binding for the consumed, flat mmCIF directory."""

    root_info = os.lstat(path)
    if not os.path.isdir(path) or os.path.islink(path):
        raise ValueError(f"mmcif_files is not a regular directory: {path}")
    digest = hashlib.sha256()
    count = 0
    total_bytes = 0
    try:
        entries = sorted(os.scandir(path), key=lambda entry: entry.name)
    except OSError as exc:
        raise ValueError(f"cannot scan mmcif_files {path}: {exc}") from exc
    for entry in entries:
        candidate = Path(entry.path)
        # AF3 and the pinned installer tree digest consume only the extracted
        # ``*.cif`` members.  Official downloads may retain a regular archive
        # beside them (for example ``mmcif_files.tar.gz``); it is not an
        # effective input and therefore must not enter the binding or seal.
        if not entry.name.lower().endswith(".cif"):
            continue
        info = os.lstat(candidate)
        if os.path.islink(candidate):
            raise ValueError(f"mmcif_files contains a symlink: {candidate}")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"mmcif_files contains a non-regular entry: {candidate}")
        encoded_name = entry.name.encode("utf-8", "surrogateescape")
        digest.update(encoded_name)
        digest.update(b"\0")
        digest.update(
            f"{info.st_size}\0{info.st_mtime_ns}\0{info.st_dev}\0{info.st_ino}\n".encode()
        )
        count += 1
        total_bytes += info.st_size
    if count == 0:
        raise ValueError(f"mmcif_files contains no regular .cif files: {path}")
    return {
        "device": root_info.st_dev,
        "inode": root_info.st_ino,
        "mtime_ns": root_info.st_mtime_ns,
        "entries": count,
        "bytes": total_bytes,
        "sha256": digest.hexdigest(),
    }


def full_database_binding(root: Path | str) -> dict[str, object]:
    """Capture cheap local metadata for every AF3-consumed full-DB object."""

    directory = Path(root).expanduser().absolute()
    root_info = os.lstat(directory)
    if not os.path.isdir(directory) or os.path.islink(directory):
        raise ValueError(f"full DB root is not a regular directory: {directory}")
    entries: dict[str, object] = {}
    for name in REQUIRED_ENTRIES:
        candidate = directory / name
        if name == "mmcif_files":
            entries[name] = {"kind": "directory", **_mmcif_binding(candidate)}
        else:
            entries[name] = {"kind": "file", **_regular_file_binding(candidate)}
    snapshot: dict[str, object] = {
        "algorithm": FULL_BINDING_ALGORITHM,
        "root_device": root_info.st_dev,
        "root_inode": root_info.st_ino,
        "entries": entries,
    }
    snapshot["sha256"] = hashlib.sha256(_canonical_json_bytes(snapshot)).hexdigest()
    return snapshot


def _deep_mmcif_content(path: Path) -> dict[str, object]:
    """Match the installer's pinned sha256sum-list tree digest in one deep pass."""

    binding = _mmcif_binding(path)
    digest = hashlib.sha256()
    candidates = [
        candidate
        for candidate in sorted(path.iterdir(), key=lambda item: item.name)
        if candidate.name.lower().endswith(".cif")
    ]
    # Match the installer's historical `xargs -P 8 sha256sum` throughput while
    # retaining deterministic filename order in the outer tree digest. Submit
    # bounded batches so Python 3.8 does not materialize ~196k Future objects.
    with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as executor:
        for start in range(0, len(candidates), 256):
            batch = candidates[start:start + 256]
            for candidate, file_sha in zip(batch, executor.map(_sha256_file, batch)):
                # _mmcif_binding already rejected linked or special CIF entries.
                line = f"{file_sha}  ./{candidate.name}\n"
                digest.update(line.encode("utf-8", "surrogateescape"))
    return {
        "kind": "directory",
        "algorithm": MMCIF_CONTENT_ALGORITHM,
        "entries": binding["entries"],
        "bytes": binding["bytes"],
        "sha256": digest.hexdigest(),
    }


def _content_identity(files: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    identity: dict[str, object] = {
        "algorithm": FULL_CONTENT_ALGORITHM,
        "entries": {name: dict(files[name]) for name in sorted(files)},
    }
    identity["sha256"] = hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()
    return identity


def _require_content_matches_binding(
    content_entries: Mapping[str, Mapping[str, object]],
    binding: Mapping[str, object],
) -> None:
    binding_entries = binding.get("entries")
    if not isinstance(binding_entries, Mapping) or set(binding_entries) != set(REQUIRED_ENTRIES):
        raise ValueError("full DB binding does not have the exact required entries")
    for name, record in content_entries.items():
        bound = binding_entries.get(name)
        if not isinstance(bound, Mapping):
            raise ValueError(f"full DB binding record is missing: {name}")
        if record.get("bytes") != bound.get("bytes"):
            raise ValueError(f"full DB content/binding byte count mismatch: {name}")
        if name == "mmcif_files" and record.get("entries") != bound.get("entries"):
            raise ValueError("full DB content/binding mmCIF count mismatch")


def _require_official_content_pins(
    content_entries: Mapping[str, Mapping[str, object]],
) -> None:
    if set(PINNED_FULL_FILES) != set(REQUIRED_ENTRIES) - {"mmcif_files"}:
        raise ValueError("internal pinned full-DB file table is incomplete")
    for name, (expected_bytes, expected_sha) in PINNED_FULL_FILES.items():
        record = content_entries.get(name)
        if not isinstance(record, Mapping) or (
            record.get("bytes") != expected_bytes
            or record.get("sha256") != expected_sha
        ):
            raise ValueError(f"full DB seal does not match the pinned AF3 object: {name}")
    mmcif = content_entries.get("mmcif_files")
    if not isinstance(mmcif, Mapping) or (
        mmcif.get("entries") != PINNED_MMCIF_ENTRIES
        or mmcif.get("sha256") != PINNED_MMCIF_SHA256
    ):
        raise ValueError("full DB seal does not match the pinned extracted mmCIF tree")


def _publish_full_seal(
    root: Path,
    content_entries: Mapping[str, Mapping[str, object]],
    binding: Mapping[str, object],
    *,
    kind: str = FULL_KIND,
) -> dict[str, object]:
    if kind not in {FULL_KIND, CUSTOM_FULL_KIND}:
        raise ValueError(f"unsupported full DB seal kind: {kind}")
    if set(content_entries) != set(REQUIRED_ENTRIES):
        raise ValueError("full DB seal content does not have the exact required entries")
    for name, record in content_entries.items():
        digest = record.get("sha256")
        byte_count = record.get("bytes")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError(f"invalid verified content digest for {name}")
        if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count <= 0:
            raise ValueError(f"invalid verified content byte count for {name}")
        if name == "mmcif_files":
            if record.get("kind") != "directory" or record.get("algorithm") != MMCIF_CONTENT_ALGORITHM:
                raise ValueError("invalid verified mmCIF content record")
            count = record.get("entries")
            if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
                raise ValueError("invalid verified mmCIF entry count")
        elif record.get("kind") != "file":
            raise ValueError(f"invalid verified file content record for {name}")
    _require_content_matches_binding(content_entries, binding)
    if kind == FULL_KIND:
        _require_official_content_pins(content_entries)
    manifest: dict[str, object] = {
        "format_version": FULL_FORMAT_VERSION,
        "kind": kind,
        "content_identity": _content_identity(content_entries),
        "local_binding": dict(binding),
    }
    _atomic_write_json(root / FULL_MANIFEST_NAME, manifest)
    return manifest


def seal_full_database(
    root: Path | str, *, enforce_official_pins: bool = True
) -> dict[str, object]:
    """Deep-hash a full DB once, mutation-check it, then publish an atomic seal."""

    directory = Path(root).expanduser().absolute()
    before = full_database_binding(directory)
    content: dict[str, dict[str, object]] = {}
    for name in REQUIRED_ENTRIES:
        candidate = directory / name
        if name == "mmcif_files":
            content[name] = _deep_mmcif_content(candidate)
        else:
            info = _regular_file_binding(candidate)
            content[name] = {
                "kind": "file",
                "bytes": info["bytes"],
                "sha256": _sha256_file(candidate),
            }
    after = full_database_binding(directory)
    if after != before:
        raise ValueError("full DB changed during sealing; no seal was published")
    return _publish_full_seal(
        directory,
        content,
        after,
        kind=FULL_KIND if enforce_official_pins else CUSTOM_FULL_KIND,
    )

# Historical front-slice sizes used for the sequence side of the benchmark.
# Rfam was already small and is copied completely.
DEFAULT_LIMITS: dict[str, int | None] = {
    "uniref90_2022_05.fa": 520_000_000,
    "bfd-first_non_consensus_sequences.fasta": 420_000_000,
    "mgy_clusters_2022_05.fa": 420_000_000,
    "uniprot_all_2021_04.fa": 320_000_000,
    "rnacentral_active_seq_id_90_cov_80_linclust.fasta": 60_000_000,
    "nt_rna_2023_02_23_clust_seq_id_90_cov_80_rep_seq.fasta": 60_000_000,
    "rfam_14_9_clust_seq_id_90_cov_80_rep_seq.fasta": None,
}


def _first_nonempty_line(path: Path) -> bytes | None:
    try:
        with path.open("rb") as handle:
            for line in handle:
                if line.strip():
                    return line
    except OSError:
        return None
    return None


def _validate_source_fasta(path: Path) -> str | None:
    if not path.is_file():
        return f"missing FASTA: {path}"
    try:
        if path.stat().st_size <= 0:
            return f"empty FASTA: {path}"
    except OSError as exc:
        return f"cannot stat FASTA {path}: {exc}"
    first = _first_nonempty_line(path)
    if first is None or not first.startswith(b">"):
        return f"invalid FASTA header: {path}"
    return None


def copy_fasta_prefix(source: Path, destination: Path, limit: int | None) -> dict[str, object]:
    """Copy complete FASTA records until at least ``limit`` bytes are written.

    The next record header is never written after the limit is reached.  This
    means a finite-limit output is normally a little larger than the requested
    byte count, because the final sequence record is completed.
    """

    if limit is not None and limit <= 0:
        raise ValueError("FASTA byte limit must be positive")
    problem = _validate_source_fasta(source)
    if problem:
        raise ValueError(problem)

    digest = hashlib.sha256()
    records = 0
    written = 0
    seen_header = False
    with source.open("rb") as src, destination.open("xb") as dst:
        for line_number, line in enumerate(src, 1):
            if line.startswith(b">"):
                if seen_header and limit is not None and written >= limit:
                    break
                seen_header = True
                records += 1
            elif line.strip() and not seen_header:
                raise ValueError(
                    f"sequence data before first FASTA header: {source}:{line_number}"
                )
            dst.write(line)
            digest.update(line)
            written += len(line)
        dst.flush()
        os.fsync(dst.fileno())

    if records == 0 or written == 0:
        destination.unlink(missing_ok=True)
        raise ValueError(f"no FASTA records copied from {source}")
    return {
        "source_name": source.name,
        "source_bytes": source.stat().st_size,
        # For sliced files this is deliberately a prefix hash.  Hashing every
        # complete source again would add a second ~422 GB read to reduction.
        "source_sha256": digest.hexdigest() if limit is None else None,
        "source_prefix_sha256": digest.hexdigest(),
        "output_bytes": written,
        "output_sha256": digest.hexdigest(),
        "records": records,
        "limit_bytes": limit,
    }


def create_reduced_overlay(
    source: Path | str,
    output: Path | str,
    *,
    limits: Mapping[str, int | None] | None = None,
) -> dict[str, object]:
    """Create a reduced MSA overlay and publish it atomically."""

    source = Path(source).expanduser().resolve()
    output = Path(output).expanduser().absolute()
    selected = dict(DEFAULT_LIMITS)
    if limits is not None:
        unknown = sorted(set(limits) - set(MSA_FASTA_NAMES))
        if unknown:
            raise ValueError("unknown overlay FASTA names: " + ", ".join(unknown))
        selected.update(limits)

    if not source.is_dir():
        raise ValueError(f"source DB directory does not exist: {source}")
    if output.exists() or output.is_symlink():
        raise ValueError(f"output already exists; choose a new directory: {output}")
    try:
        output.resolve().relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("output must not be inside the source DB directory")

    # Fail before writing anything if even one source is missing or malformed.
    source_problems = [
        problem
        for name in MSA_FASTA_NAMES
        if (problem := _validate_source_fasta(source / name)) is not None
    ]
    if source_problems:
        raise ValueError("; ".join(source_problems))

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.tmp.", dir=str(output.parent))
    )
    try:
        files: dict[str, dict[str, object]] = {}
        for name in sorted(MSA_FASTA_NAMES):
            files[name] = copy_fasta_prefix(
                source / name, staging / name, selected.get(name)
            )
        manifest: dict[str, object] = {
            "format_version": FORMAT_VERSION,
            "kind": OVERLAY_KIND,
            "algorithm": "complete-fasta-prefix-v1",
            "requires_fallback_entries": [
                "pdb_seqres_2022_09_28.fasta",
                "mmcif_files",
            ],
            "files": files,
        }
        manifest_path = staging / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, output)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _find_entry(roots: Sequence[Path], name: str) -> tuple[Path | None, str | None]:
    for root in roots:
        candidate = root / name
        if not candidate.exists() and not candidate.is_symlink():
            continue
        if candidate.is_symlink():
            return None, f"symlink is not Docker-portable: {candidate}"
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            return None, f"cannot resolve {candidate}: {exc}"
        if not _within(resolved, root.resolve()):
            return None, f"entry escapes DB root: {candidate}"
        return candidate, None
    return None, f"missing database entry {name} in roots: " + ", ".join(map(str, roots))


def _overlay_manifest_errors(root: Path) -> list[str]:
    """Cheaply validate a generated overlay without rereading multi-GB FASTAs."""

    manifest_path = root / MANIFEST_NAME
    if not manifest_path.exists() and not manifest_path.is_symlink():
        return []
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return [f"overlay manifest is not a regular file: {manifest_path}"]
    try:
        size = manifest_path.stat().st_size
    except OSError as exc:
        return [f"cannot stat overlay manifest {manifest_path}: {exc}"]
    if size <= 0 or size > MAX_MANIFEST_BYTES:
        return [f"overlay manifest has unsafe size {size}: {manifest_path}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"cannot read overlay manifest {manifest_path}: {exc}"]
    if not isinstance(manifest, dict):
        return [f"overlay manifest must be an object: {manifest_path}"]
    errors: list[str] = []
    if manifest.get("format_version") != FORMAT_VERSION:
        errors.append(f"overlay manifest format_version mismatch: {manifest_path}")
    if manifest.get("kind") != OVERLAY_KIND:
        errors.append(f"overlay manifest kind mismatch: {manifest_path}")
    files = manifest.get("files")
    if not isinstance(files, dict):
        return errors + [f"overlay manifest files map is missing: {manifest_path}"]
    missing = sorted(set(MSA_FASTA_NAMES) - set(files))
    if missing:
        errors.append("overlay manifest is missing files: " + ", ".join(missing))
    for name in MSA_FASTA_NAMES:
        info = files.get(name)
        if not isinstance(info, dict):
            continue
        expected_bytes = info.get("output_bytes")
        digest = info.get("output_sha256")
        if (
            not isinstance(expected_bytes, int)
            or isinstance(expected_bytes, bool)
            or expected_bytes <= 0
        ):
            errors.append(f"overlay manifest has invalid output_bytes for {name}")
            continue
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            errors.append(f"overlay manifest has invalid output_sha256 for {name}")
        candidate = root / name
        try:
            actual_bytes = candidate.stat().st_size
        except OSError as exc:
            errors.append(f"overlay manifest entry cannot be read {candidate}: {exc}")
            continue
        if candidate.is_symlink() or not candidate.is_file():
            errors.append(f"overlay manifest entry is not a regular file: {candidate}")
        elif actual_bytes != expected_bytes:
            errors.append(
                f"overlay manifest size mismatch for {candidate}: "
                f"expected {expected_bytes}, got {actual_bytes}"
            )
    return errors


def _load_full_manifest(root: Path) -> dict[str, object]:
    path = root / FULL_MANIFEST_NAME
    if not path.exists() and not path.is_symlink():
        raise ValueError(
            f"full DB seal is missing: {path}; run `python3 scripts/af3_db.py "
            f"seal-full --db-dir {root}` once (deep verification), or use the runner's "
            "explicit --allow-unsealed-db compatibility option"
        )
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"full DB seal is not a regular file: {path}")
    if info.st_size <= 0 or info.st_size > MAX_MANIFEST_BYTES:
        raise ValueError(f"full DB seal has unsafe size {info.st_size}: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read full DB seal {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"full DB seal must be an object: {path}")
    return value


def validate_full_database_seal(
    root: Path | str, *, require_official: bool = False
) -> dict[str, object]:
    """Validate schema, stable content identity and current cheap local binding."""

    directory = Path(root).expanduser().absolute()
    manifest = _load_full_manifest(directory)
    if manifest.get("format_version") != FULL_FORMAT_VERSION:
        raise ValueError("full DB seal format_version mismatch")
    kind = manifest.get("kind")
    if kind not in {FULL_KIND, CUSTOM_FULL_KIND}:
        raise ValueError("full DB seal kind mismatch")
    if require_official and kind != FULL_KIND:
        raise ValueError("full DB seal is custom/unpinned, not the official pinned database")
    if set(manifest) != {"format_version", "kind", "content_identity", "local_binding"}:
        raise ValueError("full DB seal has unexpected or missing top-level fields")
    content = manifest.get("content_identity")
    if not isinstance(content, dict):
        raise ValueError("full DB seal content_identity is missing")
    if set(content) != {"algorithm", "entries", "sha256"}:
        raise ValueError("full DB seal content_identity schema mismatch")
    if content.get("algorithm") != FULL_CONTENT_ALGORITHM:
        raise ValueError("full DB seal content algorithm mismatch")
    content_entries = content.get("entries")
    if not isinstance(content_entries, dict) or set(content_entries) != set(REQUIRED_ENTRIES):
        raise ValueError("full DB seal does not list the exact required entries")
    for name, record in content_entries.items():
        if not isinstance(record, dict):
            raise ValueError(f"full DB seal entry is not an object: {name}")
        expected_fields = {"kind", "bytes", "sha256"}
        if name == "mmcif_files":
            expected_fields |= {"algorithm", "entries"}
            if record.get("kind") != "directory":
                raise ValueError("full DB seal mmcif_files kind mismatch")
            if record.get("algorithm") != MMCIF_CONTENT_ALGORITHM:
                raise ValueError("full DB seal mmcif algorithm mismatch")
            if not isinstance(record.get("entries"), int) or record.get("entries", 0) <= 0:
                raise ValueError("full DB seal mmcif entry count is invalid")
        elif record.get("kind") != "file":
            raise ValueError(f"full DB seal file kind mismatch: {name}")
        if set(record) != expected_fields:
            raise ValueError(f"full DB seal entry schema mismatch: {name}")
        if not isinstance(record.get("bytes"), int) or record.get("bytes", 0) <= 0:
            raise ValueError(f"full DB seal byte count is invalid: {name}")
        digest = record.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ValueError(f"full DB seal digest is invalid: {name}")
    unsigned_content = dict(content)
    stored_content_sha = unsigned_content.pop("sha256")
    expected_content_sha = hashlib.sha256(_canonical_json_bytes(unsigned_content)).hexdigest()
    if stored_content_sha != expected_content_sha:
        raise ValueError("full DB seal stable content identity digest mismatch")
    stored_binding = manifest.get("local_binding")
    if not isinstance(stored_binding, dict):
        raise ValueError("full DB seal local_binding is missing")
    current_binding = full_database_binding(directory)
    if stored_binding != current_binding:
        raise ValueError(
            "full DB local binding changed after sealing; run seal-full only after "
            "investigating the changed file/inode/mtime"
        )
    _require_content_matches_binding(content_entries, current_binding)
    if kind == FULL_KIND:
        _require_official_content_pins(content_entries)
    # The stable record intentionally excludes root paths and inode/mtime data.
    record = {
        "kind": kind,
        "format_version": FULL_FORMAT_VERSION,
        "content_identity": content,
        "manifest_sha256": _sha256_file(directory / FULL_MANIFEST_NAME),
        "binding_sha256": current_binding["sha256"],
    }
    if kind == CUSTOM_FULL_KIND:
        record["warning"] = "custom/unpinned DB content; not the official pinned AF3 database"
    return record


def metadata_only_full_database_identity(root: Path | str) -> dict[str, object]:
    """Explicitly labelled compatibility identity; never used when a seal exists."""

    directory = Path(root).expanduser().absolute()
    seal = directory / FULL_MANIFEST_NAME
    if seal.exists() or seal.is_symlink():
        # A malformed/stale seal is evidence of an integrity problem, not permission
        # to downgrade silently to weaker metadata.
        return validate_full_database_seal(directory)
    binding = full_database_binding(directory)
    return {
        "kind": "unsealed-full-database-metadata-only",
        "warning": "NOT a stable content identity; compatibility opt-in only",
        "binding": binding,
    }


def database_root_identity(
    root: Path | str, *, allow_unsealed: bool = False
) -> dict[str, object]:
    """Return the shared runtime identity contract for one ordered DB root."""

    directory = Path(root).expanduser().absolute()
    overlay_path = directory / MANIFEST_NAME
    full_path = directory / FULL_MANIFEST_NAME
    if overlay_path.exists() or overlay_path.is_symlink():
        errors = _overlay_manifest_errors(directory)
        if errors:
            raise ValueError("; ".join(errors))
        if full_path.exists() or full_path.is_symlink():
            raise ValueError(f"DB root has both reduced and full manifests: {directory}")
        try:
            payload = json.loads(overlay_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read reduced DB manifest {overlay_path}: {exc}") from exc
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, dict):
            raise ValueError(f"reduced DB manifest files map is missing: {overlay_path}")
        verified_files = []
        for name, expected in sorted(files.items()):
            if name not in MSA_FASTA_NAMES or not isinstance(expected, dict):
                raise ValueError(f"unexpected reduced DB manifest entry: {name}")
            candidate = directory / name
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError(f"reduced DB file is missing or linked: {candidate}")
            actual_bytes = candidate.stat().st_size
            actual_sha = _sha256_file(candidate)
            if actual_bytes != expected.get("output_bytes"):
                raise ValueError(f"reduced DB byte count mismatch: {candidate}")
            if actual_sha != expected.get("output_sha256"):
                raise ValueError(f"reduced DB content hash mismatch: {candidate}")
            verified_files.append(
                {"name": name, "bytes": actual_bytes, "sha256": actual_sha}
            )
        return {
            "kind": OVERLAY_KIND,
            "manifest_sha256": _sha256_file(overlay_path),
            "verified_files": verified_files,
            "root": str(directory),
        }
    if full_path.exists() or full_path.is_symlink():
        record = validate_full_database_seal(directory)
        record["root"] = str(directory)
        return record
    if allow_unsealed:
        record = metadata_only_full_database_identity(directory)
        record["root"] = str(directory)
        return record
    # Call through the loader to produce the same actionable missing-seal error.
    _load_full_manifest(directory)
    raise AssertionError("unreachable")


def verify_database_roots(roots: Sequence[Path | str]) -> dict[str, object]:
    """Resolve and validate all AF3 DB entries from ordered roots."""

    paths = [Path(root).expanduser().absolute() for root in roots]
    errors: list[str] = []
    if not paths:
        errors.append("at least one DB root is required")
    for root in paths:
        if not root.is_dir():
            errors.append(f"DB root is not a directory: {root}")
        else:
            errors.extend(_overlay_manifest_errors(root))

    resolved: dict[str, str] = {}
    if not errors:
        for name in REQUIRED_ENTRIES:
            path, problem = _find_entry(paths, name)
            if problem:
                errors.append(problem)
                continue
            assert path is not None
            if name == "mmcif_files":
                if not path.is_dir():
                    errors.append(f"mmcif_files is not a directory: {path}")
                    continue
                try:
                    sample = next(
                        child
                        for child in path.iterdir()
                        if child.is_file()
                        and not child.is_symlink()
                        and child.suffix.lower() == ".cif"
                        and child.stat().st_size > 0
                    )
                except (StopIteration, OSError):
                    errors.append(f"mmcif_files contains no nonempty .cif file: {path}")
                    continue
                if sample.is_symlink():
                    errors.append(f"mmcif sample is a symlink: {sample}")
                    continue
            else:
                problem = _validate_source_fasta(path)
                if problem:
                    errors.append(problem)
                    continue
            resolved[name] = str(path)

    return {
        "ok": not errors,
        "roots": [str(path) for path in paths],
        "resolved": resolved,
        "errors": errors,
    }


def _expected_model_bytes() -> tuple[int | None, str | None]:
    """Return (expected size, override note). ``None`` size means the env value is bad."""

    raw = os.environ.get(MODEL_BYTES_ENV)
    if raw is None:
        return EXPECTED_AF3_BIN_BYTES, None
    try:
        value = int(raw)
    except ValueError:
        return None, f"{MODEL_BYTES_ENV} is not an integer: {raw!r}"
    if value <= 0:
        return None, f"{MODEL_BYTES_ENV} must be a positive byte count: {raw!r}"
    return value, (
        f"af3.bin size pin overridden by {MODEL_BYTES_ENV}={value}; "
        f"the verified release is {EXPECTED_AF3_BIN_BYTES} bytes"
    )


def verify_model_dir(model_dir: Path | str) -> dict[str, object]:
    root = Path(model_dir).expanduser().absolute()
    model = root / "af3.bin"
    errors: list[str] = []
    warnings: list[str] = []
    size = None
    expected_bytes, override_note = _expected_model_bytes()
    if expected_bytes is None:
        errors.append(override_note or f"invalid {MODEL_BYTES_ENV}")
    elif override_note is not None:
        warnings.append(override_note)
    if not root.is_dir():
        errors.append(f"model root is not a regular directory: {root}")
    elif model.is_symlink() or not model.is_file():
        errors.append(f"missing regular model file: {model}")
    else:
        try:
            size = model.stat().st_size
        except OSError as exc:
            errors.append(f"cannot stat model file {model}: {exc}")
        else:
            if size <= 0:
                errors.append(f"empty model file: {model}")
            elif expected_bytes is not None and size != expected_bytes:
                errors.append(
                    f"unexpected af3.bin size {size}; expected {expected_bytes} "
                    "for the pinned AF3 release. If you deliberately run a different "
                    f"verified release, set {MODEL_BYTES_ENV} to its byte count"
                )
    return {
        "ok": not errors,
        "model": str(model),
        "bytes": size,
        "errors": errors,
        "warnings": warnings,
    }


def _parse_limit(spec: str) -> tuple[str, int | None]:
    if "=" not in spec:
        raise argparse.ArgumentTypeError("limit must be NAME=BYTES or NAME=all")
    name, value = spec.split("=", 1)
    if name not in MSA_FASTA_NAMES:
        raise argparse.ArgumentTypeError(f"unknown FASTA name: {name}")
    if value.lower() == "all":
        return name, None
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid byte limit: {value}") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("byte limit must be positive")
    return name, number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and verify AlphaFold 3 database roots"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    reduce_parser = sub.add_parser(
        "reduce", help="create a reduced MSA overlay from a complete DB"
    )
    reduce_parser.add_argument("--source", required=True, help="complete official DB root")
    reduce_parser.add_argument("--output", required=True, help="new overlay directory")
    reduce_parser.add_argument(
        "--limit",
        action="append",
        default=[],
        type=_parse_limit,
        metavar="NAME=BYTES",
        help="override a per-FASTA byte limit; use NAME=all to copy all records",
    )

    verify_parser = sub.add_parser(
        "verify", help="verify a complete ordered set of DB roots"
    )
    verify_parser.add_argument(
        "--db-dir", action="append", required=True, help="DB root, repeat in priority order"
    )
    verify_parser.add_argument("--json", action="store_true", help="print machine-readable JSON")

    seal_parser = sub.add_parser(
        "seal-full", help="deep-hash and atomically seal one complete full DB"
    )
    seal_parser.add_argument("--db-dir", required=True, help="complete full DB root")

    validate_parser = sub.add_parser(
        "validate-full-seal", help="cheaply validate a full DB seal and local binding"
    )
    validate_parser.add_argument("--db-dir", required=True)
    validate_parser.add_argument("--json", action="store_true")
    validate_parser.add_argument(
        "--require-official",
        action="store_true",
        help="reject a custom/unpinned seal",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "reduce":
            overrides = dict(args.limit)
            manifest = create_reduced_overlay(
                args.source, args.output, limits=overrides or None
            )
            print(f"created {OVERLAY_KIND}: {Path(args.output).expanduser().absolute()}")
            for name, info in manifest["files"].items():
                print(f"  {name}: {info['records']} records, {info['output_bytes']} bytes")
            print("use this overlay before a complete fallback root:")
            print(f"  --db-dir {Path(args.output).expanduser().absolute()} --db-dir <FULL_DB>")
            return 0

        if args.command == "seal-full":
            manifest = seal_full_database(args.db_dir)
            print(
                f"created {FULL_KIND}: "
                f"{Path(args.db_dir).expanduser().absolute() / FULL_MANIFEST_NAME}"
            )
            print(f"stable content identity: {manifest['content_identity']['sha256']}")
            return 0

        if args.command == "validate-full-seal":
            record = validate_full_database_seal(
                args.db_dir, require_official=args.require_official
            )
            if args.json:
                print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                label = (
                    "official pinned content identity"
                    if record["kind"] == FULL_KIND
                    else "custom/unpinned content identity"
                )
                print(f"OK {label}: {record['content_identity']['sha256']}")
            return 0

        report = verify_database_roots(args.db_dir)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            for name, path in report["resolved"].items():
                print(f"OK {name}: {path}")
            for error in report["errors"]:
                print(f"FAIL {error}", file=sys.stderr)
        return 0 if report["ok"] else 1
    except (OSError, ValueError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
