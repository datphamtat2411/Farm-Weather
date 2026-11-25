# Task 16 — Trung tâm mô phỏng / cloud scenario runner

Scope: Ch. 3–6, D01–D06 presentation controls. D07 remains the real JWT/RBAC/RPC tests of Task 12/13, not a fake role switch. Task 17 remains hardware and full product acceptance.

## Run entirely in GitHub Actions
Every development commit executes the five built-in scenarios under farm-demo/demo/scenarios, validates their evidence and uploads native-evidence. Open Actions → Farm weather development CI → native-evidence → demo-scenarios/<scenario>/report.html or report.json. The HTML report is a browser-readable presentation timeline with inputs, alarm bitmask, actuator state, requested reporting period, station/Edge queues, Cloud model state and records. It is not a live ThingsBoard dashboard.

To control a scenario without a local environment, edit the JSON in GitHub on the development branch (or add a uniquely named JSON preset). CI executes every preset. The CLI can also run one custom JSON via the cloud runner:
```bash
python farm-demo/demo/run.py --scenario farm-demo/demo/scenarios/weather-fault.json --output /tmp/farm-run
```
Prerequisites: same generated Protobuf bindings and compiler as development CI. Optional --pace 1 through 100 sets logical seconds per wall second; default 0 advances as fast as the core can execute. Each output directory must be new. Reset by starting another isolated run; no production database or device is reset.

## Controls
Every segment has seconds and set. Settings persist across segments except rain_tips and confirm (reset by default). wind_hz (0–30) is contact pulses per logical second; rain_tips (0–10) is contact pulses each second; noise adds 1 ms bounce on both and an ADC outlier; temperature, humidity, pressure are environmental inputs; direction is one of eight compass bearings; battery_mv is a simulated battery; ambient_ok and wind_ok inject diagnosed input faults; lora/lte control independent links; confirm models physical inspection. The simulation starts at a fixed UTC epoch, not current weather. No pump-start action exists.

Native station.cpp is a host adapter using the same firmware Station, acquisition, LocalActuation, SampleSchedule, wire encoder, Ring and ClassA. It sends actual wire bytes through gateway.handle (ChirpStack-shaped event and real SQLite commit/ACK) to Synchronizer and an independent isolated SQLite history model. Readback is performed through the worker's Cloud interface. It does not bypass firmware thresholds or directly alter UI weather values. The native adapter uses logical sensor inputs and an in-memory NOR model, not the Wokwi I²C peripheral model or physical Flash. The existing Wokwi scenarios and actual ThingsBoard security/dashboard integration remain mandatory at their respective gates. For live Wokwi demonstrations use the existing diagram controls and UART LINK DOWN/UP; for Task 17 execute registered hardware scenarios with quota available. No new Wokwi remote runs on development pushes.

Status bits: 1 wind, 2 rain, 4 heat, 8 low battery, 16 sensor fault. Relay represents stop request only. Report contains both generated records and independently verified count. A custom scenario ending offline can legitimately have unsynchronized records; the built-in acceptance verifier requires complete recovery and checks explicit scenario outcomes. All data is labeled simulated.
