# Task 02 — Wokwi hardware model
Report Ch.3 tables 3.1–3.3, Ch.6 D01–D03. No changes to design thresholds. Blue Pill is a simulator target, not STM32L476RG deployed hardware.
| Input/output | Wokwi model | MCU simulation pins | Report physical pins |
|---|---|---|---|
| BME280 | custom register-level I2C model; synthetic calibration, forced mode | PB6 SCL, PB7 SDA | PB8, PB9 |
| Wind speed | custom contact pulse generator, 0–30 logical Hz | PB0 EXTI | PB0 |
| Rain | push button, active-low contact | PB1 EXTI | PB1 |
| Direction | adjustable ADC proxy | PA0 | PA0 |
| Battery | adjustable ADC proxy | PA1 | PA1 |
| Buzzer / stop request | red/yellow logic indicators | PB8/PB9 | PA9/PA8 |
| Serial observation | UART 115200 | PA9/PA10 | debug only |
The BME280 chip exposes temperature, humidity and pressure controls, generates raw registers and synthetic calibration; firmware reads chip ID and calibration, triggers forced measurements and compensates raw data through bounded open-drain software I2C transactions. This simulation adapter avoids an initialization hang observed with the F103 hardware Wire peripheral; the physical target retains its hardware I2C pin contract. The wind chip emits independent edges (not counters injected into the station). The clock is accelerated tenfold; edge debounce intervals scale consistently. The ADC proxies are intentionally simplified until Task 03 calibration and validity. LED indicators test GPIO behavior, not MOSFET/PC817 isolation or actual 12 V relay operation. RF, energy and flash persistence are later tasks.
CI compiles the WASM sources using the pinned Wokwi CLI, lints the diagram, runs the original alarm/rain smoke with pin assertions and new sensor controls, verifies measured values in hardware.json and preserves the existing SQLite replay checks. Artifacts are evidence of simulated I/O only.
