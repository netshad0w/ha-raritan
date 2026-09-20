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

## Notify on a threshold alert

The PDU raises an alert when a sensor crosses one of its configured thresholds. The `event` entity carries the last one, with the sensor and its state in the attributes. Its entity ID ends in your own PDU's model and serial, so take the real one from **Developer tools -> States**:

```yaml
automation:
  - alias: "Notify on Raritan threshold alert"
    triggers:
      - trigger: state
        entity_id: event.raritan_px3_5487v_n2_0a00000000_alert
    conditions:
      - condition: template
        value_template: "{{ trigger.to_state.attributes.event_type == 'alert_active' }}"
    actions:
      - action: notify.mobile_app
        data:
          message: >-
            {{ trigger.to_state.attributes.parent_label }}
            {{ trigger.to_state.attributes.sensor_label }}:
            {{ trigger.to_state.attributes.alert_state }}
```

`alert_cleared` arrives through the same entity and carries `sensor_id`. The entity holds its value through a failed poll, so the trigger fires on real events only, and an alert raised while Home Assistant was down surfaces on the next start.

With more than one PDU, the `raritan_alert` bus event is easier than naming an entity per device: it carries `serial` and `entry_id` alongside the same four alert fields.

```yaml
    triggers:
      - trigger: event
        event_type: raritan_alert
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
