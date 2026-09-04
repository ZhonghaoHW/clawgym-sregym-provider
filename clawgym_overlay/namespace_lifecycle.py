"""Provider-owned guards for Kubernetes namespace recreation.

SREGym deletes the application namespace during cleanup.  Kubernetes performs
that deletion asynchronously, so a new process can otherwise observe a
terminating namespace and race the next deployment.  This module keeps the
guard at the provider boundary rather than changing the upstream SREGym
implementation.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol


class NamespaceLifecycleError(RuntimeError):
    """Raised when a namespace cannot be safely reused."""


class NamespaceReader(Protocol):
    def read_namespace(self, *, name: str) -> Any: ...


def wait_for_namespace_recreation(
    api: NamespaceReader,
    namespace: str,
    *,
    timeout: float = 300.0,
    interval: float = 2.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Wait until ``namespace`` is safe for a fresh deployment.

    A healthy existing namespace is reusable and returns immediately.  A
    namespace with a deletion timestamp is not reusable; the function waits
    for the API to report 404.  Only a genuine 404 is treated as deletion, so
    permission and API failures remain fail-closed.
    """

    if not namespace or timeout < 0 or interval <= 0:
        raise NamespaceLifecycleError("namespace recreation parameters are invalid")
    started = monotonic()
    deadline = started + timeout
    saw_terminating = False
    while True:
        try:
            resource = api.read_namespace(name=namespace)
        except Exception as exc:  # Kubernetes ApiException is optional at import time.
            if getattr(exc, "status", None) == 404:
                return "deleted" if saw_terminating else "absent"
            raise NamespaceLifecycleError("namespace status could not be read") from exc

        metadata = getattr(resource, "metadata", None)
        if getattr(metadata, "deletion_timestamp", None) is None:
            return "present"
        saw_terminating = True
        now = monotonic()
        if now >= deadline:
            raise NamespaceLifecycleError("namespace deletion did not complete before timeout")
        sleep(min(interval, max(0.0, deadline - now)))
