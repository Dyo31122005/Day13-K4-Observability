# Báo cáo Day 13 Observability

## 1. Thông tin nhóm

- Tên nhóm: MatMangRoiHUHU
- Repository URL: https://github.com/Dyo31122005/Day13-K4-Observability
- Commit SHA cuối: `2b8061b6ebfbb169991add7ec773aa2f1e94fea0`
- Thành viên và vai trò:
  - Hà Anh Tuấn — Thành viên A: API & Middleware; Correlation ID, exception handler và request context.
  - Nguyễn Hương Trà — Thành viên B: Security Engineer; PII scrubbing, regex patterns và kiểm chứng log.
  - Trần Hoàng Mai Anh — Thành viên C: Metrics & Dashboard; error rate và dashboard sáu nhóm chỉ số.
  - Nguyễn Minh Đạt — Thành viên D: SRE & Alerts Engineer; SLO, alert rules và alert runbook.
  - Nguyễn Hùng Mạnh — Thành viên E: QA & Chief Investigator; OTEL instrumentation, liên kết OTEL–Langfuse, load test, challenge và tổng hợp báo cáo.

  (Xác nhận A=Tuấn Anh / B=Trà bởi thành viên D ngày 2026-08-11; khớp với tác giả
  Git thật của các commit liên quan: `9f36aaa` "role_A_5_nguoi" — correlation
  ID/middleware — tác giả account `tuanha122004`; `028f349` — mở rộng PII regex —
  tác giả account `teahtn72`.)

## 2. Kết quả kỹ thuật

- Điểm `validate_logs.py`: 100/100 (log cục bộ hiện tại: 21 record, 10 correlation ID duy nhất, 0 PII leak — xem `submission/evidence/validate-logs-final.txt`). Log smoke test challenge/Railway của Mạnh cũng không phát hiện PII (`submission/evidence/README.md`).
- Tổng số traces: **12 trace thật** trên Langfuse tính đến thời điểm merge (project `My Project`, id `cmso0kyzy000jad0i5gnv9daa`, host `https://jp.cloud.langfuse.com`) — gồm 10 trace từ load test của D (`submission/evidence/langfuse-traces-list.json`) + smoke test Railway/Jaeger của Mạnh (trace `3344b7c5d4949b04627fcffdcb5e82f9`, `35b57e8388ace0ac45ca899751cfa8ec`).
- Số PII leak còn lại: 0 trong mọi evidence (baseline, 3 alert, challenge, Railway smoke) — scrubber áp dụng đệ quy cho payload/list.
- Link/đường dẫn dashboard: `scripts/dashboard.py --port 8001` (local, đọc `data/logs.jsonl` theo contract `config/dashboard.yaml`) hoặc `scripts/dashboard.html` (bản export tĩnh của Mạnh). Evidence: `submission/evidence/dashboard-*.html`.

## 3. Logging và tracing

- Evidence correlation ID: đối chiếu trực tiếp được giữa 3 lớp — log JSON, Langfuse trace và OTEL/Jaeger span đều dùng chung một correlation ID. Ví dụ `correlation_id=req-6f78fc62` xuất hiện cả trong `data/logs.jsonl` (`request_received` → `response_sent`) lẫn metadata trace Langfuse `a5210fa25704aa293b0a3352c48e7015` (`submission/evidence/langfuse-traces-list.json`); phía challenge chính thức xem thêm `submission/evidence/challenge-rag_slow-2026-08-11.txt` và `railway-deployment.txt`.
- Evidence PII redaction: `submission/evidence/pii-redaction-sample.jsonl` (email/phone/credit card test → `[REDACTED_*]`) và `submission/evidence/log-alert*-correlation.jsonl`.
- Evidence trace waterfall: `submission/evidence/jaeger-otel-smoke.txt` — trace `35b57e8388ace0ac45ca899751cfa8ec` với span `http.request` → `rag.retrieve` → `llm.generate`, bridge sang Langfuse. Chưa có screenshot Langfuse UI (không có công cụ chụp màn hình trong phiên làm việc); link để tự mở và chụp: `https://jp.cloud.langfuse.com/project/cmso0kyzy000jad0i5gnv9daa/traces/a5210fa25704aa293b0a3352c48e7015`.
- Giải thích một span đáng chú ý: span `rag.retrieve` (Jaeger/OTEL) mang `incident.rag_slow=true` và `rag.document_count`, thời gian tăng rõ khi bật incident — không ghi raw message/PII. Ở lớp Langfuse, generation span của trace `a5210fa25704aa293b0a3352c48e7015` có metadata `prompt_source=local-fallback`, `prompt_fetch_error=LangfuseFallback` (xem mục 4) — cho thấy app rơi vào nhánh fail-safe khi prompt managed không tồn tại, đúng thiết kế trong `app/prompt_management.py`.

## 4. Prompt versioning

**Hoàn thành** — tạo bằng `client.create_prompt(...)` và `client.api.prompt_version.update(...)` (Langfuse Python SDK), toàn bộ 4 bước verify bằng trace thật (`submission/evidence/prompt-versioning.json`), không dùng lại trace `local-fallback` cũ nào làm bằng chứng.

- Prompt name: `day13-chat`, type `text`, giữ đúng 3 biến bắt buộc `{{feature}}`, `{{docs}}`, `{{message}}`.
- Version/label baseline: **v1**, label `baseline` + `production` — nội dung `Feature={{feature}}\nDocs={{docs}}\nQuestion={{message}}`.
- Version/label candidate: **v2**, label `candidate` — thêm 1 dòng format: `Answer in at most 3 concise sentences.` (chỉ đổi format/độ dài câu trả lời như hướng dẫn, không đổi 3 biến).
- Trace ID của mỗi version (đều `prompt_source=langfuse`, tức fetch managed prompt thành công, không fallback):
  - `req-promptv1` (label `baseline`, version 1) → trace `49c8660313542f2db957b9353a2c8203`
  - `req-promptv2` (label `candidate`, version 2) → trace `73e0b45c9630fc69a9d26decf27c4d3a`
- Bằng chứng đổi label hoặc rollback: chuyển `production` sang v2 bằng `prompt_version.update(version=2, new_labels=['candidate','production'])` → v1 tự động mất label `production` (Langfuse đảm bảo 1 label = 1 version). Request `req-prodv2` (label `production`) → trace `beb5c47a862ec664a28dc2810663d9b4` xác nhận nhận **version 2**. Sau đó rollback bằng `prompt_version.update(version=1, new_labels=['baseline','production'])`; request `req-rollback01` (label `production`) → trace `f6442b6498fbde9010cebe23127917fd` xác nhận `production` đã **quay lại version 1**. Chi tiết đầy đủ 4 trace + nội dung 2 version: `submission/evidence/prompt-versioning.json`.

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
