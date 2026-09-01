"""Headless access to the same setup engine the Setup page uses.

Useful for scripted installs, container images, and continuous integration:

    python -m backend.setup.cli --status
    python -m backend.setup.cli --install env:qc db:grch38
"""

import argparse
import sys

from backend.config import get_config
from backend.setup.bootstrap import human_bytes
from backend.setup.executor import PlanExecutor
from backend.setup.manager import SetupManager
from backend.setup.preflight import blocking_failures, run_preflight


def print_status(manager: SetupManager) -> None:
    print(f"Backend location: {manager.config.data_root}")
    print(f"Reference data:   {manager.config.database_root}\n")
    for component in manager.components():
        print(
            f"  [{component.state.value:>12}]  {component.key:<26}  {component.title}"
        )
        if component.state.usable and component.location != component.managed_location:
            print(f"                  using: {component.location}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install BioFlow's analysis backends.")
    parser.add_argument("--status", action="store_true", help="list every component and its state")
    parser.add_argument("--install", nargs="+", metavar="KEY", help="component keys to install")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without running it")
    arguments = parser.parse_args(argv)

    manager = SetupManager(get_config())
    if arguments.status or not arguments.install:
        print_status(manager)
        return 0

    plan = manager.build_plan(list(arguments.install))
    if plan.is_empty():
        print("Everything requested is already installed.")
        return 0

    print(
        f"Plan: {len(plan)} step(s), about {human_bytes(plan.estimated_bytes)} "
        f"— {', '.join(plan.components)}"
    )
    for index, step in enumerate(plan.steps, start=1):
        print(f"  {index:>2}. {step.title}")
    if arguments.dry_run:
        return 0

    failures = blocking_failures(run_preflight(manager.config, plan.estimated_bytes))
    if failures:
        for failure in failures:
            print(f"Cannot start setup — {failure.name}: {failure.detail}", file=sys.stderr)
        return 1

    def announce(step, index, total):
        print(f"\n[{index}/{total}] {step.title}", flush=True)

    executor = PlanExecutor(plan, on_step=announce, on_output=lambda line: print(line, flush=True))
    succeeded, message = executor.run()
    print(f"\n{message}")
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
