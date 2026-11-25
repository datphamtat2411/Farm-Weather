# Task 03 — Thu thập, lọc số và chất lượng dữ liệu

## Contract
Bám Chương 3, mục 3.3, bảng 3.5/3.6 và Chương 6 D01–D03.
- BME280: 60 s, forced-mode hiện có, kiểm tra từng nhóm -40..85 °C, 0..100 %RH, 300..1100 hPa; Median-3 trên ba phép đo hợp lệ gần nhất. Kiểm tra thất bại làm nhóm không hợp lệ ngay trong kỳ hiện hành, không biến giá trị lỗi thành 0 hay giữ giá trị cũ như dữ liệu mới. Sau ba lần lỗi liên tiếp bật SENSOR, xóa sau ba lần hợp lệ. Cảnh báo nhiệt dùng dữ liệu thô hợp lệ theo phút, không dùng giá trị đã lọc vì median gây trễ.
- Hướng gió: ADC 12 bit, Median-5, R=10000*ADC/(4095-ADC); bảng điện trở 8 hướng đáng tin cậy của SEN-15901, ±12% với kiểm tra không nhập nhằng. Tính trung bình hướng bằng vector (sin/cos), loại dữ liệu khi tổng vector chuẩn hóa dưới 0.2, hoặc khi lặng gió. Mỗi mẫu hướng 5 giây. Ranh giới Bắc 359°/1° tính ra gần 0°. Nếu không nhận diện điện trở, clear bit direction trong valid_mask.
- Gió: ngắt cạnh xuống với chống dội 5 ms theo thời gian logic, gom xung mỗi giây, trung bình trong kỳ và cực đại 3 giây trượt; hệ số 1 Hz ~ 0.667 m/s. Không lọc mượt những cơn gió giật làm mất tín hiệu cảnh báo.
- Mưa: ngắt cạnh xuống chống dội 50 ms, 0.2794 mm/xung. Không dùng bộ lọc trung bình lên tổng lượng mưa; cửa sổ trượt 600 giây cho cảnh báo, kỳ 300 giây cho bản ghi.
- Pin: Median-5 ADC, cầu phân áp 100k/100k và API chuyển đổi với Vref đo/hiệu chỉnh (mV), kiểm tra 2.0..3.65V. Trong Wokwi sử dụng Vref giả lập 3300 mV. VREFINT thực và nguồn/Stop 2 của board vật lý chưa được đo ở task này.
- Telemetry: JSON cho serial demo giữ cấu trúc hiện hành, valid_mask gồm 7 nhóm. Thiếu phép đo hợp lệ => không xuất trường đo đó trong kỳ. Đầu ra vẫn có period_s, timestamp, sequence, trạng thái. Tên khóa và ngưỡng giữ theo báo cáo. Adapter Wokwi dùng thời gian logic 10x, không dùng kết quả đo vật lý.

## Evidence / testing
- Native unit test độc lập: Median-5/3, dữ liệu trôi và outlier, invalid/recovery, tám giá trị điện trở, hở mạch/ADC sai, vector qua Bắc, hướng đối nhau, ADC/Vref, xung 5/50 ms, chu kỳ, cửa sổ gió/mưa, giá trị invalid không kích hoạt heat.
- Wokwi: driver BME280 qua I²C, xung gió qua interrupt, nút mưa, ADC direction và pin đi xuyên pipeline, pin alarm/relay được xác nhận trong smoke.
- Scenario riêng cho BME280 sai miền đo, phải có valid_mask loại nhiệt độ, status SENSOR. Kết quả được kiểm tra bằng file serial xuất từ Wokwi và script validation.
- Artifacts GitHub Actions: log native, serial, JSON Wokwi smoke, báo cáo negative scenario, toolchain và SHA.
- Task 03 **không** triển khai LoRaWAN, Queue Flash, RPC, Cloud, cảnh báo mới hay một ứng dụng khác; những việc này thuộc các task kế tiếp.

Nguồn bảng hướng: SparkFun Weather Meter Kit SEN-15901 resistor ladder (8 cardinal positions). Các giá trị ADC trong Wokwi là proxy của mạch 10k pull-up, không phải biến trở tuyến tính thành góc. https://www.sparkfun.com/weather-meter-kit.html
