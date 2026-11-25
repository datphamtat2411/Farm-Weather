# Cloud-only development and evidence
Task 01 foundation. All compilation and simulation run on GitHub-hosted runners. Required secret: WOKWI_CLI_TOKEN, injected only into the simulation step. Never commit it or publish it in artifacts. No local setup for the user.

## Required stages
- Native: compile common C++ logic with warnings as errors and undefined-behavior sanitizer; run Python tests including deliberately invalid smoke evidence.
- Firmware matrix: F103 Wokwi build and L476RG compile/link probe. Separate artifacts; never present the physical probe as deployable firmware.
- Wokwi: download the exact F103 artifact from this run, verify its checksum, lint the diagram, run the scenario, validate GPIO and complete serial telemetry, reopen the SQLite database and replay the log.
- Acceptance: all required jobs must succeed. External fork PRs lack the token; their result is explicitly incomplete and the acceptance gate fails closed. Only trusted in-repository branches may use the token.
- License provenance: separate upstream check remains active. A pre-existing incorrect SPDX header in map-layer.ts was corrected without changing its executable code. Standalone project code stays in farm-demo; no blanket disabling of upstream checks.

Push and PR runs are independently grouped so an identical commit's PR does not cancel its push check; newer runs of the same event/ref supersede older ones. Running both can consume additional simulation credits. Use current commit's acceptance and license results for review. No skips, timeouts or cancellations count as passing acceptance. Version and digest recording improves repeatability, not bit-identical reproducibility.

## Evidence
native-evidence, firmware-bluepill_f103c8, firmware-nucleo_l476rg (compile evidence only), simulation-evidence (F103 artifact includes BIN/ELF plus checksum). Logs include source SHA, toolchain versions and real test results. Retention 30 days. The Wokwi evidence covers boot, simulated wind alarm/clear, one rain tip, one 300-second record and SQLite idempotency. It is not D01–D07 end-to-end acceptance or physical validation.
