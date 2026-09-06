"""Recognise the progress and phase lines that installation tools emit.

Downloads here are measured in tens of gigabytes, and the tools that fetch
them report in two very different registers: a redrawn progress line many
thousands of times, and a handful of durable sentences that say what is
actually happening. Treating both the same way buries the second in the first,
so this module separates them.
"""

from __future__ import annotations

from dataclasses import dataclass
import enum
import re


class Phase(enum.Enum):
    """The stage of work a line of tool output indicates."""

    DOWNLOADING = "Downloading"
    VERIFYING = "Verifying"
    EXTRACTING = "Extracting"
    BUILDING = "Building"
    CLEANING = "Reclaiming space"


@dataclass(frozen=True)
class ProgressUpdate:
    """A single redraw of a progress indicator."""

    text: str
    #: Completion from 0 to 100 where the tool reports it, otherwise None.
    percent: float | None = None


_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_RATE = re.compile(r"\d\s*[KMGT]?i?B\s*/\s*(?:sec|s)\b", re.IGNORECASE)
_ETA = re.compile(r"\b(?:eta|remaining|min\s+\d+\s*sec)\b", re.IGNORECASE)
#: Bar glyphs used by conda/mamba, pip, and curl-style indicators.
_BAR = re.compile(r"[━█▏▎▍▌▋▊▉]{3,}|\[=*>?\s*\]|#{5,}")
#: An elapsed-time counter redrawn in place, such as Micromamba's "[+] 17.0s".
#: It reports no position and no rate, only that the tool is still alive, which
#: the stall notice already covers; solving an environment emitted 82 of these
#: in 19 seconds, swamping the lines that said what was happening.
_ELAPSED = re.compile(r"^\[?\+?\]?\s*\d+(?:\.\d+)?\s*(?:s|sec|secs|seconds)$")

#: Matched against a lower-cased line; the earliest match in the line wins.
_PHASE_PATTERNS: tuple[tuple[re.Pattern[str], Phase], ...] = (
    (re.compile(r"\bchecking md5\b|\bverif(?:y|ying)\b|\bchecksum\b"), Phase.VERIFYING),
    # "Decompressing" is how MetaPhlAn announces unpacking, and matching only
    # "uncompress" missed it entirely: the extraction phase never appeared for
    # the one install where a user most wants to see it.
    (
        re.compile(r"\b(?:un|de)compress|\bextract|\buntar|\bunpack|\binflating\b"),
        Phase.EXTRACTING,
    ),
    (re.compile(r"\bdownload(?:ing)?\b|\bfetching\b|\bretrieving\b"), Phase.DOWNLOADING),
    (
        re.compile(r"\bbuilding\b|\bbowtie2-build\b|\bindexing\b|\bjoining\b|\bmerging\b"),
        Phase.BUILDING,
    ),
    (re.compile(r"\bremoving\b|\bdeleting\b|\breclaim"), Phase.CLEANING),
)

#: A line reporting that work has ended names the same verb as the line that
#: began it. Announcing a phase for these rewinds the sequence, so an install
#: that has finished unpacking and tidying up reports "Downloading" last.
_COMPLETION = re.compile(
    r"\b(?:complete|completed|finished|done|installed|successfully)\b", re.IGNORECASE
)


def parse_progress(line: str) -> ProgressUpdate | None:
    """Return an update when the line is a progress redraw, otherwise None.

    A progress indicator is recognised by shape rather than by the delimiter
    that ended it: some tools redraw with a carriage return, but others (
    MetaPhlAn among them) print a full line per update, and those flood a log
    just as badly.
    """
    text = line.strip()
    if not text:
        return None
    if _ELAPSED.match(text):
        return ProgressUpdate(text)
    if _BAR.search(text):
        return ProgressUpdate(text, _first_percent(text))
    percent = _first_percent(text)
    if percent is None:
        return None
    # A percentage alone is not enough: prose can mention one. Require the
    # accompanying transfer rate or countdown that marks a live indicator.
    if _RATE.search(text) or _ETA.search(text):
        return ProgressUpdate(text, percent)
    return None


def _first_percent(text: str) -> float | None:
    match = _PERCENT.search(text)
    if not match:
        return None
    value = float(match.group(1))
    return value if 0.0 <= value <= 100.0 else None


def detect_phase(line: str) -> Phase | None:
    """Classify a durable line of output into the phase it announces."""
    text = line.strip().lower()
    if not text or text.startswith("$ "):
        return None
    if _COMPLETION.search(text):
        return None
    # Whichever verb appears first describes what the tool is starting on.
    # "Downloading and uncompressing bowtie2 indexes" announces a download
    # that an extraction follows, and must not be reported as extraction.
    best: tuple[int, Phase] | None = None
    for pattern, phase in _PHASE_PATTERNS:
        match = pattern.search(text)
        if match and (best is None or match.start() < best[0]):
            best = (match.start(), phase)
    return best[1] if best else None


def condense(text: str) -> str:
    """Strip the drawn bar from a progress line, keeping what it reports.

    A progress bar is drawn for a terminal that overwrites one line. Appended to
    a scrolling log it contributes hundreds of glyphs per update and hides the
    label and percentage that carry the meaning, so the drawing is removed and
    the words either side are kept.
    """
    without_bar = _BAR.sub("", text)
    collapsed = re.sub(r"\s{2,}", " ", without_bar).strip(" .")
    return collapsed or text.strip()
