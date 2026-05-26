"""DeviceInfo builders: bare sub-device names, PDU carried by serial_number.

The sub-device name is what HA prepends to every entity's friendly name, so it
stays short ("Outlet 3" -> "Outlet 3 Active power"). The owning PDU is conveyed
by ``via_device`` (UI nesting) and ``serial_number`` (info box), not a prefix,
which is what keeps several PDUs apart without bloating every entity name.
"""

from __future__ import annotations

from custom_components.raritan.const import DOMAIN
from custom_components.raritan.device_info import (
    env_device_info,
    env_display_name,
    inlet_device_info,
    ocp_device_info,
    outlet_device_info,
    psu_device_info,
    slug_sensor_id,
)
from custom_components.raritan.models import CapabilityMatrix

_CAP = CapabilityMatrix(
    model="PX3-5487V-N2",
    firmware="4.0.30",
    serial="1A77200022",
    hw_revision="0x0A",
    nb_inlets=2,
    outlet_ids=(1, 2, 3),
    ocp_ids=(1,),
    env_sensor_ids=(),
    outlet_switching=True,
    outlet_metering=True,
    nb_psu=2,
)


def test_outlet_name_is_bare_with_serial() -> None:
    info = outlet_device_info(_CAP, 3)
    assert info["name"] == "Outlet 3"
    assert info["serial_number"] == "1A77200022"
    assert info["via_device"] == (DOMAIN, "1A77200022")
    assert (DOMAIN, "1A77200022_outlet_3") in info["identifiers"]


def test_ocp_name_is_bare_with_serial() -> None:
    info = ocp_device_info(_CAP, 1)
    assert info["name"] == "OCP 1"
    assert info["serial_number"] == "1A77200022"
    assert info["via_device"] == (DOMAIN, "1A77200022")


def test_multi_inlet_name_is_bare_with_serial() -> None:
    info = inlet_device_info(_CAP, 2, "10.0.0.1")
    assert info["name"] == "Inlet 2"
    assert info["serial_number"] == "1A77200022"
    assert info["via_device"] == (DOMAIN, "1A77200022")


def test_multi_psu_name_is_bare_with_serial() -> None:
    info = psu_device_info(_CAP, 1)
    assert info["name"] == "PSU 1"
    assert info["serial_number"] == "1A77200022"
    assert info["via_device"] == (DOMAIN, "1A77200022")


def test_env_name_is_the_label_with_serial() -> None:
    info = env_device_info(_CAP, "AA_BB", "Rack top")
    assert info["name"] == "Rack top"
    assert info["serial_number"] == "1A77200022"
    assert info["via_device"] == (DOMAIN, "1A77200022")


def test_single_psu_stays_on_pdu_device() -> None:
    """A single-PSU PDU keeps the PSU health sensor flat on the PDU device."""
    cap = CapabilityMatrix(
        model="PX3-5487V-N2",
        firmware="4.0.30",
        serial="1A77200022",
        hw_revision="0x0A",
        nb_inlets=1,
        outlet_ids=(1,),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=True,
        outlet_metering=True,
        nb_psu=1,
    )
    info = psu_device_info(cap, 1)
    assert info["identifiers"] == {(DOMAIN, "1A77200022")}
    assert "via_device" not in info
    assert "name" not in info


def test_slug_sensor_id_replaces_colons_and_slashes() -> None:
    assert slug_sensor_id("AABB1234:n0") == "AABB1234_n0"
    assert slug_sensor_id("path/to:sensor") == "path_to_sensor"


def test_env_display_name_titlecases_the_type() -> None:
    assert env_display_name("DEW_POINT") == "Dew Point"
    assert env_display_name("temperature") == "Temperature"


def test_single_inlet_stays_on_pdu_device() -> None:
    """A single-inlet PDU keeps inlet sensors flat on the PDU device itself."""
    cap = CapabilityMatrix(
        model="PX3-5487V-N2",
        firmware="4.0.30",
        serial="1A77200022",
        hw_revision="0x0A",
        nb_inlets=1,
        outlet_ids=(1,),
        ocp_ids=(),
        env_sensor_ids=(),
        outlet_switching=True,
        outlet_metering=True,
    )
    info = inlet_device_info(cap, 1, "10.0.0.1")
    assert info["name"] == "Raritan PX3-5487V-N2 (1A77200022)"
    assert (DOMAIN, "1A77200022") in info["identifiers"]
    assert "via_device" not in info
