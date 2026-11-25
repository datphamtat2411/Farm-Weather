# Farm Weather – Đề tài 12

MVP cho báo cáo trạm thời tiết mini. Mã nguồn tách khỏi ThingsBoard có sẵn.
- firmware: STM32F103C8 (Blue Pill) là target mô phỏng Wokwi, không phải STM32L476RG của thiết kế thực tế.
- Sáu biến trở mô phỏng dữ liệu BME280 (3 biến), gió (tần số xung tổng hợp), hướng gió và điện áp pin. Nút nhấn mô phỏng tiếp điểm gầu mưa có chống dội. Hai LED đại diện còi/relay yêu cầu ngừng tưới, không điều khiển bơm.
- Một giây logic = 100 ms mô phỏng. Cửa sổ, chu kỳ và ngưỡng giữ theo Chương 3–6. Timestamp cố định + thời gian logic là dữ liệu thử nghiệm.
- edge: SQLite outbox nhận JSON từ log; chống trùng theo device_id + sequence_number, phát hiện xung đột. Chưa triển khai RF/LoRaWAN, Protobuf, ACK từ STM32, Flash bền vững, đồng bộ ThingsBoard hay Dashboard.
- Chưa xác minh Stop 2, mức dòng điện, cảm biến BME280 thật hoặc an toàn điện bằng Wokwi.
- Các bước CI: native test, build PlatformIO, chạy Wokwi scenario và kiểm tra log (cần WOKWI_CLI_TOKEN), thử nhập bản ghi log vào SQLite.
- Không lưu token vào mã nguồn. Đặt WOKWI_CLI_TOKEN tại Settings > Secrets and variables > Actions.

## Source provenance

The farm-demo directory contains standalone project-specific firmware, tests and Edge code, not upstream ThingsBoard source. Do not stamp these files with a ThingsBoard copyright claim. The root ThingsBoard license and its restrictions remain unchanged; do not copy upstream code into this directory to evade the provenance check. Any additional licensing decision for standalone code belongs to the repository owner.

## Engineering rule
Select the best solution for the project. Avoid bare-minimum solutions or lazy shortcuts that only aim to get the task done with the least effort. Prefer verified, maintainable changes that preserve the report's architecture. Never disable a failing check merely to turn CI green; diagnose the failure and resolve its cause. Review scope and provenance explicitly. A task is done only after the full acceptance gate for its current commit passes.
