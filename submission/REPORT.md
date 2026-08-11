# Báo cáo Day 13 Observability

## 1. Thông tin nhóm

- Tên nhóm: MatMangRoiHUHU
- Repository URL: https://github.com/Dyo31122005/Day13-K4-Observability.git
- Commit SHA cuối:
- Thành viên và vai trò:

## 2. Kết quả kỹ thuật

- Điểm `validate_logs.py`:
- Tổng số traces:
- Số PII leak còn lại:
- Link/đường dẫn dashboard:

## 3. Logging và tracing

- Evidence correlation ID:
- Evidence PII redaction:
- Evidence trace waterfall:
- Giải thích một span đáng chú ý:

## 4. Prompt versioning

- Prompt name:
- Version/label baseline:
- Version/label candidate:
- Trace ID của mỗi version:
- Bằng chứng đổi label hoặc rollback:

## 5. Dashboard, SLO và alerts

- **CP1 — Metric runtime error rate.** `app/main.py` ghi nhận mọi `request_received` ngay khi request vào API; nhánh exception tiếp tục gọi `record_error(error_type)`. Vì vậy `/metrics` tính đúng `error_total / traffic * 100`, trả thêm `error_total`, `error_rate_pct` và `error_breakdown`, không dùng giá trị hard-code. `tests/test_metrics.py` bao phủ các trường hợp không có request (0.00%), 10 request/1 lỗi (10.00%) và breakdown theo loại lỗi.
- **CP2 — Dashboard runtime.** Dashboard tại `scripts/dashboard.py` đọc trực tiếp `data/logs.jsonl` và phục vụ ở `http://127.0.0.1:8001`. Dashboard giữ time range 60 phút, refresh 30 giây, và có đúng 6 panel: Latency, Traffic, Errors, Cost, Tokens, Quality. Mỗi panel hiển thị đơn vị cùng threshold/SLO line theo `config/dashboard.yaml`.
- **Nguồn và công thức Errors.** `error_rate_pct = count(request_failed) / count(request_received) * 100` trong cửa sổ 60 phút; panel Errors hiển thị thêm breakdown `error_type`. Threshold là `≤ 2%`.
- **SLO.** `config/slo.yaml` khai báo cửa sổ đo 28 ngày cho latency P95 `≤ 3000 ms`, error rate `≤ 2%`, daily cost `≤ USD 2.50`, và quality score trung bình `≥ 0.75`.
- **Alert và runbook.** `config/alert_rules.yaml` có 3 alert symptom-based: `HighLatencyP95`, `ElevatedErrorRate`, `CostSpike`; mỗi alert có severity, điều kiện và owner. Runbook tương ứng (SLI/SLO, thời gian duy trì, ảnh hưởng, kiểm tra ban đầu, mitigation, owner) nằm trong `docs/alerts.md`.
- **Xác minh.** `python scripts/validate_dashboard.py` cho kết quả `HỢP LỆ: 6/6 panel`; toàn bộ test: `24 passed`. Evidence gồm `submission/evidence/dashboard-validator.txt`, `submission/evidence/dashboard-baseline.png`, `submission/evidence/dashboard-rag-slow.png`, và `submission/evidence/langfuse-trace.png`.

### Kết quả practice `rag_slow` (2026-08-11)

Mỗi lần chạy dùng `python scripts/load_test.py --concurrency 5` với 10 request; tất cả đều trả HTTP 200 nên error rate của hai tập request là `0.00%`.

| Tập request | P50 từ `response_sent.latency_ms` | P95/P99 |   Total cost | Quality trung bình | Kết luận                                       |
| ----------- | --------------------------------: | ------: | -----------: | -----------------: | ---------------------------------------------- |
| Baseline    |                           1084 ms | 1174 ms | USD 0.020985 |               0.88 | Đạt SLO latency                                |
| `rag_slow`  |                           3597 ms | 3643 ms | USD 0.020100 |               0.88 | P95 tăng khoảng 2469 ms và vi phạm SLO 3000 ms |

- Metric → Trace → Log (practice): metric cần điều tra là P95 tăng từ 1174 ms lên 3643 ms. Mở trace chậm trên Langfuse, đối chiếu correlation ID của request, rồi tìm event `response_sent` cùng ID trong `data/logs.jsonl` để xác nhận `latency_ms`. Ảnh trace đã lưu tại `submission/evidence/langfuse-trace.png` (run ID `a5210fa25704aa293b0a3352c48e7015`).
- Latency in bởi `load_test.py` là end-to-end latency phía client; dashboard dùng `response_sent.latency_ms` theo contract, nên không dùng trực tiếp các số 6430 ms/18121 ms để tính P95 dashboard.
- Dashboard evidence runtime đã lưu tại `submission/evidence/dashboard-baseline.png` và `submission/evidence/dashboard-rag-slow.png`; incident `rag_slow` đã được disable sau khi kiểm tra.

## 6. Điều tra challenge

- Challenge ID:
- Triệu chứng từ metrics:
- Trace ID liên quan:
- Log line/correlation ID liên quan:
- Root cause:
- Fix action:
- Preventive measure:

## 7. Đóng góp cá nhân

Với mỗi thành viên, ghi rõ nhiệm vụ và link commit/PR tương ứng.

| Thành viên         | Phần việc                                                                                                                                       | Commit/PR             | Điều đã học                                                             |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- | --------------------- | ----------------------------------------------------------------------- |
| Trần Hoàng Mai Anh | CP1: metric `/metrics` cho error rate và test; CP2: dashboard 6 panel, SLO 28 ngày, 3 alert/runbook, baseline, practice `rag_slow` và evidence. | `<commit/PR của bạn>` | Tính error rate từ request events và dùng P95 để phát hiện tail latency |
