# Automation examples

## What people use it for

- Feed inlet and per-outlet `active_energy` into the HA Energy Dashboard to track rack consumption and cost per circuit.
- Power-cycle a hung server from an automation, a dashboard button, or a voice assistant.
- Drive automations off attached temperature, humidity, water-leak, or smoke peripherals.
- Watch OCP trips and PSU health as `PROBLEM` binary sensors, and act on threshold alerts through the `event` entities.

## Power-cycle an unresponsive server

Cycle an outlet when a connectivity ping has been down for five minutes:

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

## Shed load on high rack temperature

Cut a non-critical outlet and notify when a rack sensor gets too hot:

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
