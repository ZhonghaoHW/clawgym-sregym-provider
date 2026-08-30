"""ClawGym integration overlay for the pinned SREGym provider fork."""

from clawgym_overlay.release import (
    MANIFEST_FILENAMES,
    SREGymReleaseBuilder,
    build_environment_release,
    load_release_manifests,
    provider_configuration_digests,
)
from clawgym_overlay.provenance import ProviderProvenanceError, source_revision
from clawgym_overlay.materializer import MaterializationError, materialize_reference_profile

__all__ = [
    "MANIFEST_FILENAMES",
    "SREGymReleaseBuilder",
    "build_environment_release",
    "load_release_manifests",
    "provider_configuration_digests",
    "ProviderProvenanceError",
    "source_revision",
    "MaterializationError",
    "materialize_reference_profile",
]
