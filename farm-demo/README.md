# Farm Weather – Đề tài 12

MVP cho báo cáo trạm thời tiết mini. Mã nguồn tách khỏi ThingsBoard có sẵn.
- firmware: STM32F103C8 (Blue Pill) là target mô phỏng Wokwi, không phải STM32L476RG của thiết kế thực tế.
- BME280 custom chip mô phỏng thanh ghi I²C địa chỉ 0x76, đọc qua driver thanh ghi với I²C open-drain có timeout, ở forced mode. Anemometer custom chip tạo xung cạnh xuống thật tại PB0. Biến trở mô phỏng ADC hướng gió và điện áp pin. Nút nhấn mô phỏng gầu mưa. Hai LED thể hiện tín hiệu còi/relay yêu cầu ngừng tưới, không điều khiển tải thật.
- Một giây logic = 100 ms mô phỏng. Cửa sổ, chu kỳ và ngưỡng giữ theo Chương 3–6. Timestamp cố định + thời gian logic là dữ liệu thử nghiệm.
- edge: SQLite outbox nhận JSON từ log (legacy smoke); bộ giải mã Protobuf v1 độc lập; chống trùng theo device_id + sequence_number, phát hiện xung đột. Chưa triển khai RF/LoRaWAN, ACK từ STM32, Flash bền vững, đồng bộ ThingsBoard hay Dashboard.
- Chưa xác minh Stop 2, mức dòng điện, cảm biến BME280 thật hoặc an toàn điện bằng Wokwi.
- Development CI: native test, build hai target PlatformIO, compile custom chip và lint Wokwi offline, không tiêu hao phút mô phỏng. Hardware acceptance chỉ chạy khi yêu cầu nghiệm thu rõ ràng (Task 17).
- Không lưu token vào mã nguồn. Đặt WOKWI_CLI_TOKEN tại Settings > Secrets and variables > Actions.

## Source provenance

The farm-demo directory contains standalone project-specific firmware, tests and Edge code, not upstream ThingsBoard source. Do not stamp these files with a ThingsBoard copyright claim. The root ThingsBoard license and its restrictions remain unchanged; do not copy upstream code into this directory to evade the provenance check. Any additional licensing decision for standalone code belongs to the repository owner.

## Engineering rule
Select the best solution for the project. Avoid bare-minimum solutions or lazy shortcuts that only aim to get the task done with the least effort. Prefer verified, maintainable changes that preserve the report's architecture. Never disable a failing check merely to turn CI green; diagnose the failure and resolve its cause. Review scope and provenance explicitly. A task is fully accepted only when its required evidence and acceptance gates pass for the candidate commit. During Tasks 05–16, development checks may pass while hardware acceptance remains DEFERRED. Do not report hardware PASS without actual evidence.

See [Task 02 hardware model](docs/task-02-hardware.md) for controls and evidence.

## Task 03 — sensor acquisition

The common sensor-processing core implements report Ch.3 table 3.5/3.6: pulse debounce, wind/rain windows, BME280 range/Median-3/fault recovery, ADC Median-5, SEN-15901 resistive direction lookup/vector average, and valid_mask-aware telemetry. See [engineering notes](docs/task-03-acquisition.md). The simulator uses 10x logical time; a physical STM32L476RG build is a non-deployable compile probe only. Separate GitHub Actions evidence covers real Wokwi invalid BME280 readings.

## Task 04 — local alarms

See [local alarm contract and evidence](docs/task-04-local-alarms.md). Local actuator state and event records do not depend on network availability. The simulator includes a diagnosed wind-failure input and inspection acknowledgment; it never starts a pump.

## Task 05 — power management

See [power schedule and engineering budget](docs/task-05-power.md). The logical sleep/wake model and actual ADC-driven battery policy preserve sampling and local alarms across 300/900-second report periods. No physical current or Stop 2 measurement is claimed.

## CI policy — preserve Wokwi quota

See [CI and acceptance policy](docs/ci-acceptance.md). Ordinary pushes to `demo/farm-weather` run only development checks. All Wokwi simulations remain mandatory for hardware acceptance on the Task 17 candidate. Existing upstream license checks remain unchanged.

## Task 06 — protocol

See [wire protocol and evidence](docs/task-06-protocol.md). Firmware encodes actual measurements into FPort 10/11 Protobuf, serial hex is the simulation adapter. Edge decodes using protoc-generated Python bindings. The legacy SQLite smoke remains until Task 08; no RF or end-to-end delivery claim.
