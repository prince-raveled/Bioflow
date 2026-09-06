#!/usr/bin/env python3
"""Run BioFlow's full workflow over a prepared sample and report what happened.

Run this from an ordinary terminal, not from an editor's built-in one. Editors
set a positive oom_score_adj on everything they launch - VS Code uses 100 - and
the kernel then prefers to kill the analysis over anything else using the same
memory. MetaPhlAn loads a ~6 GB marker table, which makes it the largest such
target, so the identical command succeeds in a plain shell and is killed in an
editor's. The script says which one it is running in before it starts.

    scripts/run_end_to_end_test.py R1.fastq R2.fastq [--output DIR] [--pairs N]
"""

from pathlib import Path
import argparse
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from backend.config import get_config                       # noqa: E402
from backend.execution.pipeline import PipelineExecutor, default_stages  # noqa: E402
from backend.execution.stage import RunOptions              # noqa: E402
from backend.execution.stages.taxonomy import memory_warnings  # noqa: E402
from backend.project import Project                         # noqa: E402
from backend.samples import ReadLayout                      # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reads", nargs="+", type=Path, help="one FASTQ, or two for paired")
    parser.add_argument("--output", type=Path, default=Path.home() / "bioflow-test" / "e2e-run")
    parser.add_argument("--pairs", type=int, default=None,
                        help="profile only this many read pairs (default: every read)")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--fresh", action="store_true", help="ignore any previous run")
    arguments = parser.parse_args(argv)

    started = time.time()
    timings: dict[str, float] = {}
    running: dict[str, float] = {}

    def log(message: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    def on_event(event) -> None:
        if event.status.value == "running":
            running[event.stage_key] = time.time()
        elif event.stage_key in running:
            timings[event.stage_key] = time.time() - running.pop(event.stage_key)

    for warning in memory_warnings():
        log(f"WARNING: {warning}")

    layout = ReadLayout.PAIRED if len(arguments.reads) == 2 else ReadLayout.SINGLE
    project, problems = Project.from_files(
        "e2e", arguments.output, list(arguments.reads), layout=layout,
        options=RunOptions(threads=arguments.threads,
                           metaphlan_subsample_pairs=arguments.pairs),
    )
    for problem in problems:
        log(f"input problem: {problem}")
    if not project.samples:
        log("No usable samples."); return 1

    log(f"sample : {project.samples[0].describe()}")
    log(f"output : {project.root}")
    log(f"profile: {arguments.pairs or 'every read'}")

    executor = PipelineExecutor(project.context(get_config()), default_stages(),
                                on_log=log, on_event=on_event)
    missing = executor.missing_components()
    if missing:
        log(f"Cannot run: {executor.describe_missing(missing)} not installed.")
        return 1

    log("=== RUN START ===")
    outcome = executor.run(project.samples, resume=not arguments.fresh)
    log("=== RUN END ===")

    print("\n--- STAGES ---", flush=True)
    for record in outcome.record.stages:
        took = timings.get(record.stage_key)
        duration = f"{took / 60:6.1f} min" if took else "     reused"
        print(f"  {record.stage_key:16} {record.status.value:10} {duration}  {record.message[:70]}")
        for check in record.validation.checks:
            print(f"      [{'ok ' if check.passed else 'FAIL'}] {check.description} — {check.detail}")

    workspace = project.workspace
    print("\n--- OUTPUTS ---", flush=True)
    for directory in workspace.all_directories():
        files = sorted(p for p in directory.glob("*") if p.is_file())
        if files:
            print(f"  {directory.name}/")
            for path in files:
                print(f"      {path.name:44} {path.stat().st_size:>12,} bytes")

    profile = workspace.taxonomic_profile(project.samples[0])
    if profile.is_file():
        print("\n--- TAXONOMIC PROFILE (first rows) ---", flush=True)
        for line in profile.read_text(encoding="utf-8").splitlines()[:14]:
            print(f"  {line}")

    print(f"\n  total: {(time.time() - started) / 60:.1f} min", flush=True)
    print(f"  result: {'SUCCESS' if outcome.succeeded else 'FAILED'} — {outcome.message}", flush=True)
    return 0 if outcome.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
