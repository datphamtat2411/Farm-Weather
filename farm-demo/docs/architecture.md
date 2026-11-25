# Architecture baseline — Task 01
Source of truth: report chapters 1–6 (Topic 12). Single station FARM_WS_01. Keep this boundary for subsequent tasks.

| Layer | Design target | Demo boundary |
|---|---|---|
| Perception | STM32L476RG, BME280 I2C, SEN-15901 wind/rain, SX1262 SPI, buzzer and isolated relay | STM32F103C8 Blue Pill in Wokwi, analog proxies and rain switch in initial smoke test; no claim of real circuit isolation |
| Network | Class A OTAA AS923-2, gateway, local ChirpStack/Mosquitto, 4G LTE | transport behavior simulation; RF/OTAA security not proven by simulation |
| Processing | STM32 persistent flash queue, Edge SQLite, ThingsBoard PE/PostgreSQL | initial JSON serial and SQLite smoke test only |
| Application | ThingsBoard Dashboard, RBAC, RPC, alarms | deferred to Tasks 10–16; licensed PE access must be confirmed |

Report invariants: BME280 60 s, direction 5 s, wind 1 s samples/3 s gust, rain 0.2794 mm/tip, wind debounce 5 ms, rain 50 ms. Reports 300/900 s; wind alarm >=10 m/s, clear <7 for 60 s; rain >=5 mm/600 s, clear <2 mm; heat >=35 C 3 valid readings, clear <33 C 3 readings; low battery <=3000 mV 3 periods, recovery >=3200 mV 3 periods; sensor fault 3 invalid/3 valid. Invalid is never treated as zero. No auto-start irrigation. Flash budget 64 KiB (960 slots in design), SQLite commit before application ACK, Cloud REST verification before SYNCED, timestamps UTC at measurement. Wokwi Blue Pill has only 64 KiB total flash; do not allocate the design's whole 64 KiB buffer on this MCU. A simulated storage backend and target-specific layout are required in Task 07. Initial 10x accelerated simulated clock is not wall time or measured power.

CI gate: compile native tests, run Python tests, build firmware with pinned versions, lint diagram, run scenario, validate serial JSON and SQLite idempotency. No false success on missing secret or failed simulation. Log/artifact retention for evidence. Keep master untouched until checks and review pass.


## Target boundary
The shared core is compiled for both the Blue Pill F103 simulation and the actual STM32L476RG MCU via PlatformIO nucleo_l476rg. The L476 target is deliberately a non-deployable compile/link probe of the common logic and report GPIO contract. It does not pretend to implement physical drivers, RTC, RF, or power management. The Blue Pill adapter is guarded against being built for the physical target. Task 02 onward replaces the probe with real target adapters incrementally. Report timing and thresholds live in design_config.hpp; the simulation clock belongs only to the adapter.
