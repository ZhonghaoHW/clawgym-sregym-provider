"""ClawGym integration overlay for the pinned SREGym provider fork."""

from clawgym_overlay.environment_materializer import EnvironmentMaterializationError, materialize_environment_recipe
from clawgym_overlay.environment_qualification import (
    EnvironmentControlProfile,
    SREGymEnvironmentQualificationBackend,
    build_sregym_qualification_backend,
    default_environment_control_profile,
)
from clawgym_overlay.first_party_dependency import (
    DependencyProvenanceError,
    build_first_party_dependency_attestation,
    build_first_party_sbom_component,
    verify_first_party_dependency_attestation,
    write_attestation_exclusive,
    write_sbom_exclusive,
)
from clawgym_overlay.materializer import MaterializationError, materialize_reference_profile
from clawgym_overlay.platform_observation import (
    PlatformObservationError,
    build_platform_host_observation,
    collect_platform_host_observation,
    require_clean_observation,
)
from clawgym_overlay.provenance import ProviderProvenanceError, source_revision
from clawgym_overlay.release import (
    MANIFEST_FILENAMES,
    SREGymReleaseBuilder,
    build_environment_release,
    load_release_manifests,
    provider_configuration_digests,
)

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
    "EnvironmentMaterializationError",
    "materialize_environment_recipe",
    "EnvironmentControlProfile",
    "SREGymEnvironmentQualificationBackend",
    "build_sregym_qualification_backend",
    "default_environment_control_profile",
    "PlatformObservationError",
    "build_platform_host_observation",
    "collect_platform_host_observation",
    "require_clean_observation",
    "DependencyProvenanceError",
    "build_first_party_dependency_attestation",
    "build_first_party_sbom_component",
    "verify_first_party_dependency_attestation",
    "write_attestation_exclusive",
    "write_sbom_exclusive",
]
