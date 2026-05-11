"""Capture anonymized JSON-RPC responses from a real Raritan PDU.

Usage:
    python scripts/capture_fixtures.py --host 10.20.0.42 --user admin --pass secret \
        --output tests/fixtures/4.3.11

The script hits a live PDU, reads metadata + inlet/outlet/OCP/env sensors,
anonymizes serial numbers, IPs, MAC addresses, hostnames and writes the result
as JSON files for use as pytest fixtures.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Anonymization patterns ------------------------------------------------------

SERIAL_RE = re.compile(r"^[A-Z]{2,4}[0-9]{6,}$")
MAC_RE = re.compile(r"\b([0-9A-F]{2}[:-]){5}[0-9A-F]{2}\b", re.IGNORECASE)
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

ANON_SERIAL = "TEST00000001"
ANON_MAC = "00:11:22:33:44:55"
ANON_IPV4 = "10.0.0.1"
ANON_HOST = "test-pdu.invalid"


def anonymize(value: Any, real_host: str) -> Any:
    if isinstance(value, str):
        v = value
        if SERIAL_RE.match(v):
            v = ANON_SERIAL
        v = MAC_RE.sub(ANON_MAC, v)
        v = IPV4_RE.sub(ANON_IPV4, v)
        v = re.sub(re.escape(real_host), ANON_HOST, v, flags=re.IGNORECASE)
        return v
    if isinstance(value, list):
        return [anonymize(x, real_host) for x in value]
    if isinstance(value, dict):
        return {k: anonymize(v, real_host) for k, v in value.items()}
    return value


# Capture ---------------------------------------------------------------------


def capture(host: str, user: str, password: str, output: Path) -> None:
    from raritan.rpc import Agent, pdumodel  # type: ignore[import-not-found]

    agent = Agent(host, user, password, disable_certificate_verification=True)
    pdu = pdumodel.Pdu("/model/pdu/0", agent)

    output.mkdir(parents=True, exist_ok=True)

    # Probe metadata
    metadata = pdu.getMetaData()
    inlets = pdu.getInlets()
    outlets = pdu.getOutlets()
    ocps = pdu.getOverCurrentProtectors()
    env = pdu.getExternalSensors()

    snapshots: dict[str, Any] = {
        "metadata": metadata,
        "inlets": [
            {
                "label": inlet.getLabel(),
                "metadata": inlet.getMetaData(),
                "sensor_logical_properties": {
                    name: getattr(inlet.sensors, name).getLogicalProperties()
                    for name in (
                        "voltage",
                        "current",
                        "activePower",
                        "apparentPower",
                        "powerFactor",
                        "frequency",
                        "activeEnergy",
                    )
                    if hasattr(inlet.sensors, name) and getattr(inlet.sensors, name) is not None
                },
            }
            for inlet in inlets
        ],
        "outlet_count": len(outlets),
        "ocp_count": len(ocps),
        "env_count": len(env),
    }

    serializable = json.loads(json.dumps(snapshots, default=str))
    anonymized = anonymize(serializable, host)

    out_file = output / "snapshot.json"
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(anonymized, f, indent=2, sort_keys=True)
    print(f"Wrote {out_file}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture anonymized PDU fixtures.")
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--pass", dest="password", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    capture(args.host, args.user, args.password, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
