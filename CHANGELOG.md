# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and SemVer.

## [1.0.0] - 2026-05-22

Initial stable release. Verified end-to-end on a PX3-5487V-N2
(24 outlets, 201 entities, ~1.9 s average coordinator tick).

### Added

#### Inlets
- Voltage, current, active power, apparent power, energy (kWh
  `total_increasing`), frequency, power factor. Energy Dashboard ready.
- Conditional inlet sub-devices for multi-inlet PDUs (ATS / dual-feed).
  Each inlet becomes its own sub-device so users can assign Source A
  and Source B to different HA Areas. Single-inlet PDUs keep the inlet
  sensors flat on the parent PDU device.

#### Outlets
- Per-outlet voltage, current, active power, apparent power, active
  energy.
- On/off `switch` entities (requires PDU role permission to switch).
- Per-outlet power-cycle `button`.
- Sub-device hierarchy: each outlet is a child device of the PDU so
  it can be assigned to its own HA Area.

#### Over-current protectors
- Per-OCP `binary_sensor` for trip state (PROBLEM device class).
- Per-OCP numeric sensors: current and peak current.

#### Internal PSU health
- Each controller power-supply state from `Pdu.Sensors.powerSupplyStatus`
  becomes a `binary_sensor` with `BinarySensorDeviceClass.PROBLEM`
  (off = OK, on = problem). Flat on single-PSU SKUs, sub-device per
  PSU when more than one is reported.

#### Environmental peripherals
- Numeric peripherals (temperature, humidity, air pressure, air flow,
  dew point) as `sensor` entities with HA device classes.
- State peripherals (contact closure, on/off, water leak, smoke,
  motion, tamper) as `binary_sensor` entities.

#### Events
- Threshold-alert detection via `AlertedSensorManager` polling. Per-PDU
  `event.<serial>_alert` entity plus `raritan_alert` bus event for
  automations.
- Outlet state-change detection via local diff. Per-outlet
  `event.<outlet>_state_change` entity plus `raritan_outlet_state_changed`
  bus event.

#### Services
- `cycle_outlet`, `set_outlet_state`, `reset_energy_counter`.

#### Configuration
- Config flow over HTTPS with optional CA bundle and a TLS verification
  toggle.
- Reauth flow that detects serial mismatch and prevents silent device
  swap.
- Refresh-capabilities button for one-shot re-probe without reloading
  the entry.

#### Diagnostics
- Anonymized state export. Redacted fields: host, username, serial,
  hw_revision, MAC variants, ca_bundle path.

### Requirements
- Home Assistant 2026.3.0 or later
- Raritan firmware 4.0.10 or later
- PX2, PX3, or PX4 hardware

### Architecture notes
- All telemetry batches via `BulkRequestHelper` into one HTTP roundtrip
  per tick.
- Write paths (toggle, cycle, energy reset, refresh-capabilities) share
  the coordinator's `asyncio.Lock` so they never collide with an
  in-flight read on the SDK's single HTTP connection.
