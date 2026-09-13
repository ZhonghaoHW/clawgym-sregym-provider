"""Explicit AgentAdapter composition for the provider worker.

The provider owns environment implementations, while ClawGym owns the
AgentAdapter contracts.  This module is only the host-side composition seam:
it selects a trusted builder by the immutable ``adapter_id`` in the
AgentRelease and never discovers implementations from the filesystem, the
lane, or model metadata.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class AgentAdapterBuildContext:
    """Host-owned inputs supplied after document admission and before effects."""

    agent_release: Mapping[str, Any]
    manifest_root: Path
    materialization_bundle: Path | None
    compatibility_bridge: Mapping[str, Any] | None
    secret_file: str | None
    zeroclaw_logical_profile: Path | None = None
    zeroclaw_config_bundle: Path | None = None
    zeroclaw_executable: Path | None = None
    zeroclaw_config_dir: Path | None = None
    zeroclaw_workspace_dir: Path | None = None
    zeroclaw_message: str | None = None


AgentAdapterBuilder = Callable[[AgentAdapterBuildContext], object]


@dataclass(slots=True)
class AgentAdapterComposition:
    """A closed, exact-ID map of trusted AgentAdapter builders."""

    _builders: dict[str, AgentAdapterBuilder] = field(default_factory=lambda: dict[str, AgentAdapterBuilder]())

    def register(self, adapter_id: str, builder: AgentAdapterBuilder) -> None:
        if type(adapter_id) is not str or not adapter_id:
            raise ValueError("AgentAdapter builder ID must be a non-empty string")
        if not callable(builder):
            raise TypeError("AgentAdapter builder must be callable")
        if adapter_id in self._builders:
            raise ValueError(f"AgentAdapter builder already registered: {adapter_id}")
        self._builders[adapter_id] = builder

    def build(self, context: AgentAdapterBuildContext) -> object:
        adapter_id = context.agent_release.get("adapter_id")
        if type(adapter_id) is not str or not adapter_id:
            raise ValueError("AgentRelease must identify an AgentAdapter")
        try:
            builder = self._builders[adapter_id]
        except KeyError as exc:
            raise ValueError(f"AgentAdapter is not explicitly composed: {adapter_id}") from exc
        adapter = builder(context)
        if getattr(adapter, "provider_id", None) != adapter_id:
            raise ValueError("composed AgentAdapter identity does not match AgentRelease")
        if getattr(adapter, "provider_type", None) != "agent_adapter":
            raise ValueError("composed implementation is not an AgentAdapter")
        return adapter


__all__ = ["AgentAdapterBuildContext", "AgentAdapterBuilder", "AgentAdapterComposition"]
