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
import sys
import tempfile
from pathlib import Path
from typing import Mapping, Sequence


FORMAT_VERSION = 1
MANIFEST_NAME = "af3_db_manifest.json"
OVERLAY_KIND = "af3_reduced_msa_overlay"
EXPECTED_AF3_BIN_BYTES = 1_146_811_260
# 고정 크기를 벗어난 릴리스를 쓰려면 이 환경변수로 명시한다. 조용히 넘어가지 않고
# 경고를 남긴다 (af3_check.sh 의 AF3_MODEL_SHA256 과 같은 성격의 탈출구다).
MODEL_BYTES_ENV = "AF3_MODEL_BYTES"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024

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
