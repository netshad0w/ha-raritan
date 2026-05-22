"""Shared test helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


def make_fake_bulk_helper_class() -> MagicMock:
    """Build a ``MagicMock`` replacement for the SDK's ``BulkRequestHelper``.

    The real helper inspects ``method.parent.target`` (a real string) on every
    queued bound method, which ``MagicMock`` cannot satisfy. Instead, we record
    queued calls and dispatch them to the mock methods on ``perform_bulk()``,
    mirroring the SDK contract where each request can independently fail.
    """

    cls = MagicMock()
    # Every helper built by the factory is appended here so tests can assert how
    # many bulk roundtrips happened and how many requests each batched.
    cls.instances = []

    def _factory(_agent: Any) -> MagicMock:
        instance = MagicMock()
        queued: list[tuple[Any, tuple[Any, ...]]] = []

        def _add_request(method: Any, *args: Any) -> None:
            queued.append((method, args))

        def _perform_bulk() -> list[Any]:
            results: list[Any] = []
            for method, args in queued:
                try:
                    results.append(method(*args))
                except Exception as exc:  # mirror SDK: each request can independently fail
                    results.append(exc)
            queued.clear()
            return results

        instance.add_request.side_effect = _add_request
        instance.perform_bulk.side_effect = _perform_bulk
        cls.instances.append(instance)
        return instance

    cls.side_effect = _factory
    return cls
