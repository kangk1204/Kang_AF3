#!/usr/bin/env python3
"""Tracked reference figures를 고정 CSV에서 재생성하고 lineage manifest를 쓴다.

재현 가능한 그림만 만든다. raw AF3 output/browser capture가 저장소에 없는 역사적
artifact는 ARTIFACT_MANIFEST.json에 ``historical_not_reproducible``로 남긴다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import statistics
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results_example"
FIGURES = ROOT / "figures"
AF3_REVISION = "97d20234c6eb89e8d05376e9eecc9321e60a559b"
BUILDER_VERSION = 1
PNG_METADATA = {"Software": "Kang_AF3 build_reference_artifacts.py v1"}

REPRODUCIBLE = {
    "figures/baseline_gpu5070ti.png": [
        "results_example/ab_benchmark.csv", "docs/benchmark_report.md"
    ],
    "figures/ab_benchmark.png": ["results_example/ab_benchmark.csv"],
    "figures/msa_threads_scaling.png": ["results_example/msa_threads.csv"],
    "figures/msa_depth_comparison.png": ["results_example/msa_comparison.csv"],
    "figures/db_confidence_comparison.png": [
        "results_example/db_confidence_comparison.csv"
    ],
    "figures/confidence_overview.png": ["results_example/af3_summary.csv"],
    "figures/view3d_index_table.png": ["figures/view3d_screenshot.png"],
    "figures/view3d_molstar_target.png": ["figures/view3d_screenshot.png"],
}

CROPS = {
    "figures/view3d_index_table.png": (520, 1930, 3440, 2260),
    "figures/view3d_molstar_target.png": (535, 75, 1920, 960),
}

HISTORICAL = {
    "figures/example_complex_pae.png": "raw *_confidences.json is not distributed",
    "figures/example_complex_plddt.png": "raw *_confidences.json/mmCIF is not distributed",
    "figures/example_pymol_plddt.png": "original PyMOL session and raw mmCIF are absent",
    "figures/example_summary_6targets.png": "original raw AF3 output is absent",
    "figures/quickstart_a_multifasta.png": "original raw AF3 output is absent",
    "figures/quickstart_b_homodimer_pae.png": "original raw AF3 output is absent",
    "figures/view3d_screenshot.png": "browser/session capture source is absent",
    "examples/view3d_example.html": "embedded AF3 output is retained, but original generator inputs/session are absent",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rendering_environment() -> dict[str, object]:
    """Record the renderer state required for byte-level PNG reproducibility."""
    try:
        import matplotlib
        from matplotlib import font_manager, ft2font

        font_path = Path(font_manager.findfont("DejaVu Sans"))
        return {
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "matplotlib": matplotlib.__version__,
            "freetype": getattr(ft2font, "__freetype_version__", None),
            "font_family": "DejaVu Sans",
            "font_file": font_path.name,
            "font_sha256": sha256(font_path),
            "platform": platform.platform(),
        }
    except (ImportError, OSError) as exc:
        return {"unavailable": str(exc), "python": sys.version.split()[0]}


def rows(name: str):
    with (RESULTS / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def style(plt):
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 9,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.dpi": 100,
        "savefig.dpi": 160,
    })


def save(fig, name: str):
    fig.tight_layout()
    fig.savefig(FIGURES / name, metadata=PNG_METADATA)
    fig.clf()


def build_baseline(plt):
    data = rows("ab_benchmark.csv")
    steady = [float(r["peak_vram_mib"]) for r in data if r.get("peak_vram_mib")]
    # The one-off preallocation/smoke measurements are retained in the benchmark report;
    # the batch value is recomputed from the complete tracked run table.
    values = [15157.0, 5291.0, statistics.median(steady)]
    labels = ["preallocation ON\n(reserved)", "preallocation OFF\none-off smoke",
              "preallocation OFF\nbatch median"]
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    bars = ax.bar(labels, values, color=["#888888", "#2A5C87", "#2A5C87"])
    ax.axhline(16303, color="#C45A2A", ls="--", lw=1.2,
               label="card total: 16,303 MiB")
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value + 250,
                f"{value:,.0f} MiB", ha="center")
    ax.set_ylabel("Peak VRAM (MiB)")
    ax.set_title("RTX 5070 Ti AF3 VRAM observations — MiB throughout\n"
                 "reserved memory is not model demand; workloads/conditions differ")
    ax.legend(frameon=False)
    ax.set_ylim(0, 18000)
    save(fig, "baseline_gpu5070ti.png")


def build_ab(plt):
    data = rows("ab_benchmark.csv")
    grouped = defaultdict(list)
    labels = {
        "B_perproc_nocache": "per-process\ncache off",
        "B_perproc_cache": "per-process\ncache on",
        "A_single_nocache": "single process\ncache off",
        "A_single_cache": "single process\ncache on",
    }
    for row in data:
        if row["condition"] in labels:
            grouped[row["condition"]].append(float(row["wall_per_target_s"]))
    order = list(labels)
    medians = [statistics.median(grouped[key]) for key in order]
    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    bars = ax.bar([labels[k] for k in order], medians,
                  color=["#B75A27", "#D2A087", "#86A5C9", "#24568E"])
    for bar, value in zip(bars, medians):
        ax.text(bar.get_x() + bar.get_width() / 2, value + 0.5,
                f"{value:.2f} s", ha="center")
    ax.set_ylabel("Wall time per target (s)")
    ax.set_title("Median MSA-free AF3 inference, 32 targets x 3 runs (warm page cache)\n"
                 "descriptive runtime benchmark; no biological endpoint")
    save(fig, "ab_benchmark.png")


def build_threads(plt):
    data = rows("msa_threads.csv")
    grouped = defaultdict(list)
    for row in data:
        grouped[int(row["동시타깃수"])].append(
            (int(row["총요구스레드"]), float(row["처리율_건당분"]))
        )
    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    for concurrent in sorted(grouped):
        points = sorted(grouped[concurrent])
        ax.plot([p[0] for p in points], [p[1] for p in points], marker="o",
                label=f"{concurrent} concurrent target(s)")
    ax.axvline(24, color="0.4", ls="--", lw=1, label="24 host cores")
    ax.set_xlabel("Requested threads = n_cpu x 4 DB searches x concurrency")
    ax.set_ylabel("Throughput (targets/min)")
    ax.set_title("AF3 MSA thread sweep — 24-core host, four DB searches")
    ax.legend(frameon=False, fontsize=8)
    save(fig, "msa_threads_scaling.png")


def build_msa_depth(plt):
    data = [r for r in rows("msa_comparison.csv") if r["실험"].startswith("jackhmmer")]
    labels = [r["tag"].replace("vhh_", "").upper() for r in data]
    reduced = [float(r["축소DB_깊이"]) for r in data]
    full = [float(r["전체DB_깊이"]) for r in data]
    x = list(range(len(labels)))
    fig, ax = plt.subplots(figsize=(8.2, 4.4))
    width = 0.38
    ax.bar([v - width / 2 for v in x], reduced, width, label="reduced DB", color="#C44E52")
    ax.bar([v + width / 2 for v in x], full, width, label="full DB", color="#4C72B0")
    ax.set_yscale("log")
    ax.set_xticks(x, labels, rotation=25, ha="right")
    ax.set_ylabel("uniref90 jackhmmer sequence count (log scale)")
    ax.set_title("Tracked VHH MSA-depth measurements (n=6; descriptive)")
    ax.legend(frameon=False)
    save(fig, "msa_depth_comparison.png")


def build_db_comparison(plt):
    data = rows("db_confidence_comparison.csv")
    labels = [r["tag"].replace("vhh_", "").upper() for r in data]
    delta_rank = [float(r["Δranking"]) for r in data]
    delta_plddt = [float(r["ΔpLDDT"]) for r in data]
    ratios = [float(r["unpaired_배수"]) for r in data]
    x = list(range(len(labels)))
    fig, axes = plt.subplots(1, 3, figsize=(11.2, 3.9))
    axes[0].bar(x, ratios, color="#4C72B0")
    axes[0].set_ylabel("full/reduced unpaired depth")
    axes[0].set_title("MSA depth ratio")
    axes[1].bar(x, delta_rank, color=["#C44E52" if v < 0 else "#55A868" for v in delta_rank])
    axes[1].axhline(0, color="0.4", lw=0.8)
    axes[1].set_title("ranking score delta")
    axes[2].bar(x, delta_plddt, color=["#C44E52" if v < 0 else "#55A868" for v in delta_plddt])
    axes[2].axhline(0, color="0.4", lw=0.8)
    axes[2].set_title("atom-weighted mean pLDDT delta")
    for ax in axes:
        ax.set_xticks(x, labels, rotation=35, ha="right", fontsize=7)
    fig.suptitle("Reduced vs full DB, same six VHH monomers — descriptive confidence only")
    save(fig, "db_confidence_comparison.png")


def build_confidence(plt):
    data = rows("af3_summary.csv")
    groups = defaultdict(list)
    for row in data:
        groups[row["타깃"]].append(row)
    labels, reduced, full = [], [], []
    for target in sorted(groups):
        by_condition = {r["조건"]: r for r in groups[target]}
        if "축소DB" in by_condition and "전체DB" in by_condition:
            labels.append(target.replace("vhh_", "").upper())
            reduced.append(float(by_condition["축소DB"]["pLDDT평균"]))
            full.append(float(by_condition["전체DB"]["pLDDT평균"]))
    x = list(range(len(labels)))
    fig, ax = plt.subplots(figsize=(8.2, 4.4))
    width = 0.38
    ax.bar([v - width / 2 for v in x], reduced, width, label="reduced DB", color="#C44E52")
    ax.bar([v + width / 2 for v in x], full, width, label="full DB", color="#4C72B0")
    ax.set_xticks(x, labels, rotation=25, ha="right")
    ax.set_ylim(75, 100)
    ax.set_ylabel("atom-weighted mean pLDDT (local derived metric)")
    ax.set_title("Same six VHH monomers under two DB configurations\n"
                 "descriptive AF3 confidence; not assay/native validation")
    ax.legend(frameon=False)
    save(fig, "confidence_overview.png")


def build_viewer_crops():
    """Rebuild the README's exact browser crops from the retained screenshot."""
    from PIL import Image

    source = Image.open(FIGURES / "view3d_screenshot.png")
    try:
        for artifact, box in CROPS.items():
            cropped = source.crop(box)
            try:
                cropped.save(ROOT / artifact)
            finally:
                cropped.close()
    finally:
        source.close()


