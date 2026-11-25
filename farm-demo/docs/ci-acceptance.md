# CI policy — Task 05–17

Mục tiêu: không tiêu hao phút mô phỏng Wokwi trên commit phát triển. Không đổi tiêu chí nghiệp vụ hay bỏ bài kiểm thử. Hạn mức đã hết ở 4a138a2; đó là blocker dịch vụ, không phải PASS. Từ Task 05–16 phân biệt development PASS và hardware DEFERRED.

| Gate | Trigger | Checks |
| --- | --- | --- |
| Farm development acceptance | Push nhánh demo/farm-weather hoặc demo/farm-weather/**; manual/call | Workflow lint, inventory, toàn bộ native *_test.cpp, Python unittest, energy budget, hai firmware target, custom-chip compile và diagram lint không token |
| Farm hardware acceptance | Push ref demo/acceptance/task-17-* hoặc tag farm-demo-task17-*; manual với SHA xác nhận | Toàn bộ development gate, từng scenario Wokwi thực tế và verifier bắt buộc |
| Task 17 product acceptance | Hardware candidate + actual Wokwi wire replay + product evidence gate | Hardware gate xanh và end-to-end Edge/Cloud/UI, không suy diễn từ riêng hardware gate |

Không có trigger pull_request cho workflow demo: cùng commit nhánh nội bộ chỉ build một lần. Workflow license upstream vẫn chạy trên PR. Scope hiện tại là nhánh demo của repo; không cấu hình fork PR hay thay đổi branch protection. Không dùng continue-on-error, không cache kết quả PASS khác SHA, không giả lập log evidence. GitHub job summary ghi rõ DEFERRED.

Offline preflight chỉ compile chip WASM và lint bằng CLI v0.29.1 đã pin SHA256, không cung cấp WOKWI_CLI_TOKEN và không khởi chạy simulator. Hardware workflow tải artifact từ chính run của mình, kiểm tra commit.txt và SHA256 firmware/chip trước mô phỏng. Không gọi remote khi development thất bại. Matrix tự sinh từ ci/scenarios.json và kiểm tra đầy đủ mọi YAML trong thư mục scenarios, bao gồm sáu scenario hiện hành với verifier đã có. Khi thêm kịch bản Task 07/13/17, phải đăng ký vào registry, giữ kiểm tra đủ phạm vi, không bỏ scenario cũ. Các kiểm thử backend/frontend bổ sung vào development workflow khi các thành phần đó xuất hiện.

## Chỉ yêu cầu Wokwi tại mốc Task 17

1. Chốt commit ứng viên trên demo/farm-weather. Xác nhận development CI xanh, còn đủ Wokwi quota, và đã có các kịch bản E2E tương ứng.
2. Tạo một tag có chủ đích, ví dụ farm-demo-task17-v1, trỏ đúng SHA ứng viên và push tag. Có thể dùng nhánh acceptance riêng demo/acceptance/task-17-v1 với một lần push commit ứng viên. Không push nhánh/tag này trong quá trình phát triển thường ngày. Nếu workflow đã tồn tại trên default branch, có thể dùng Run workflow ở ref ứng viên và nhập SHA đầy đủ. Chỉ chọn một cách, không chạy cả tag và manual.
3. Kiểm tra Farm hardware acceptance và artifacts cho đúng candidate SHA. Mỗi scenario là một matrix job, chạy tuần tự (max-parallel 1) và độc lập. Không auto retry. Khi lỗi hạ tầng/quota, giữ gate đỏ và chỉ rerun failed jobs sau khi khắc phục; các scenario đã thành công của cùng run/SHA không phải chạy lại. Khi sửa mã nguồn, đó là candidate SHA mới: phải nghiệm thu lại các kiểm thử bắt buộc, không lấy artifact khác SHA.
4. Kiểm tra các gate E2E của Task 17 khi được triển khai. Chỉ tuyên bố hoàn thành khi tất cả gate bắt buộc xanh.

Nếu workflow chưa tồn tại ở default branch master, GitHub có thể không hiện nút Run workflow. Dùng trigger tag/acceptance branch đã định nghĩa thay vì merge vào master chỉ để có nút. Không tạo tag/nhánh nghiệm thu sớm.

## Evidence

native-evidence, firmware-bluepill_f103c8, firmware-nucleo_l476rg, wokwi-preflight. Simulation xuất artifact theo từng scenario và run attempt (simulation-<id>-attempt-<n>) cùng commit và checksum. Log từ simulation thật và JSON verifier là bằng chứng, không phải giá trị đo vật lý. Artifact lưu 30 ngày. Không đưa secret vào mã nguồn.

Số lần gọi remote của push phát triển: 0. Một đợt nghiệm thu đầy đủ hiện tại: 6 scenario (weather, invalid-ambient, local-alarms, power, link-recovery, remote-control). Số phút tính quota thực tế phụ thuộc Wokwi, chưa thể cam kết đủ quota cho Task 17; dự trù thêm các bài kiểm thử Task 06–16 và lần sửa lỗi. Không giảm tốc độ xác nhận hoặc thời gian cửa sổ nghiệp vụ chỉ để tiết kiệm phút.

Task 17: replay and product_gate are mandatory; see [handover](task-17-handover.md). The gate validates same-SHA artifacts, latest attempts, actual reports and browser evidence before generating the handover manifest.
