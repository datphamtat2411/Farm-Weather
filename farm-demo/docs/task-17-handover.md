# Task 17 — Hướng dẫn nghiệm thu và bàn giao

## 1. Nghiệm thu trên GitHub Cloud

Ứng viên là SHA chính xác trên nhánh demo/farm-weather. Kiểm tra workflow Farm weather development CI và workflow Farm weather hardware acceptance (Task 17). Để khởi chạy nghiệm thu dùng ref demo/acceptance/task-17-* theo chính sách CI; không cần clone local. Mỗi lần sửa mã nguồn phải nghiệm thu lại ứng viên mới. Wokwi Secret và quota đủ là điều kiện bắt buộc; không bỏ qua scenario lỗi.

Gate sản phẩm yêu cầu mọi job phát triển, sáu Wokwi scenario, verifier và bài replay Wokwi -> Edge -> ThingsBoard thật -> trình duyệt thành công. Tải artifact farm-demo-task17-handover-attempt-* để xem acceptance.md và acceptance.json (SHA, tên artifact, checksum). Mỗi artifact giữ riêng log hoặc dữ liệu gốc; không lấy minh chứng của SHA khác. Artifact không phải GitHub Release đã xuất bản hay máy chủ luôn hoạt động.

## 2. Kịch bản trình bày

| Mã | Thao tác / minh chứng | Nội dung báo cáo |
|---|---|---|
| D01 | weather, hardware.json, dashboard-operator.png; xem sáu nhóm đo và timestamp | Ch. 2, 3, 5 |
| D02 | local-alarms.log, kiểm tra còi/relay yêu cầu ngừng tưới, hysteresis, Alarm | Ch. 3, 5 |
| D03 | local-alarms.json, invalid-ambient.json; 18 xung = 5,0292 mm, không đếm dội | Ch. 3 |
| D04 | task16 demo-scenarios/lte-outage/report.html, thingsboard-report.json; 72 bản ghi định kỳ, phục hồi không lặp | Ch. 4 |
| D05 | link-recovery.log, task17-replay.json, task17-recovered-dashboard.png và CSV; log gửi lại, ACK và lịch sử thật | Ch. 4, 5 |
| D06 | power.json, energy-budget.json; chu kỳ 300/900/300 s và cảnh báo còn hoạt động | Ch. 3 |
| D07 | remote-control.json, security-report.json, dashboard-viewer.png; quyền, trạng thái lệnh và quá hạn | Ch. 5, 6 |

Bộ chạy Task 16 ở demo/run.py là công cụ trình diễn kịch bản tăng tốc, dùng logic native và Cloud SQLite độc lập. Wokwi acceptance dùng mô hình Blue Pill với chip custom và serial trace thực. Bài Task 17 replay chỉ sử dụng byte đã truyền thành công trong trace link-recovery; không sửa timestamp, không giả lập phản hồi REST. RPC hardware và Cloud RPC được kiểm thử tại các biên bổ sung, không tuyên bố đường truyền live Wokwi–Cloud. Không dùng số liệu mô phỏng để khẳng định độ chính xác khí tượng, RF, Stop 2, dòng tiêu thụ hay an toàn điện.

## 3. Triển khai trên máy chủ Cloud

GitHub Actions là môi trường tích hợp tạm thời và dừng services sau kiểm thử. Để trình diễn trực tiếp trên Internet, cần máy chủ Cloud riêng có Docker Compose, tên miền/chứng chỉ TLS, quyền sử dụng ThingsBoard 4.4.1 tương ứng mục đích. Không công khai tài khoản demo hay thông tin xác thực. Không dùng runner làm máy chủ dài hạn.

1. Từ source checkout trên Cloud ở đúng SHA đã nghiệm thu, cài dependency Python và generate Protobuf theo workflow. Dùng compose.yml và quy trình cài đặt, hoàn thành setup wizard ở [Task 10](task-10-thingsboard.md). CI dùng chế độ non-production và dữ liệu tạm.
2. Cấp phát topology, token Gateway, tài khoản và key riêng theo [Task 12](task-12-security.md). Tạo TLS tin cậy, không dùng CA tạm CI trên Internet. Cấu hình nginx example, giữ PostgreSQL và MQTT nội bộ riêng.
3. Chạy Edge Gateway, SerialBridge và sync_service theo [Task 08](task-08-edge.md), [Task 09](task-09-sync.md), [Task 13](task-13-rpc.md) với SQLite bền vững. Cấu hình API service và cài native dashboard theo [Task 14](task-14-dashboard.md). Cung cấp private secrets từ môi trường, không commit.
4. Truy cập dashboard bằng tài khoản operator/viewer được cấp. Kiểm tra HTTPS, telemetry, quyền đọc và lịch sử. Chỉ dùng môi trường cô lập để mô phỏng lỗi. Sao lưu dữ liệu và dừng dịch vụ khi kết thúc trình bày.

Việc cấp máy chủ, domain, giấy phép và kiểm thử truy cập public là điều kiện vận hành ngoài CI; chưa được xác nhận chỉ từ trạng thái gate. Không merge master hoặc công bố release nếu chưa có evidence cùng SHA.
