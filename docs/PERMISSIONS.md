# PDU role & permissions

The integration works with a read-only PDU account for metering. Switching, alerts, and energy resets each need an extra permission, granted per feature.

For least privilege, create a dedicated PDU user (for example `homeassistant`) bound to its own role rather than reusing an admin account, then grant only what the features you use require:

| Permission (exact Xerus label) | Required for |
|---|---|
| Unrestricted View Privileges | Sensors, capability detection, diagnostics |
| Switch Outlet | `switch` entities, `cycle_outlet` and `set_outlet_state` services, cycle button |
| View Local Event Log | Real `raritan_alert` events (without it, the alert poll silently 401s) |
| Administrator Privileges | `reset_energy_counter` service |

Read-only telemetry works with just **Unrestricted View Privileges**; everything else is opt-in.

## Why `reset_energy_counter` needs full admin

Xerus has no granular privilege for resetting cumulative energy counters. Per the PDU G2 user guide, `AccumulatingNumericSensor.resetValue()` requires full **Administrator Privileges**. If you would rather not give the HA user full admin, skip this service and reset counters from the Xerus web UI directly.

## TLS

The PDU is always reached over HTTPS on port 443. Certificate verification is on by default; turning it off raises a repair issue. For a self-signed PKI, point the optional CA bundle at a `.pem`/`.crt`/`.cer` file inside the HA config directory.
