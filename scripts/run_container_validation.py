#!/usr/bin/env python
"""Run the released workflow in a container and compare it with a native run.

Dockerisation is not successful because an image builds. It is successful when
the workflow produces the same science it produced natively, so this drives the
real application objects - Project, RunContext, PipelineExecutor - exactly as
the interface does, and then compares the result against a native baseline
file by file.

It never touches the baseline or the reference databases: it writes only into
the output directory given to it, and the databases are mounted read-only.
"""

from __future__ import annotations

import argparse
import bz2
import json
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from backend.config import get_config, reload_config  # noqa: E402
from backend.execution.backends import execution_image_reference  # noqa: E402
from backend.execution.pipeline import PipelineExecutor, default_stages  # noqa: E402
from backend.execution.record import StageStatus  # noqa: E402
from backend.execution.stage import RunOptions  # noqa: E402
from backend.project import Project  # noqa: E402
from backend.samples import ReadLayout  # noqa: E402


def profile_rows(path: Path) -> dict[str, float]:
    """Clade -> relative abundance, from a MetaPhlAn profile."""
    rows: dict[str, float] = {}
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            try:
                rows[parts[0]] = float(parts[2])
            except ValueError:
                continue
    return rows


def compare_profiles(native: Path, produced: Path) -> dict:
    left, right = profile_rows(native), profile_rows(produced)
    shared = set(left) & set(right)
    worst = 0.0
    for clade in shared:
        worst = max(worst, abs(left[clade] - right[clade]))
    return {
        "native_clades": len(left),
        "container_clades": len(right),
        "shared_clades": len(shared),
        "only_native": sorted(set(left) - set(right)),
        "only_container": sorted(set(right) - set(left)),
        "largest_abundance_difference": worst,
        "identical": left == right,
    }


