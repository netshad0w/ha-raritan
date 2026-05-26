# Home Assistant Raritan PDU

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Custom Home Assistant integration for **Raritan intelligent rack PDUs** running Xerus firmware **4.0.10 or later** (PX2/PX3/PX4 families). Inlet and per-outlet metering, outlet switching, environment peripherals, threshold alerts, and Energy Dashboard support.

Verified end-to-end on a **PX3-5487V-N2** (24 outlets, ~200 entities). A steady-state poll is a single HTTP round-trip.

## Features at a glance

- **Metering**: inlet and per-outlet voltage, current, power, and energy (kWh, `total_increasing`, Energy Dashboard ready).
- **Control**: per-outlet on/off switches and power-cycle buttons, loaded only when the PDU reports switchable outlets.
- **Protection & health**: OCP trip and per-PSU health binary sensors, plus threshold/alert events.
- **Environment**: Raritan SmartLock / SmartSensor peripherals as sensors and binary sensors.
- **Services**: `cycle_outlet`, `set_outlet_state`, `reset_energy_counter`.
- DHCP discovery, reauth on credential rotation, anonymized diagnostics, English and French.

Full reference: [docs/entities.md](docs/entities.md).

## Requirements

- Home Assistant **2026.5.4** or later (Python 3.14, managed by HA).
- Network reachability from HA to the PDU over **HTTPS** (port 443; HTTP works but raises a repair issue).
- A PDU account. Read-only access covers metering; switching, alerts, and energy resets need extra role permissions (see [docs/permissions.md](docs/permissions.md)).

## Installation

### HACS (recommended)

1. HACS -> three-dot menu -> **Custom repositories**.
2. Add `https://github.com/netshad0w/ha-raritan` as type **Integration**.
3. Install **Raritan PDU**, then restart Home Assistant.
4. **Settings -> Devices & Services -> Add Integration -> "Raritan PDU"**.

### Manual

Copy `custom_components/raritan/` into your HA config's `custom_components/` directory and restart.

## Configuration

The config flow asks for the host, username and password, whether to verify the TLS certificate (default on), and an optional CA bundle path for self-signed PKI. The polling interval (2-300 s) is editable afterwards via **Configure** on the integration card.

To change the host, credentials, or TLS later, use **Reconfigure**. It re-probes the device and refuses the change if the reported serial differs from the configured PDU, which prevents a silent device swap. A password change is also handled on its own via the reauthentication banner.

## Documentation

- [Entities & services](docs/entities.md): every entity, event, and service, plus how the coordinator polls.
- [PDU role & permissions](docs/permissions.md): least-privilege role setup and the exact Xerus labels.
- [Automation examples](docs/automations.md): energy tracking, power-cycling, load shedding.
- [Troubleshooting & compatibility](docs/troubleshooting.md): common issues, firmware support, DHCP discovery, removing the integration.
- [Contributing & hardware reports](docs/contributing.md): help cover more PDU models.

## License

MIT. See [LICENSE](LICENSE).
