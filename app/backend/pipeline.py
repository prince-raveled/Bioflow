"""Compatibility shim for the old placeholder pipeline.

The previous implementation printed "Pipeline Finished Successfully" without
executing anything. It has been replaced by a real executor that runs external
tools and validates their outputs. Import from backend.execution.pipeline.
"""

from backend.execution.pipeline import (  # noqa: F401
    PipelineEvent,
    PipelineExecutor,
    PipelineOutcome,
    default_stages,
    stages_by_key,
)

__all__ = [
    "PipelineExecutor",
    "PipelineOutcome",
    "PipelineEvent",
    "default_stages",
    "stages_by_key",
]
