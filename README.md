# Home Assistant Raritan PDU

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Custom Home Assistant integration for **Raritan intelligent rack PDUs** running Xerus firmware **4.0.10 or later**. Tested against PX3; supports PX2/PX3/PX4 hardware families.

Verified end-to-end on a **PX3-5487V-N2** (24 outlets, 201 entities, ~1.9 s average coordinator tick).

## Features

### Inlets
- Voltage, current, active power, apparent power, energy (kWh, `total_increasing`), frequency, power factor. Energy Dashboard ready.

### Outlets
- Per-outlet voltage, current, active power, apparent power, active energy.
- On/off `switch` entities (requires PDU role permission **Switch Outlet**).
- Per-outlet power-cycle `button`.
- Sub-device hierarchy: each outlet is a child device of the PDU, allowing per-outlet area assignment in HA.
- Capability filtering: switching/cycle entities only loaded when the PDU reports `hasSwitchableOutlets`.

### Over-current protectors (OCPs)
- Per-OCP `binary_sensor` for trip state (`PROBLEM` device class).
- Per-OCP current and peak-current `sensor` entities.

### Environment peripherals (Raritan SmartLock / SmartSensor)
- Numeric: `TEMPERATURE`, `HUMIDITY`, `AIR_PRESSURE`, `AIR_FLOW`, `DEW_POINT` -> `sensor`.
- State: `CONTACT_CLOSURE`, `ON_OFF`, `WATER_LEAK`, `SMOKE`, `MOTION`, `TAMPER` -> `binary_sensor`.

### Events
- Threshold/alert events via `AlertedSensorManager` polling, surfaced as `event.<serial>_alert` and `raritan_alert` bus events (requires PDU role permission **View Local Event Log**).
- Outlet state-change events via local diff, surfaced as `event.<outlet>_state_change` and `raritan_outlet_state_changed`.

### Services
- `cycle_outlet`: power-cycle a single outlet.
- `set_outlet_state`: turn an outlet on/off (idempotent).
- `reset_energy_counter`: reset cumulative energy on an inlet or outlet (requires PDU role permission **Reset Energy Counters**).

### Operations
- Single `DataUpdateCoordinator` polling every 5 s (configurable via options flow, range 2-300 s).
- All sensor reads batched via `BulkRequestHelper`, one HTTP round-trip per tick on a 24-outlet PDU.
- Reauthentication flow: credential rotation triggers an HA reauth UI banner; identity verified by serial number to prevent silent device swap.
- Repair issues: TLS verification disabled, firmware below minimum, extended unreachability.
- Anonymized diagnostics export including last 5 alert snapshots and per-domain entity counts.
- Full English & French translations.

## Requirements

- Home Assistant **2026.3** or later.
- Python 3.14+ (managed by HA).
- Network reachability from HA to the PDU on **HTTPS port 443** (configurable). HTTP works but raises a repair issue.
- A PDU user account. Read-only access works for the inlet/outlet/OCP/env metering subset; full feature set requires the additional permissions noted above.

## Recommended PDU role

For least privilege, create a dedicated PDU user (e.g. `homeassistant`) bound to its own role rather than reusing an existing admin account, then grant only the permissions for the features you actually use:

| Permission (exact Xerus label) | Required for |
|---|---|
| Unrestricted View Privileges | Sensors, capability detection, diagnostics |
| Switch Outlet | `switch` entities, `cycle_outlet`, `set_outlet_state` services, cycle button |
| View Local Event Log | Real `raritan_alert` events (without it, polling silently 401s) |
| Administrator Privileges | `reset_energy_counter` service |

Read-only telemetry works with just **Unrestricted View Privileges**; the rest is opt-in.

> **Note on `reset_energy_counter`**: Xerus has no granular privilege for resetting cumulative energy counters. Per the PDU G2 user guide, `AccumulatingNumericSensor.resetValue()` requires full **Administrator Privileges**. If you don't want to give the HA user full admin, skip this service and reset counters from the Xerus web UI directly.

## Installation

### HACS (recommended, while pending default-repo submission)

1. **HACS -> Integrations -> ⋮ -> Custom repositories**
2. Add `https://github.com/netshad0w/ha-raritan` as type **Integration**.
3. Install **Raritan PDU**, restart Home Assistant.
4. **Settings -> Devices & Services -> Add Integration -> "Raritan PDU"**.

### Manual

Copy `custom_components/raritan/` into your HA config's `custom_components/` directory and restart.

### Removing the integration

Go to **Settings -> Devices & Services**, open the **Raritan PDU** entry, and choose **Delete** from the three-dot menu. This removes the config entry, its devices, and all entities. No files are left behind on the PDU side. If you installed via HACS and want to remove the code too, delete the integration from HACS afterwards and restart.

## Reconfiguring

To change the PDU address, credentials, or TLS settings without re-adding the integration, open the **Raritan PDU** entry and choose **Reconfigure**. The flow re-probes the device and refuses the change if the reported serial differs from the originally configured PDU, preventing a silent device swap. Credential rotation is also handled automatically via the reauthentication banner.

## Configuration

The config flow asks for:

- **Host**: IP or hostname (e.g. `pdu.lab.internal`). Always reached over HTTPS on port 443.
- **Username** / **Password**: PDU account credentials.
- **Verify TLS certificate** (default on; off raises a repair issue).
- **CA bundle** (optional path inside HA config, for self-signed PKI).

