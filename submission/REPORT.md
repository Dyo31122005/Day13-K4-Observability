# Báo cáo Day 13 Observability

## 1. Thông tin nhóm

- Tên nhóm:
- Repository URL:
- Commit SHA cuối:
- Thành viên và vai trò:
  - Hà Anh Tuấn — Thành viên A: API & Middleware; Correlation ID, exception handler và request context.
  - Nguyễn Hương Trà — Thành viên B: Security Engineer; PII scrubbing, regex patterns và kiểm chứng log.
  - Trần Hoàng Mai Anh — Thành viên C: Metrics & Dashboard; error rate và dashboard sáu nhóm chỉ số.
  - Nguyễn Minh Đạt — Thành viên D: SRE & Alerts Engineer; SLO, alert rules và alert runbook.
  - Nguyễn Hùng Mạnh — Thành viên E: QA & Chief Investigator; OTEL instrumentation, liên kết OTEL–Langfuse, load test, challenge và tổng hợp báo cáo.

## 2. Kết quả kỹ thuật

- Điểm `validate_logs.py`: `100/100` ở bộ evidence sau merge (xem `submission/evidence/README.md`); log smoke test mới không phát hiện PII.
- Tổng số traces: challenge + load test tạo tối thiểu 15 request traces; smoke test Railway đã xác nhận trace Langfuse production `3344b7c5d4949b04627fcffdcb5e82f9`.
- Số PII leak còn lại: `0` trong các evidence alert/challenge; scrubber áp dụng đệ quy cho payload/list.
- Link/đường dẫn dashboard: `scripts/dashboard.py`, contract `config/dashboard.yaml`, evidence `submission/evidence/dashboard-*.html`.

## 3. Logging và tracing

- Evidence correlation ID: `submission/evidence/challenge-rag_slow-2026-08-11.txt` và `railway-deployment.txt`.
- Evidence PII redaction: `submission/evidence/log-alert*-correlation.jsonl`; các email/điện thoại/thẻ được thay bằng `[REDACTED_*]`.
- Evidence trace waterfall: `submission/evidence/jaeger-otel-smoke.txt` ghi trace `35b57e8388ace0ac45ca899751cfa8ec` với `http.request`, Langfuse bridge, `rag.retrieve`, `llm.generate`.
- Giải thích một span đáng chú ý: `rag.retrieve` mang `incident.rag_slow=true`, `rag.document_count` và thời gian tăng rõ trong challenge; không ghi raw message/PII.

## 4. Prompt versioning

- Prompt name: `day13-chat` (cấu hình qua `LANGFUSE_PROMPT_NAME`).
- Version/label baseline: `production`; trace production đã được ghi thành công trên Langfuse.
- Version/label candidate: chưa tạo trong repo; cần thao tác trên project Langfuse chung.
- Trace ID của mỗi version: trace smoke test label `production` là `3344b7c5d4949b04627fcffdcb5e82f9`; candidate cần tạo thêm trên Langfuse.
- Bằng chứng đổi label hoặc rollback: bổ sung sau khi tạo v1/v2 trên Langfuse; app đã sẵn metadata `prompt_name`, `prompt_label`, `prompt_version`.

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

- Challenge ID: `day13-k4-observability-v1` (`rag_slow`).
- Triệu chứng từ metrics: P95 `2737ms`, tăng mạnh so với baseline ~`150ms`, error rate `0%`.
- Trace ID liên quan: OTEL request/child spans được liên kết bằng correlation ID; trace backend cần bổ sung ID khi bật Jaeger/Langfuse.
- Log line/correlation ID liên quan: `req-4ea8f5a2`, `req-388a7e08`, `req-f06309e5`, `req-77c77433`, `req-84e3f74e`.
- Root cause: practice incident bật `STATE["rag_slow"]`, làm chậm `rag.retrieve` trước khi LLM chạy.
- Fix action: tắt incident bằng `python scripts/inject_incident.py --disable`; xác nhận `/health` cả ba incident đều `false`.
- Preventive measure: alert theo latency P95, điều tra theo luồng metrics → OTEL/Langfuse trace → JSON log và giữ threshold/runbook trong `config/`/`docs/alerts.md`.

## 7. Đóng góp cá nhân

Với mỗi thành viên, ghi rõ nhiệm vụ và link commit/PR tương ứng.

| Thành viên | Phần việc | Commit/PR | Điều đã học |
|---|---|---|---|
| Hà Anh Tuấn (A) | CP1 API & Middleware: correlation ID, response headers, request context và exception handler. | `9f36aaa`, `402cfbd` | Context phải được bind trước log đầu tiên và phản hồi lỗi vẫn cần correlation ID. |
| Nguyễn Hương Trà (B) | CP1 PII scrubbing: regex email/phone/CCCD/card, recursive sanitization và log validation. | `028f349`, `ba07e02` | Redaction phải chạy trước file renderer và bao phủ cả payload lồng nhau. |
| Trần Hoàng Mai Anh (C) | CP1/CP2 metrics error rate và dashboard sáu panel từ `data/logs.jsonl`. | `e38d80e`, `402cfbd` | Error rate phải lấy trên toàn request nhận được, không chỉ request thành công. |
| Nguyễn Minh Đạt (D) | CP2: `config/slo.yaml` (4 SLI hiệu chỉnh từ baseline + incident practice thật), `config/alert_rules.yaml` (3 alert symptom-based), `docs/alerts.md` (runbook đầy đủ); fix regression regex `phone_vn` phát sinh khi merge nhánh `tea`; merge thủ công nhánh `manh` (Mai Anh) vào `main` — gỡ 4 conflict, giữ đúng phần việc từng vai trò, không để mất công correlation ID/PII/SLO đã hoàn thiện; test end-to-end cả 3 alert bằng `inject_incident.py` và lưu evidence | `ba07e02`, `727e94f`, `402cfbd` | Threshold alert cần có biên an toàn thật (đo từ incident thật, không đoán); merge nhánh dựa trên base cũ có thể vô tình revert code đã hoàn thiện nếu không kiểm tra kỹ trước khi merge — luôn `git diff base...branch` trước khi merge một nhánh lâu ngày không rebase. |
| Nguyễn Hùng Mạnh (E) | Hợp nhất OTEL request/RAG/LLM spans, liên kết correlation ID với Langfuse metadata, bổ sung Jaeger Compose/Railway config, chạy load test/challenge và smoke test Railway. | deployment `b87df5d5-fa7a-4ae0-83a1-b4263e64a464` | OTEL giữ vendor-neutral trace context; secret Langfuse phải cấu hình ngoài repo; metrics → trace → log giúp khoanh vùng `rag_slow`. |
| | | | |
