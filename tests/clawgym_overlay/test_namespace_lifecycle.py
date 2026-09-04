from __future__ import annotations

from types import SimpleNamespace

import pytest

from clawgym_overlay.namespace_lifecycle import NamespaceLifecycleError, wait_for_namespace_recreation


class _NotFound(Exception):
    status = 404


class _Api:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def read_namespace(self, *, name: str):
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


def _namespace(*, deleting: bool = False):
    return SimpleNamespace(metadata=SimpleNamespace(deletion_timestamp="2026-09-04T00:00:00Z" if deleting else None))


def test_absent_namespace_is_ready_without_sleep() -> None:
    api = _Api([_NotFound()])
    assert wait_for_namespace_recreation(api, "hotel-reservation") == "absent"
    assert api.calls == 1


def test_healthy_namespace_is_reusable() -> None:
    api = _Api([_namespace()])
    assert wait_for_namespace_recreation(api, "hotel-reservation") == "present"


def test_terminating_namespace_waits_until_deleted() -> None:
    api = _Api([_namespace(deleting=True), _NotFound()])
    now = iter((0.0, 1.0))
    sleeps: list[float] = []
    assert (
        wait_for_namespace_recreation(
            api,
            "hotel-reservation",
            timeout=10,
            interval=2,
            monotonic=lambda: next(now),
            sleep=sleeps.append,
        )
        == "deleted"
    )
    assert sleeps == [2]


def test_timeout_is_fail_closed() -> None:
    api = _Api([_namespace(deleting=True), _namespace(deleting=True)])
    now = iter((0.0, 2.0))
    with pytest.raises(NamespaceLifecycleError, match="did not complete"):
        wait_for_namespace_recreation(
            api,
            "hotel-reservation",
            timeout=1,
            monotonic=lambda: next(now),
            sleep=lambda _seconds: None,
        )


def test_non_not_found_api_error_is_not_treated_as_deleted() -> None:
    error = RuntimeError("forbidden")
    error.status = 403  # type: ignore[attr-defined]
    with pytest.raises(NamespaceLifecycleError, match="could not be read"):
        wait_for_namespace_recreation(_Api([error]), "hotel-reservation")


def test_invalid_wait_parameters_are_rejected() -> None:
    api = _Api([])
    with pytest.raises(NamespaceLifecycleError):
        wait_for_namespace_recreation(api, "", timeout=1)
    with pytest.raises(NamespaceLifecycleError):
        wait_for_namespace_recreation(api, "hotel-reservation", interval=0)