Polling interval is editable post-setup via **Configure** on the integration card (2-300 s).

## Use cases

- Feed inlet and per-outlet `active_energy` into the HA Energy Dashboard to track rack consumption and cost per circuit.
- Power-cycle a hung server from an automation, a dashboard button, or a voice assistant.
- Drive automations off attached temperature, humidity, water-leak, or smoke peripherals (alert and shed load when a rack gets too hot).
- Watch OCP trips and PSU health as `PROBLEM` binary sensors, and act on threshold alerts through the `event` entities.

## Examples

Power-cycle an unresponsive server when a connectivity ping fails:

```yaml
automation:
  - alias: "Reboot NAS outlet when unreachable"
    triggers:
      - trigger: state
        entity_id: binary_sensor.nas_reachable
        to: "off"
        for: "00:05:00"
    actions:
      - action: raritan.cycle_outlet
        target:
          entity_id: switch.outlet_3
```

Alert and cut non-critical load when a rack temperature sensor gets too hot:

```yaml
automation:
  - alias: "Shed load on high rack temperature"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.rack_top_temperature
        above: 40
    actions:
      - action: raritan.set_outlet_state
        target:
          entity_id: switch.outlet_8
        data:
          state: false
      - action: notify.mobile_app
        data:
          message: "Rack temperature high; shedding outlet 8."
```

## Troubleshooting

- **`raritan_alert` never fires**: the role is missing the **View Local Event Log** permission. The alert poll degrades gracefully but emits a debug-level log line each tick.
- **`switch.turn_on` returns 401**: the role is missing **Switch Outlet**.
- **Re-auth banner after a password change**: expected. Click the banner, supply the new password; HA verifies the PDU's serial number matches before saving.
- **High CPU / slow ticks (>3 s)**: confirm PDU firmware ≥ 4.0.10. Older firmware lacks `BulkRequestHelper` and falls back to per-sensor RPCs (~50 s on a 24-outlet PDU).
- **Entity IDs look redundant** (`switch.outlet_1_outlet_1`): fixed cosmetically post-creation; existing entity IDs are preserved by HA. Delete and re-add the integration to regenerate clean IDs.

## Compatibility

Tested firmware family: **Xerus 4.3.x**. The Raritan SDK pin in `manifest.json` (`raritan>=4.3.13.52458`) controls the wire-protocol baseline. Older firmware (down to 4.0.10) is expected to work for read paths; newer firmware (4.4+/5.x) will load but is unverified.

mDNS / zeroconf discovery is **not supported**. PX3 firmware 4.3.x does not advertise via Bonjour, so manual configuration is the only entry path.

## Not yet supported

These Raritan capabilities exist in the SDK but aren't exposed by this integration today, mostly because verifying them safely requires hardware the maintainers don't have:

| Capability | SDK surface | Why deferred |
|---|---|---|
| **Transfer Switch (ATS / STS / HTS)** | `pdu.getTransferSwitches()` | Requires a real ATS PDU (dual-source auto-failover) to validate transfer events, source switching, and bypass states. Tracked for v2.0. |
| **Outlet groups** (ganged on/off, group cycle) | `pdu.getOutletGroups()` | Group semantics differ across SKUs; defer until a user has a concrete use case. |
| **Cascade / RS485 chaining** | `pdumodel.Cascade` | Needs multiple chained PDUs; very niche. |
| **Sensor history logging** | `pdu.getSensorLogger()` | Home Assistant's Recorder already provides time-series storage; the SDK's logger would duplicate it. |
| **Outlet/circuit-breaker detailed statistics** | `OutletStatistic`, `CircuitBreakerStatistic` | Counter values; low value vs. the noise they'd add to the entity registry. |

Open an issue if you need one of these; knowing there's a real user makes it worth implementing.

## Contributing

Bug reports and PRs welcome at [GitHub Issues](https://github.com/netshad0w/ha-raritan/issues). Anonymized diagnostics export and PDU model/firmware help triage. **Diagnostics dumps are auto-redacted** for host, username, serial, MAC, and hardware revision, so they're safe to paste into a public issue as-is.

### Help cover more PDU models

The integration's automated tests run against captured snapshots of real PDUs. If you have a Raritan model not yet covered (PX2, PX4, PXC, ATS variants, dual-feed SKUs, or anything with OCPs / env peripherals attached), running the capture script and attaching the output to a "Hardware capability report" issue helps grow compatibility without the maintainer needing physical access:

```bash
git clone https://github.com/netshad0w/ha-raritan.git
cd ha-raritan
python -m venv .venv && source .venv/bin/activate
pip install -r requirements_dev.txt

# Captures + anonymizes nameplate, capabilities, and a single telemetry tick
python scripts/capture_fixtures.py \
    --host <your-pdu-host-or-ip> \
    --user <readonly-user> --pass <password> \
    --output tests/fixtures/<firmware-version>
```

The script anonymizes serial numbers, MAC addresses, IPv4 addresses, and hostnames before writing the fixture files. Diff and verify before sharing: a quick `grep` for any value you consider sensitive (rack labels, internal hostnames in custom fields) catches anything the regex missed.

Open the issue with the **Hardware capability report** template, paste the anonymized JSON snippets, and confirm whether outlet switching / metering, OCPs, env peripherals, multi-inlet, or transfer-switch behavior was observed. Tests for that model can then be added without the maintainer ever connecting to your PDU.

## License

MIT. See [LICENSE](LICENSE).
