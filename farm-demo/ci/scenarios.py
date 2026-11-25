"""Mandatory scenario registry and fail-closed single-scenario runner.

The matrix command is offline. Only the explicit hardware workflow calls run.
Never retry the remote simulator automatically: retrying costs quota.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / "firmware"
EVIDENCE = ROOT / "evidence"
MANDATORY = {
    "weather": {"verify_smoke.py", "verify_hardware.py"},
    "invalid-ambient": {"verify_invalid_ambient.py"},
    "local-alarms": {"verify_alarms.py"},
    "power": {"verify_power.py"},
    "link-recovery": {"verify_link.py"},
    "remote-control": {"verify_remote_control.py"},
}

def require(condition, message):
    if not condition:
        raise ValueError(message)

def basename(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", value) and value not in (".", "..")

def validate(entries, root=ROOT):
    require(isinstance(entries, list) and entries, "empty scenario registry")
    ids, outputs = set(), set()
    for entry in entries:
        scenario = entry["id"]
        require(isinstance(scenario, str) and re.fullmatch(r"[a-z0-9-]+", scenario), "invalid scenario ID")
        require(scenario not in ids, "duplicate scenario ID")
        ids.add(scenario)
        require(type(entry["timeout_ms"]) is int and 0 < entry["timeout_ms"] <= 900000, "invalid scenario timeout")
        for output in (entry["serial"], entry["console"]):
            require(basename(output) and output not in outputs and output.endswith(".log"), "invalid or duplicated log name")
            outputs.add(output)
        checks = entry["checks"]
        require(isinstance(checks, list) and checks, "scenario has no verifier")
        scripts = set()
        for check in checks:
            script = check["script"]
            require(basename(script) and script.startswith("verify_") and script.endswith(".py") and
                    (root / "tests" / script).is_file(), "missing or invalid verifier")
            require(script not in scripts, "duplicate verifier")
            scripts.add(script)
            require(isinstance(check["args"], list) and check["args"] and all(basename(a) for a in check["args"]),
                    "invalid verifier args")
            output = check["report"]
            require(basename(output) and output.endswith(".json") and output not in outputs, "invalid or duplicated report name")
            outputs.add(output)
        require(MANDATORY.get(scenario, set()) <= scripts, "existing required verifier removed")
    actual = {p.stem for p in (root / "firmware" / "scenarios").glob("*.yaml")}
    require(ids == actual, f"scenario registry mismatch: registered={sorted(ids)} files={sorted(actual)}")
    require(set(MANDATORY) <= ids, "existing required scenario removed")
    return entries

def load():
    return validate(json.loads((ROOT / "ci" / "scenarios.json").read_text(encoding="utf-8")))

def logged(command, log, cwd, env=None):
    # Preserve the exit code and stream the exact output to both evidence and CI log.
    with log.open("w", encoding="utf-8") as output:
        with subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace") as process:
            for line in process.stdout:
                print(line, end="", flush=True)
                output.write(line)
            code = process.wait()
    if code:
        raise subprocess.CalledProcessError(code, command)

def run(entry):
    require(bool(os.environ.get("WOKWI_CLI_TOKEN")), "Missing WOKWI_CLI_TOKEN; hardware acceptance cannot pass")
    EVIDENCE.mkdir(exist_ok=True)
    serial = EVIDENCE / entry["serial"]
    # Do not accept stale output, including on a retry in a reused working directory.
    for name in [entry["serial"], entry["console"], *(c["report"] for c in entry["checks"])]:
        (EVIDENCE / name).unlink(missing_ok=True)
    if entry["id"] == "weather":
        for suffix in ("", "-wal", "-shm"):
            (EVIDENCE / ("weather.sqlite" + suffix)).unlink(missing_ok=True)
    logged(["wokwi-cli", ".", "--scenario", f"scenarios/{entry['id']}.yaml", "--fail-text", "HARDWARE_ERROR",
            "--timeout", str(entry["timeout_ms"]), "--serial-log-file", str(serial)],
           EVIDENCE / entry["console"], FIRMWARE)
    require(serial.is_file() and serial.stat().st_size > 0, "missing serial evidence")
    env = dict(os.environ)
    env.pop("WOKWI_CLI_TOKEN", None)
    for check in entry["checks"]:
        report = EVIDENCE / check["report"]
        logged([sys.executable, str(ROOT / "tests" / check["script"]), *[str(EVIDENCE / a) for a in check["args"]]],
               report, ROOT, env=env)
        require(json.loads(report.read_text(encoding="utf-8")).get("result") == "PASS",
                f"verifier did not report PASS: {check['script']}")
    wire_report = EVIDENCE / (entry["id"] + "-wire.json")
    wire_report.unlink(missing_ok=True)
    logged([sys.executable, str(ROOT / "tests" / "verify_protocol.py"), "serial", str(serial)],
           wire_report, ROOT, env=env)
    require(json.loads(wire_report.read_text(encoding="utf-8")).get("result") == "PASS",
            "wire protocol verifier did not report PASS")
    print("Scenario verified:", entry["id"])

def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("matrix")
    execute = commands.add_parser("run")
    execute.add_argument("scenario")
    args = parser.parse_args()
    entries = load()
    if args.command == "matrix":
        print(json.dumps([entry["id"] for entry in entries], separators=(",", ":")))
    else:
        selected = next((entry for entry in entries if entry["id"] == args.scenario), None)
        require(selected is not None, "unknown scenario")
        run(selected)

if __name__ == "__main__":
    main()
