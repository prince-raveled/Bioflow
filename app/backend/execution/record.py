"""Structured records of what a pipeline stage actually did.

Every stage records its command, environment, timing, exit code, captured
output, produced files, and the result of validating those files. A stage is
successful only when the command exited zero *and* its outputs validate.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import hashlib
import json


class StageStatus(str, Enum):
    """Lifecycle of one stage for one sample."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    #: Reused from a previous run because its outputs are still valid.
    SKIPPED = "skipped"
    #: Never attempted because an earlier required stage failed.
    BLOCKED = "blocked"

    @property
    def is_success(self) -> bool:
        return self in (StageStatus.COMPLETED, StageStatus.SKIPPED)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class ValidationCheck:
    """One assertion about a stage's output."""

    description: str
    passed: bool
    detail: str = ""


@dataclass
class ValidationResult:
    """Whether a stage's outputs are usable, and the evidence for that."""

    checks: list[ValidationCheck] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)

    def add(self, description: str, passed: bool, detail: str = "") -> "ValidationResult":
        self.checks.append(ValidationCheck(description, passed, detail))
        return self

    def failures(self) -> list[ValidationCheck]:
        return [check for check in self.checks if not check.passed]

    def summary(self) -> str:
        if self.valid:
            return f"{len(self.checks)} output check(s) passed"
        return "; ".join(
            f"{check.description}{f' ({check.detail})' if check.detail else ''}"
            for check in self.failures()
        )


@dataclass
class CommandRecord:
    """Exactly what was executed, and what came back."""

    description: str
    program: str
    arguments: list[str]
    environment: str
    started_at: str = ""
    finished_at: str = ""
    exit_code: int | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    #: Tail of the captured output, kept for the interface and history.
    stderr_tail: str = ""

    @property
    def command_line(self) -> str:
        return " ".join([self.program, *self.arguments])

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


@dataclass
class StageRecord:
    """One stage's execution for one sample."""

    stage_key: str
    stage_title: str
    sample_name: str
    status: StageStatus = StageStatus.PENDING
    commands: list[CommandRecord] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    validation: ValidationResult = field(default_factory=ValidationResult)
    message: str = ""
    log_path: str | None = None
    started_at: str = ""
    finished_at: str = ""
    #: Identifies the inputs and commands this result was produced from, so a
    #: changed input or a changed command invalidates the checkpoint.
    fingerprint: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "StageRecord":
        validation = ValidationResult(
            checks=[ValidationCheck(**check) for check in data.get("validation", {}).get("checks", [])]
        )
        return cls(
            stage_key=data["stage_key"],
            stage_title=data.get("stage_title", data["stage_key"]),
            sample_name=data["sample_name"],
            status=StageStatus(data.get("status", "pending")),
            commands=[CommandRecord(**command) for command in data.get("commands", [])],
            outputs=list(data.get("outputs", [])),
            validation=validation,
            message=data.get("message", ""),
            log_path=data.get("log_path"),
            started_at=data.get("started_at", ""),
            finished_at=data.get("finished_at", ""),
            fingerprint=data.get("fingerprint", ""),
        )


def fingerprint_for(commands: list[list[str]], inputs: list[Path]) -> str:
    """Identify a stage run by its commands and the state of its input files.

    Using size and modification time alongside the command means an edited or
    replaced input invalidates the checkpoint, which a bare existence check
    would not catch.
    """
    digest = hashlib.sha256()
    for command in commands:
        digest.update("\x1f".join(command).encode("utf-8"))
        digest.update(b"\x1e")
    for path in sorted(inputs):
        digest.update(str(path).encode("utf-8"))
        try:
            stat = path.stat()
            digest.update(f"{stat.st_size}:{int(stat.st_mtime)}".encode("utf-8"))
        except OSError:
            digest.update(b"missing")
        digest.update(b"\x1e")
    return digest.hexdigest()[:32]


@dataclass
class PipelineRecord:
    """The whole run: every stage for every sample, plus the overall verdict."""

    project_name: str
    started_at: str = field(default_factory=now)
    finished_at: str = ""
    status: StageStatus = StageStatus.PENDING
    stages: list[StageRecord] = field(default_factory=list)
    message: str = ""

    def add(self, record: StageRecord) -> None:
        self.stages = [
            existing
            for existing in self.stages
            if not (existing.stage_key == record.stage_key and existing.sample_name == record.sample_name)
        ]
        self.stages.append(record)

    def find(self, stage_key: str, sample_name: str) -> StageRecord | None:
        for record in self.stages:
            if record.stage_key == stage_key and record.sample_name == sample_name:
                return record
        return None

    @property
    def failures(self) -> list[StageRecord]:
        return [record for record in self.stages if record.status is StageStatus.FAILED]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "project_name": self.project_name,
                    "started_at": self.started_at,
                    "finished_at": self.finished_at,
                    "status": self.status.value,
                    "message": self.message,
                    "stages": [record.to_dict() for record in self.stages],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "PipelineRecord | None":
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        record = cls(
            project_name=data.get("project_name", ""),
            started_at=data.get("started_at", ""),
            finished_at=data.get("finished_at", ""),
            status=StageStatus(data.get("status", "pending")),
            message=data.get("message", ""),
        )
        for entry in data.get("stages", []):
            try:
                record.stages.append(StageRecord.from_dict(entry))
            except (KeyError, ValueError):
                continue  # A malformed entry simply means that stage reruns.
        return record
