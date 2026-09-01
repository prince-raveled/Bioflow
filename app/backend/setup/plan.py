"""Units of work that provision one BioFlow backend component."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


LogCallback = Callable[[str], None]


@dataclass(frozen=True)
class CommandStep:
    """Run one external command and stream its combined output to the log."""

    title: str
    program: str
    arguments: tuple[str, ...] = ()
    working_directory: Path | None = None
    #: Non-zero exit codes that should not fail the plan, for optional steps.
    tolerate_failure: bool = False
    #: Extra attempts for steps whose downloads are large and worth retrying.
    retries: int = 0

    def described_command(self) -> str:
        return " ".join([self.program, *self.arguments])


@dataclass(frozen=True)
class ActionStep:
    """Run one in-process Python action, such as a resumable download."""

    title: str
    action: Callable[[LogCallback], None]


SetupStep = CommandStep | ActionStep


@dataclass
class SetupPlan:
    """An ordered list of steps that installs a selected set of components."""

    steps: list[SetupStep] = field(default_factory=list)
    #: Human-readable names of the components this plan provisions.
    components: list[str] = field(default_factory=list)
    #: Rough download + install footprint, used for the disk-space preflight.
    estimated_bytes: int = 0

    def add(self, *steps: SetupStep) -> None:
        self.steps.extend(steps)

    def is_empty(self) -> bool:
        return not self.steps

    def __len__(self) -> int:
        return len(self.steps)
