# Troubleshooting & compatibility

## Common issues

- **`raritan_alert` never fires**: the role is missing **View Local Event Log**. The alert poll degrades gracefully but logs a debug line each tick.
- **`switch.turn_on` returns 401**: the role is missing **Switch Outlet**.
- **Re-auth banner after a password change**: expected. Click the banner and supply the new password; HA verifies the PDU's serial matches before saving.
- **Slow ticks (>3 s)**: check the network path. The PDU closes the TLS keep-alive between requests, so every RPC pays a fresh handshake; a slow or lossy link multiplies per-tick latency.
- **Long entity IDs after upgrading**: as of 1.0.2 sub-device entities use bare names ("Outlet 3 Active power"). On upgrade the friendly names clean themselves up, but an entity_id already registered with the old long slug keeps it until you remove and re-add the integration.

## Compatibility

Tested firmware family: **Xerus 4.3.x**. The Raritan SDK pin in `manifest.json` (`raritan>=4.3.13.52458`) sets the wire-protocol baseline. Older firmware (down to the enforced minimum 4.0.10) is expected to work for read paths; newer firmware (4.4+/5.x) loads but is unverified.

## Discovery

The PDU is discovered automatically over **DHCP** (Raritan MAC prefix `00:0D:5D`): Home Assistant raises a discovery notification you can click to add it, and it updates the stored host if the lease IP changes. mDNS / zeroconf is not used (PX3 firmware 4.3.x does not advertise over Bonjour). Manual configuration also works.

## Removing the integration

Open **Settings -> Devices & Services**, open the **Raritan PDU** entry, and choose **Delete** from the three-dot menu. This removes the config entry, its devices, and all entities. Nothing is left behind on the PDU. If you installed via HACS and want to remove the code too, delete the integration from HACS afterwards and restart.

## Reconfiguring

To change the PDU address, credentials, or TLS settings without re-adding the integration, open the **Raritan PDU** entry and choose **Reconfigure**. The flow re-probes the device and refuses the change if the reported serial differs from the originally configured PDU. A password rotation is handled separately through the reauthentication banner, which only asks for username and password. To change or remove the CA bundle, use **Reconfigure** instead.
