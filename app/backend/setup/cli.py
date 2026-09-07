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

    requested = list(arguments.install)
    known = {component.key for component in manager.components()}

    withheld = manager.withheld_keys(requested)
    if withheld:
        # Silently dropping these would report "already installed" for a tool
        # that was never offered, which is the least useful thing to say.
        print(
            f"Not part of this release, ignoring: {', '.join(withheld)}",
            file=sys.stderr,
        )
    unknown = [key for key in requested if key not in known and key not in withheld]
    if unknown:
        print(f"Unknown component(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"Available: {', '.join(sorted(known))}", file=sys.stderr)
        return 2
    if withheld and not [key for key in requested if key in known]:
        return 2

    plan = manager.build_plan(requested)
    if plan.is_empty():
        print("Everything requested is already installed.")
        return 0

    print(
        f"Plan: {len(plan)} step(s), "
        f"{human_bytes(manager.estimated_download_bytes(requested))} to download, "
        f"needs {human_bytes(manager.estimated_peak_bytes(requested))} free while "
        f"installing — {', '.join(plan.components)}"
    )
    for index, step in enumerate(plan.steps, start=1):
        print(f"  {index:>2}. {step.title}")
    if arguments.dry_run:
        return 0

    failures = blocking_failures(
        run_preflight(
            manager.config,
            manager.estimated_peak_bytes(requested),
            required_memory_bytes=manager.required_memory_bytes(list(arguments.install)),
        )
    )
    if failures:
        for failure in failures:
            print(f"Cannot start setup — {failure.name}: {failure.detail}", file=sys.stderr)
        return 1

    def announce(step, index, total):
        print(f"\n[{index}/{total}] {step.title}", flush=True)

    interactive = sys.stdout.isatty()
    state = {"width": 0}

    def write_line(line):
        # Clear any progress line still on screen before printing over it.
        if state["width"]:
            print("\r" + " " * state["width"] + "\r", end="")
            state["width"] = 0
        print(line, flush=True)

    def show_transfer(update):
        if not interactive:
            # Redirected output gets one line per update rather than a redraw
            # that would fill a log file with control characters.
            print(update.text, flush=True)
            return
        padding = max(0, state["width"] - len(update.text))
        print("\r" + update.text + " " * padding, end="", flush=True)
        state["width"] = len(update.text)

    executor = PlanExecutor(
        plan, on_step=announce, on_output=write_line, on_progress=show_transfer
    )
    succeeded, message = executor.run()
    print(f"\n{message}")
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
