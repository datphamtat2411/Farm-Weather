# Report-to-demo roadmap (scope lock)
Chapter 1: CN01–CN07; Chapter 2: four-layer architecture; Chapter 3: STM32/sensors/energy; Chapter 4: protocol and two-stage buffering; Chapter 5: ThingsBoard PE; Chapter 6: D01–D07.
| Task | Deliverable | Acceptance focus |
|---|---|---|
| 01 | Cloud CI foundation | Actual native, firmware build, Wokwi smoke, persistent SQLite replay; license check and evidence |
| 02 | Wokwi hardware model | Interactive inputs and actuators, explicit simulation substitutions |
| 03 | Acquisition/filters | Correct sampling, debounce, ADC lookup, vector average, validity |
| 04 | Local alarms | Threshold/hysteresis, safe outputs and transition events independent of network |
| 05 | Power scheduling | 300/900 s, simulated sleep/wake, calculated not measured energy |
| 06 | WeatherRecord protobuf | Report field numbers/units/FPorts, cross-language encoding and size |
| 07 | Station queue and simulated LoRaWAN | Application ACK, bounded persistent model, retry, Class A behavior |
| 08 | Edge service | ChirpStack-shaped uplink/MQTT, SQLite transaction before ACK, dedup |
| 09 | Offline synchronization | 72 periodic records in 6 h; retry, verify before SYNCED |
| 10 | ThingsBoard PE | Licensed deployment/access, PostgreSQL, provision Asset/Gateway/Device and adapter |
| 11 | Rule Engine | Live vs historical events, alarms, stale data, buffer warnings |
| 12 | Security | JWT, TLS, four roles with server-side enforcement, no exposed credentials |
| 13 | RPC | Command IDs, expiry, applied acknowledgement, safe actions |
| 14 | Dashboard | Live weather, device, status, alarm, configuration, responsive |
| 15 | History | Period-aware aggregations, missing data, CSV, original timestamps |
| 16 | Demo controls | Repeatable scenario controls through real logic, clear simulation label |
| 17 | End-to-end/release | D01–D07 verified, evidence, cloud access, handover |
No task is done solely because code exists. A task is accepted only when its relevant checks pass and evidence is available. No AI forecasting, soil moisture, pump auto-start, GIS, or unrelated farm-management modules.