def write_manifest():
    artifacts = []
    for artifact, inputs in sorted(REPRODUCIBLE.items()):
        path = ROOT / artifact
        record = {
            "path": artifact,
            "sha256": sha256(path),
            "status": "reproducible_from_tracked_sources",
            "sources": [{"path": item, "sha256": sha256(ROOT / item)} for item in inputs],
            "generator": "python3 scripts/build_reference_artifacts.py --build",
            "generator_version": BUILDER_VERSION,
            "af3_revision": AF3_REVISION,
            "output_terms": "notice_and_pinned_terms_distributed",
        }
        if artifact in CROPS:
            record["transform"] = {
                "operation": "pixel_crop",
                "box": list(CROPS[artifact]),
            }
        artifacts.append(record)
    for artifact, reason in sorted(HISTORICAL.items()):
        path = ROOT / artifact
        artifacts.append({
            "path": artifact,
            "sha256": sha256(path),
            "status": "historical_not_reproducible",
            "reason": reason,
            "sources": [],
            "generator": None,
            "af3_revision": AF3_REVISION,
            "output_terms": "notice_and_pinned_terms_distributed",
        })
    manifest = {
        "schema_version": 1,
        "scope": "tracked reference figures and AF3-derived example viewer",
        "generated_by": "scripts/build_reference_artifacts.py",
        "builder_sha256": sha256(Path(__file__)),
        "terms_sha256": sha256(ROOT / "OUTPUT_TERMS_OF_USE.md"),
        "build_environment": rendering_environment(),
        "reproducibility_scope": (
            "source lineage is portable; PNG byte identity is claimed only when "
            "Python/matplotlib/FreeType/font environment matches build_environment"
        ),
        "artifacts": artifacts,
    }
    destination = ROOT / "ARTIFACT_MANIFEST.json"
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=".ARTIFACT_MANIFEST.", suffix=".tmp", dir=str(ROOT)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(ROOT, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def check_manifest() -> list[str]:
    """Validate the tracked artifact lineage without changing any file."""
    manifest_path = ROOT / "ARTIFACT_MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read ARTIFACT_MANIFEST.json: {exc}"]
    errors = []
    if manifest.get("schema_version") != 1:
        errors.append("unsupported artifact manifest schema_version")
    environment = manifest.get("build_environment")
    if not isinstance(environment, dict) or "python" not in environment:
        errors.append("build_environment is missing")
    if not manifest.get("reproducibility_scope"):
        errors.append("reproducibility_scope is missing")
    expected_artifacts = set(REPRODUCIBLE) | set(HISTORICAL)
    records = manifest.get("artifacts")
    if not isinstance(records, list):
        return errors + ["manifest artifacts must be a list"]
    by_path = {
        record.get("path"): record
        for record in records
        if isinstance(record, dict) and isinstance(record.get("path"), str)
    }
    if set(by_path) != expected_artifacts:
        errors.append(
            "artifact coverage mismatch: expected=%s recorded=%s"
            % (sorted(expected_artifacts), sorted(by_path))
        )
    if manifest.get("builder_sha256") != sha256(Path(__file__)):
        errors.append("builder_sha256 mismatch")
    terms_path = ROOT / "OUTPUT_TERMS_OF_USE.md"
    if not terms_path.is_file() or manifest.get("terms_sha256") != sha256(terms_path):
        errors.append("terms_sha256 mismatch")
    for artifact, record in sorted(by_path.items()):
        path = ROOT / artifact
        if not path.is_file():
            errors.append(f"missing artifact: {artifact}")
            continue
        if record.get("sha256") != sha256(path):
            errors.append(f"artifact hash mismatch: {artifact}")
        sources = record.get("sources")
        if not isinstance(sources, list):
            errors.append(f"sources must be a list: {artifact}")
            continue
        for source in sources:
            if not isinstance(source, dict) or not isinstance(source.get("path"), str):
                errors.append(f"invalid source record: {artifact}")
                continue
            source_path = ROOT / source["path"]
            if not source_path.is_file():
                errors.append(f"missing source: {source['path']}")
            elif source.get("sha256") != sha256(source_path):
                errors.append(f"source hash mismatch: {source['path']}")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser()
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--build", action="store_true", help="rebuild reproducible figures")
    actions.add_argument("--manifest-only", action="store_true", help="refresh manifest hashes only")
    actions.add_argument("--check", action="store_true", help="verify tracked artifacts and lineage without writing")
    args = parser.parse_args(argv)
    if args.check:
        errors = check_manifest()
        for error in errors:
            print("ERROR: " + error)
        if errors:
            return 1
        print("artifact manifest check passed")
        return 0
    if args.build:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        style(plt)
        FIGURES.mkdir(exist_ok=True)
        build_baseline(plt)
        build_ab(plt)
        build_threads(plt)
        build_msa_depth(plt)
        build_db_comparison(plt)
        build_confidence(plt)
        build_viewer_crops()
    write_manifest()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
