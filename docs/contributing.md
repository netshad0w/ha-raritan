# Contributing & hardware reports

Bug reports and PRs are welcome at [GitHub Issues](https://github.com/netshad0w/ha-raritan/issues). A diagnostics export and the PDU model/firmware help triage. Diagnostics dumps are auto-redacted for host, username, serial, MAC, and hardware revision, so they are safe to paste into a public issue as-is.

## Help cover more PDU models

The automated tests run against captured snapshots of real PDUs. If you have a Raritan model not yet covered (PX2, PX4, PXC, ATS variants, dual-feed SKUs, or anything with OCPs or environment peripherals attached), capturing a snapshot and attaching it to a **Hardware capability report** issue grows compatibility without the maintainer needing physical access.

```bash
git clone https://github.com/netshad0w/ha-raritan.git
cd ha-raritan
python -m venv .venv && source .venv/bin/activate
pip install -r requirements_dev.txt

# Captures and anonymizes the nameplate, capabilities, and one telemetry tick.
# The password is read from RARITAN_PASSWORD if set, otherwise prompted for;
# it is never passed on the command line.
export RARITAN_PASSWORD='your-pdu-password'   # optional - omit to be prompted
python scripts/capture_fixtures.py \
    --host <your-pdu-host-or-ip> \
    --user <readonly-user> \
    --output tests/fixtures/<firmware-version>
```

The script anonymizes serial numbers, MAC addresses, IPv4 addresses, and hostnames before writing the fixture files. Diff and verify the output before sharing: a quick `grep` for any value you consider sensitive (rack labels, internal hostnames in custom fields) catches anything the regex missed.

Open the issue with the [Hardware capability report](https://github.com/netshad0w/ha-raritan/issues/new?template=hardware_capability_report.yml) template, paste the anonymized JSON, and note whether outlet switching/metering, OCPs, environment peripherals, multi-inlet, or transfer-switch behavior was observed. Tests for that model can then be added without the maintainer ever connecting to your PDU.

## Running the tests

```bash
python -m pytest tests/ -q
python -m ruff check custom_components/raritan tests scripts
python -m ruff format --check custom_components/raritan tests scripts
python -m mypy custom_components/raritan
```
