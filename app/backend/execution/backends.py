"""Choosing which backend runs a stage's commands.

One function, so that the workflow page, the standalone tool pages and the
executor cannot disagree about what a run is using. The two resolvers present
the same interface, so everything downstream - CommandRunner, the validators,
the checkpoint - is unaffected by which one it holds.

Native is the default and stays the default. It needs nothing beyond what
BioFlow installs for itself, which is what makes a fresh Linux machine work
without asking anything of the person using it. Container execution adds a hard
dependency on a runtime being installed, and exists for reproducibility and for
portability to machines where the Micromamba solve cannot be trusted - not to
replace the desktop path.
"""

from pathlib import Path

from backend.config import BioFlowConfig, get_config
from backend.execution.container import ContainerResolver
from backend.execution.environment import EnvironmentResolver


NATIVE = "native"
CONTAINER = "container"


def backend_for(context=None, config: BioFlowConfig | None = None) -> str:
    """Which backend a run should use.

    The context wins when it names one, because a run that has already started
    must keep the backend it was built with even if the setting changes under
    it. Otherwise the saved preference decides.
    """
    resolved = config or get_config()
    from_context = getattr(context, "execution_backend", "") if context is not None else ""
    return from_context or resolved.execution_backend or NATIVE


def resolver_for(
    context=None,
    config: BioFlowConfig | None = None,
    cidfile_directory: Path | None = None,
):
    """The resolver for this run's backend."""
    resolved = config or get_config()
    if backend_for(context, resolved) == CONTAINER:
        image = getattr(context, "execution_image", "") if context is not None else ""
        return ContainerResolver(
            config=resolved,
            context=context,
            # The context carries a digest once one has been resolved; the
            # configured tag is what to start from when it has not.
            image=image if image and not image.startswith("sha256:") else resolved.container_image,
            cidfile_directory=cidfile_directory,
        )
    return EnvironmentResolver(resolved)


def execution_image_reference(config: BioFlowConfig | None = None) -> str:
    """What identifies the image for the run record and the fingerprint.

    The digest where the runtime can supply one, because a tag can be moved to
    different bytes and a digest cannot; a result should name what actually ran.
    Falls back to the tag so an image that reports no digest still identifies
    itself rather than identifying nothing.
    """
    resolved = config or get_config()
    probe = ContainerResolver(config=resolved, image=resolved.container_image)
    if probe.runtime is None:
        return resolved.container_image
    digest = probe.image_digest()
    if digest and digest != resolved.container_image:
        return f"{resolved.container_image}@{digest}"
    return resolved.container_image
