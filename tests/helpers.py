"""Shared test helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from homeassistant.helpers import device_registry as dr

from custom_components.raritan.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry


def entry_devices(hass: HomeAssistant) -> list[DeviceEntry]:
    """Every device the one configured PDU entry owns.

    Goes through the registry helper rather than reading ``devices`` as a
    container: what iterating that container yields changed in 2026.9, and
    the helper reads the same on either side of it.
    """
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    return dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)


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
