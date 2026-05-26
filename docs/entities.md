# Entities & services

Every entity, event, and service the integration exposes, and how it polls the PDU.

## Inlets

Voltage, current, active power, apparent power, energy (kWh, `total_increasing`), frequency, and power factor. The energy sensor is Energy Dashboard ready.

Single-inlet PDUs keep the inlet sensors flat on the PDU device. Multi-inlet PDUs (ATS, dual-feed) get a sub-device per inlet so each feed can be assigned to its own HA area.

## Outlets

- Per-outlet voltage, current, active power, apparent power, and active energy.
- On/off `switch` entities (requires the PDU role permission **Switch Outlet**).
- Per-outlet power-cycle `button`.
- Each outlet is a sub-device of the PDU, so it can be assigned to its own HA area.
- Switching and cycle entities are only loaded when the PDU reports `hasSwitchableOutlets`.

## Over-current protectors (OCPs)

- Per-OCP `binary_sensor` for trip state (`PROBLEM` device class).
- Per-OCP current and peak-current `sensor` entities.

## Controller & diagnostics

- Per-controller-PSU health `binary_sensor` (`PROBLEM` device class, diagnostic): on when a power supply reports a fault.
- **Refresh capabilities** diagnostic `button` on the PDU device: forces a re-probe and entry reload, for example after attaching a peripheral.

## Environment peripherals (Raritan SmartLock / SmartSensor)

- Numeric (temperature, humidity, air pressure, air flow, dew point) become `sensor` entities, with the unit and device class mapped from the peripheral type.
- State (contact closure, dry contact, on/off, water leak, smoke, motion, tamper, trip) become `binary_sensor` entities, with the device class mapped from the peripheral type.

Peripherals are hot-plug aware: the coordinator re-scans periodically, so attaching or removing one is picked up without a restart.

## Events

- Threshold/alert events via `AlertedSensorManager` polling, exposed as `event` entities plus a `raritan_alert` bus event (requires the PDU role permission **View Local Event Log**).
- Outlet state-change events via local diff, exposed as `event` entities plus a `raritan_outlet_state_changed` bus event.

Both bus events carry the PDU `serial` and the config `entry_id`, so automations can target a specific PDU.

## Services

- `cycle_outlet`: power-cycle a single outlet.
- `set_outlet_state`: turn an outlet on or off (idempotent).
- `reset_energy_counter`: reset the cumulative energy counter on an inlet or outlet (requires the PDU role permission **Administrator Privileges**; see [permissions](permissions.md)).

## How it polls

- A single `DataUpdateCoordinator` polls every 5 s by default (configurable via the options flow, 2-300 s).
- All sensor reads are batched through `BulkRequestHelper`: one HTTP round-trip per tick, even on a 24-outlet PDU.
- Credential rotation triggers an HA reauthentication banner; the new identity is verified by serial number to prevent a silent device swap.
- Repair issues are raised for: TLS verification disabled, firmware below the minimum, and extended unreachability.
- Diagnostics export is anonymized and includes the last 5 alert snapshots and per-domain entity counts.
- Full English and French translations.
