# Báo cáo Day 13 Observability

## 1. Thông tin nhóm

- Tên nhóm:
- Repository URL:
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

- Kết quả `validate_dashboard.py`: `HỢP LỆ: 6/6 panel có trong dashboard contract.` (xem `submission/evidence/dashboard-validator.txt`).
- Evidence dashboard: `scripts/dashboard.py` (Mai Anh) render trực tiếp từ `data/logs.jsonl` theo `config/dashboard.yaml`. Ảnh/HTML baseline và 3 lần bật incident practice lưu tại `submission/evidence/dashboard-baseline.html`, `dashboard-alert1-rag_slow.html`, `dashboard-alert2-tool_fail.html`, `dashboard-alert3-cost_spike.html` (chi tiết xem `submission/evidence/README.md`).
- SLO đã chọn và lý do (`config/slo.yaml`, khớp threshold trong `config/dashboard.yaml`):
  - Latency P95 ≤ 3000ms — baseline đo được ~150ms; incident `rag_slow` đẩy P95 lên ~2651ms (~17.6x) nhưng vẫn dưới ngưỡng, chừa biên an toàn có chủ đích trước khi vi phạm SLO.
  - Error rate ≤ 2% — baseline 0%; incident `tool_fail` đẩy lên 50%, vượt xa ngưỡng, chứng minh SLO đủ nhạy để bắt lỗi downstream dependency.
  - Daily cost ≤ $2.5 — baseline avg $0.0023/request; incident `cost_spike` đẩy avg lên ~$0.0092/request (~4x).
  - Quality mean ≥ 0.75 — baseline đo được 0.88, còn biên an toàn ~0.13.
- Alert rules và runbook (`config/alert_rules.yaml`, `docs/alerts.md`):
  - `HighLatencyP95` (critical) — P95 > 3000ms trong 5 phút. Test bằng `rag_slow`: P95 tăng rõ rệt (151ms→2651ms) đúng hướng nhưng chưa chạm ngưỡng ở mức practice — biên an toàn có chủ đích, không phải lỗi cấu hình.
  - `ElevatedErrorRate` (critical) — error rate > 2% trong 5 phút. Test bằng `tool_fail`: error rate đạt 50%, vượt ngưỡng rõ ràng; log cho thấy correlation ID xuyên suốt `request_received → request_failed (RuntimeError: Vector store timeout) → http_error (500)`.
  - `CostSpike` (warning) — avg cost/request > 2x baseline trong 15 phút, hoặc tổng cost > $2.5/cửa sổ. Test bằng `cost_spike`: avg cost tăng ~4x, vượt ngưỡng.
  - Mỗi alert trong `docs/alerts.md` có đủ severity, SLI/SLO liên quan, điều kiện+duration, ảnh hưởng người dùng, 3 bước kiểm tra đầu tiên (metrics → trace → log), mitigation và owner.

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

| Thành viên | Phần việc | Commit/PR | Điều đã học |
|---|---|---|---|
| D (SRE & Alerts Engineer) | CP2: `config/slo.yaml` (4 SLI hiệu chỉnh từ baseline + incident practice thật), `config/alert_rules.yaml` (3 alert symptom-based), `docs/alerts.md` (runbook đầy đủ); fix regression regex `phone_vn` phát sinh khi merge nhánh `tea`; merge thủ công nhánh `manh` (Mai Anh) vào `main` — gỡ 4 conflict, giữ đúng phần việc từng vai trò, không để mất công correlation ID/PII/SLO đã hoàn thiện; test end-to-end cả 3 alert bằng `inject_incident.py` và lưu evidence | `ba07e02`, `727e94f`, `402cfbd` | Threshold alert cần có biên an toàn thật (đo từ incident thật, không đoán); merge nhánh dựa trên base cũ có thể vô tình revert code đã hoàn thiện nếu không kiểm tra kỹ trước khi merge — luôn `git diff base...branch` trước khi merge một nhánh lâu ngày không rebase. |
| | | | |