def compare_mapout(native: Path, produced: Path) -> dict:
    """Compare the read-to-marker mapping, decompressed.

    Comparing the .bz2 files directly would report a difference that is only in
    the compressor, so the streams are decompressed first.
    """
    result = {
        "native_bytes": native.stat().st_size if native.is_file() else 0,
        "container_bytes": produced.stat().st_size if produced.is_file() else 0,
    }
    if not (native.is_file() and produced.is_file()):
        result["identical"] = False
        return result
    try:
        left = bz2.open(native, "rb").read()
        right = bz2.open(produced, "rb").read()
    except OSError as error:
        result["identical"] = False
        result["error"] = str(error)
        return result
    result["native_lines"] = left.count(b"\n")
    result["container_lines"] = right.count(b"\n")
    result["identical"] = left == right
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reads", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--name", default="container-validation")
    parser.add_argument("--layout", choices=("paired", "single"), default="paired")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument(
        "--subsample", type=int, default=None,
        help="Profile only this many read pairs, matching the baseline's setting. "
             "Leaving it unset profiles every read, which is a different analysis "
             "and not comparable with a baseline that subsampled.",
    )
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()

    reload_config()
    config = get_config()
    if config.execution_backend != "container":
        print(f"ERROR: configured backend is {config.execution_backend!r}, not 'container'.")
        return 2

    layout = ReadLayout.PAIRED if arguments.layout == "paired" else ReadLayout.SINGLE
    project, problems = Project.from_files(
        arguments.name, arguments.output, [Path(r) for r in arguments.reads],
        layout=layout,
        options=RunOptions(
            threads=arguments.threads,
            metaphlan_subsample_pairs=arguments.subsample,
        ),
    )
    if problems:
        print("ERROR: could not build the project:")
        for problem in problems:
            print("  -", problem)
        return 2

    context = project.context(config)
    print("=" * 78)
    print("BioFlow container validation")
    print("=" * 78)
    print(f"  backend          : {context.execution_backend}")
    print(f"  image            : {context.execution_image}")
    print(f"  shim             : {context.bowtie2_memory_mapped_shim} (provided={context.shim_is_provided})")
    print(f"  memory ceiling   : {context.total_memory() / 1024 ** 3:.2f} GiB")
    print(f"  GRCh38           : {context.host_index_prefix}")
    print(f"  MetaPhlAn db     : {context.metaphlan_database}")
    print(f"  MetaPhlAn index  : {context.metaphlan_index}")
    print(f"  samples          : {[s.name for s in project.samples]} ({arguments.layout})")
    print(f"  subsample pairs  : {arguments.subsample if arguments.subsample else 'none (every read)'}")
    print(f"  output           : {arguments.output}")
    print(f"  baseline         : {arguments.baseline}")
    print("=" * 78, flush=True)

    started = time.time()
    executor = PipelineExecutor(
        context, default_stages(),
        on_log=lambda message: print(message, flush=True),
    )
    outcome = executor.run(project.samples, resume=False)
    elapsed = time.time() - started
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)

    print("\n" + "=" * 78)
    print("STAGES")
    print("=" * 78)
    stages = {}
    for record in outcome.record.stages:
        checks = record.validation.checks if record.validation else []
        passed = sum(1 for check in checks if check.passed)
        stages[f"{record.stage_key}/{record.sample_name}"] = {
            "status": record.status.value,
            "checks_passed": passed,
            "checks_total": len(checks),
            "started_at": record.started_at,
            "finished_at": record.finished_at,
        }
        print(f"  {record.stage_key:<16} {record.sample_name:<14} {record.status.value:<10} "
              f"{passed}/{len(checks)} checks")

    sample = project.samples[0]
    workspace = context.workspace
    produced_profile = workspace.taxonomic_profile(sample)
    produced_mapout = workspace.taxonomic_map(sample)
    baseline_profile = arguments.baseline / "05_taxonomy" / produced_profile.name
    baseline_mapout = arguments.baseline / "05_taxonomy" / produced_mapout.name

    comparison = {
        "profile": compare_profiles(baseline_profile, produced_profile),
        "mapout": compare_mapout(baseline_mapout, produced_mapout),
    }

    print("\n" + "=" * 78)
    print("NATIVE vs CONTAINER")
    print("=" * 78)
    profile = comparison["profile"]
    print(f"  clades           : native {profile['native_clades']}  container {profile['container_clades']}"
          f"  shared {profile['shared_clades']}")
    print(f"  only native      : {profile['only_native'] or 'none'}")
    print(f"  only container   : {profile['only_container'] or 'none'}")
    print(f"  max abundance Δ  : {profile['largest_abundance_difference']:.10f}")
    print(f"  profiles identical: {profile['identical']}")
    mapout = comparison["mapout"]
    print(f"  mapout bytes     : native {mapout['native_bytes']}  container {mapout['container_bytes']}")
    print(f"  mapout identical : {mapout.get('identical')}")

    ownership = []
    for path in sorted(workspace.root.rglob("*")):
        if path.is_file():
            ownership.append(path.stat().st_uid)
    import os
    owned = all(uid == os.getuid() for uid in ownership) if ownership else False

    report = {
        "backend": context.execution_backend,
        "image": context.execution_image,
        "image_reference": execution_image_reference(config),
        "metaphlan_index": context.metaphlan_index,
        "grch38_index": str(context.host_index_prefix),
        "metaphlan_database": str(context.metaphlan_database),
        "layout": arguments.layout,
        "threads": arguments.threads,
        "subsample_pairs": arguments.subsample,
        "memory_ceiling_bytes": context.total_memory(),
        "elapsed_seconds": round(elapsed, 1),
        "peak_child_rss_bytes": usage.ru_maxrss * 1024,
        "succeeded": outcome.succeeded,
        "message": outcome.message,
        "stages": stages,
        "comparison": comparison,
        "files": len(ownership),
        "all_files_owned_by_invoking_user": owned,
    }
    print(f"\n  runtime          : {elapsed / 60:.1f} min")
    print(f"  peak child RSS   : {usage.ru_maxrss / 1024 ** 2:.2f} GiB")
    print(f"  files produced   : {len(ownership)}  all owned by us: {owned}")
    print(f"  overall          : {'SUCCEEDED' if outcome.succeeded else 'FAILED'} - {outcome.message}")

    if arguments.report:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"  report written   : {arguments.report}")

    return 0 if outcome.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
