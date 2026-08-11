# OTEL + Langfuse Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hoàn thiện bài lab observability với OpenTelemetry cho telemetry chuẩn và Langfuse cho prompt/generation metadata, đồng thời tạo đủ evidence để nộp bài.

**Architecture:** OTEL quản lý request span, RAG span, LLM span và context propagation. OTLP đưa các span sang Jaeger local để xem waterfall; Langfuse vẫn là hệ thống bắt buộc cho LLM trace, prompt version, token, cost và quality. `correlation_id` là khóa nối chung trong JSONL log; dashboard chính vẫn đọc `data/logs.jsonl` theo contract hiện có.

**Tech Stack:** FastAPI, structlog, Langfuse 3.2.1, OpenTelemetry API/SDK + OTLP HTTP exporter, Jaeger all-in-one, Python 3.11+, YAML dashboard/SLO/alert config.

## Global Constraints

- Không sửa hoặc thay thế `config/challenge.json`.
- Không commit `.env`, Langfuse secret, OTLP credential, log chứa PII hoặc `.venv/`.
- Giữ đủ sáu dashboard panel theo `config/dashboard.yaml` và nguồn chuẩn `data/logs.jsonl`.
- Prompt fallback local phải tiếp tục hoạt động khi Langfuse không khả dụng.
- Mỗi thành viên có commit/PR và evidence riêng trong `submission/`.
- OTEL là phần mở rộng; không được làm hỏng Langfuse, logging, metrics hoặc public tests.
- Jaeger là backend local tùy chọn; Langfuse vẫn là backend bắt buộc cho prompt/generation evidence.
- Không gửi raw prompt, email, phone hoặc secret vào Jaeger span attributes/events.
- Railway phải nhận `PORT` do platform cấp; không hard-code port `8000` trong start command production.
- Không commit hoặc đưa `RAILWAY_TOKEN` vào runtime variables của app; token chỉ dùng cho CLI/deploy local.
- Không trỏ OTLP từ Railway tới `localhost:4318`; Railway cần OTLP endpoint public hoặc Jaeger service chạy cùng project.
- Filesystem Railway có thể ephemeral; log/dashboard production phải dùng volume hoặc backend ngoài nếu cần giữ dữ liệu sau redeploy.

## Ownership

- **A — Tuấn:** API & Middleware.
- **B — Trà:** PII Scrubbing và log security.
- **C — Mai Anh:** Metrics & Dashboard.
- **D — Đạt:** SLO, Alert và Runbook.
- **E — Mạnh:** QA/Chief Investigator, OTEL instrumentation và liên kết OTEL–Langfuse.

### Task 1: Baseline và môi trường dùng chung — cả nhóm

**Files:**
- Modify: `requirements.txt` chỉ khi dependency được xác nhận tương thích.
- Local only: `.env` dựa trên `.env.example`.
- Test: toàn bộ `tests/`.

- [x] Cài Python 3.11+, virtual environment và dependencies hiện tại.
- [ ] Điền Langfuse host/key trong `.env`; giữ file ngoài Git.
- [ ] Chạy `/health`, `python scripts/validate_dashboard.py` và ghi baseline vào evidence.
- [ ] Chạy `python -m pytest -q`; ghi rõ test nào bị chặn nếu môi trường thiếu dependency.

### Task 2: API & Middleware — A/Tuấn

**Files:**
- Modify: `app/middleware.py`, `app/main.py`.
- Test: `tests/test_chat_observability.py`, thêm `tests/test_middleware.py`.

**Interfaces:** Middleware phải đặt `request.state.correlation_id`; `chat()` phải bind `user_id_hash`, `session_id`, `feature`, `model`, `env` trước `request_received`.

- [ ] Viết test cho request ID hợp lệ, request ID được sinh dạng `req-xxxxxxxx`, response header và context isolation.
- [ ] Implement `clear_contextvars()` ở đầu request và `bind_contextvars(correlation_id=...)`.
- [ ] Thêm `x-request-id` và `x-response-time-ms` vào response.
- [ ] Thêm exception handler trả lỗi ổn định, ghi `request_failed` với `error_type` và payload đã scrub.
- [ ] Chạy các test API và tạo evidence correlation ID.

### Task 3: PII Scrubbing — B/Trà

**Files:**
- Modify: `app/logging_config.py`, `app/pii.py`.
- Test: `tests/test_pii.py`, `tests/test_validate_logs.py`.

- [ ] Đăng ký `scrub_event` trước `JsonlFileProcessor` và JSON renderer.
- [ ] Scrub đệ quy các string trong payload; giữ nguyên hash user ID và metadata không nhạy cảm.
- [ ] Kiểm thử email, số điện thoại Việt Nam, CCCD và thẻ tín dụng.
- [ ] Chạy load test với dữ liệu mẫu và lưu kết quả `validate_logs.py`.

### Task 4: Metrics và Dashboard — C/Mai Anh

**Files:**
- Modify: `app/metrics.py`, nếu cần `scripts/validate_dashboard.py`.
- Create: `scripts/render_dashboard.py` hoặc dashboard artifact tương đương.
- Test: `tests/test_metrics.py`, `tests/test_dashboard_validator.py`.

- [ ] Bảo đảm error rate được tính từ `request_failed / request_received`, không chỉ từ request thành công.
- [ ] Dùng `config/dashboard.yaml` làm contract bất biến.
- [ ] Dựng sáu panel runtime: latency, traffic, errors, cost, tokens, quality.
- [ ] Hiển thị time range 60 phút, refresh 30 giây, đơn vị và threshold.
- [ ] Chạy baseline và incident dataset; lưu ảnh dashboard và kết quả `6/6 panel`.

### Task 5: SLO, Alert và Runbook — D/Đạt

**Files:**
- Modify: `config/slo.yaml`, `config/alert_rules.yaml`, `docs/alerts.md`.
- Test: thêm kiểm tra YAML/config nếu cần.

- [ ] Chốt mục tiêu latency P95, error rate, daily cost và quality theo dashboard contract.
- [ ] Hoàn thiện ba symptom-based alerts với severity, condition, duration, owner và runbook link.
- [ ] Viết ba runbook có bước kiểm tra metric → trace → log và mitigation tạm thời.
- [ ] Bảo đảm không dùng tên implementation nội bộ làm điều kiện alert.
- [ ] Lưu evidence config và runbook vào report.

### Task 6: OTEL và Langfuse — E/Mạnh

**Files:**
- Create: `app/otel.py`.
- Modify: `app/main.py`, `app/mock_rag.py`, `app/mock_llm.py`, `app/agent.py`, `requirements.txt`, `.env.example`, `SETUP.md`.
- Test: create `tests/test_otel.py`, extend `tests/test_agent_prompt_trace.py`.

**Interfaces:** `app/otel.py` cung cấp `configure_telemetry()`, `get_tracer(name: str)` và `shutdown_telemetry()`. Span names thống nhất là `http.request`, `rag.retrieve`, `llm.generate`.

- [x] Thêm OTEL API/SDK và OTLP exporter tương thích với Langfuse 3.2.1; instrument route FastAPI thủ công để tránh duplicate span.
- [x] Mặc định dùng console exporter để chạy local; OTLP endpoint là cấu hình tùy chọn, không bắt buộc cho bài lab.
- [x] Gắn `correlation_id`, feature và incident state vào span attributes; không ghi raw message/email/phone.
- [x] Tạo child span riêng cho retrieval và LLM; không tạo span trùng với Langfuse `@observe` generation.
- [x] Ghi `otel_trace_id`, `otel_span_id` và `correlation_id` vào metadata Langfuse để truy vết chéo.
- [ ] Tạo prompt v1/v2, labels `baseline`, `candidate`, `production`, chạy ít nhất 10 traces và evidence rollback sau khi có project Langfuse dùng chung.
- [x] Kiểm tra fallback local vẫn chạy khi Langfuse tắt.

### Task 6A: Jaeger local qua OTLP — E/Mạnh

**Files:**
- Create: `docker-compose.jaeger.yml`.
- Modify: `.env.example`, `SETUP.md`, `app/otel.py`, `docs/grading-evidence.md`.
- Test: extend `tests/test_otel.py` with exporter configuration coverage.

**Interfaces:** Jaeger nhận OTLP HTTP tại `http://localhost:4318/v1/traces`; giao diện truy vấn trace mở tại `http://localhost:16686`; service name là `day13-observability-lab`.

- [ ] Tạo `docker-compose.jaeger.yml` với nội dung:

```yaml
services:
  jaeger:
    image: jaegertracing/all-in-one:1.57
    environment:
      COLLECTOR_OTLP_ENABLED: "true"
    ports:
      - "16686:16686"
      - "4317:4317"
      - "4318:4318"
```

- [ ] Khởi động backend bằng `docker compose -f docker-compose.jaeger.yml up -d` và kiểm tra container healthy.
- [x] Đặt `OTEL_TRACES_EXPORTER=otlp` và `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318/v1/traces` trong `.env` cá nhân; không commit `.env`.
- [x] Chạy API và load test; mở Jaeger, chọn service `day13-observability-lab`, kiểm tra trace có `http.request`, `rag.retrieve`, `llm.generate`.
- [x] Đối chiếu cùng request giữa Jaeger, Langfuse và `data/logs.jsonl` bằng `correlation_id`/trace metadata.
- [ ] Chụp ảnh Jaeger service, trace waterfall và span `rag.retrieve` chậm cho `submission/evidence/`.
- [ ] Dừng stack bằng `docker compose -f docker-compose.jaeger.yml down`; không dùng `down -v` khi còn cần dữ liệu.

Test cấu hình exporter bằng đoạn test sau trong `tests/test_otel.py`:

```python
def test_otlp_mode_is_accepted(monkeypatch):
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")
    assert otel.configure_telemetry() in {True, False}
```

### Task 7: Challenge và báo cáo — E dẫn dắt, cả nhóm cung cấp evidence

**Files:**
- Modify: `submission/REPORT.md`.
- Create: các artifact trong `submission/evidence/`.

- [x] Chạy `python scripts/inject_incident.py` và `python scripts/load_test.py --challenge --concurrency 5`.
- [ ] Xác định triệu chứng từ metrics, span bất thường từ OTEL/Langfuse và log cùng `correlation_id`.
- [ ] Ghi root cause, fix action và preventive measure; không chỉ kết luận từ source code.
- [ ] Mỗi thành viên điền phần việc, commit/PR và điều đã học trong report.
- [ ] Chụp evidence health, logs, PII redaction, traces, prompt labels, dashboard, alerts và challenge.

### Task 8: Verification và handoff — cả nhóm

- [x] Chạy `python -m pytest -q`.
- [ ] Chạy `python scripts/validate_logs.py` và đạt tối thiểu 80/100.
- [x] Chạy `python scripts/validate_dashboard.py` và thấy `6/6 panel`.
- [ ] Nếu dùng Jaeger, chạy `docker compose -f docker-compose.jaeger.yml ps` và lưu evidence trace waterfall.
- [ ] Kiểm tra `git status --short`, secret scan và không có PII trong Git.
- [ ] Demo theo thứ tự Metrics → Traces → Logs → Root cause → Fix.
- [ ] Commit theo từng owner rồi tạo commit tích hợp cuối cùng.

### Task 9: Deploy Railway — E/Mạnh, cả nhóm review

**Target:** Railway project `llmops-jaeger` (`63e999d9-1468-4db1-955f-3265bc4fb8aa`).

**Files:**
- Create: `railway.toml`.
- Modify: `SETUP.md`, `README.md`, `docs/grading-evidence.md`.
- Runtime only: Railway service variables; không ghi các giá trị secret vào repo.

**Interfaces:** Railway phải chạy `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, health check `GET /health`, và giữ các biến `LANGFUSE_*`, `OTEL_*`, `APP_*`, `LOG_*` trong Railway Variables.

- [x] Tạo `railway.toml` với cấu hình:

```toml
[build]
builder = "NIXPACKS"

[deploy]
startCommand = "uvicorn app.main:app --host 0.0.0.0 --port $PORT"
healthcheckPath = "/health"
healthcheckTimeout = 300
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

- [x] Kiểm tra Nixpacks nhận Python từ `requirements.txt` và không dùng `--env-file .env` trên Railway.
- [ ] Cấu hình Railway Variables từ `.env` bằng dashboard/CLI; tuyệt đối không commit `RAILWAY_TOKEN`, Langfuse secret hoặc OTLP headers.
- [x] Nếu dùng Jaeger, cung cấp OTLP endpoint public; nếu chỉ có Jaeger local thì đặt Railway `OTEL_TRACES_EXPORTER=none` hoặc `console` thay vì dùng `localhost`.
- [ ] Nếu cần giữ `data/logs.jsonl` sau redeploy, gắn Railway Volume vào `/app/data`; nếu không, coi log file production là dữ liệu tạm và dùng Langfuse/OTLP làm nguồn quan sát lâu dài.
- [x] Deploy staging/service riêng trước, gọi `/health`, `/metrics` và một request `/chat`; kiểm tra log startup có `tracing_enabled` và `otel_enabled` đúng.
- [x] Kiểm tra Railway public URL, response header/correlation ID sau khi Tuấn hoàn thiện middleware.
- [ ] Theo dõi error rate và P95 latency tối thiểu 15 phút; rollback về deployment trước nếu error rate vượt 2% hoặc P95 vượt 3000 ms liên tục.
- [x] Lưu URL Railway, deployment ID, health response và smoke-test response vào `submission/evidence/` (screenshot trace còn chờ Jaeger/Langfuse backend).

## Trade-off

OTEL tốt hơn cho tính mở, vendor-neutral và telemetry đa dịch vụ; Jaeger tốt cho việc lưu/truy vấn trace local; Langfuse tốt hơn cho prompt version, generation, token/cost và chất lượng LLM. Dùng cả ba giúp quan sát đầy đủ hơn nhưng tăng dependency, cần Docker và có nguy cơ duplicate span, nên chỉ một lớp được tạo mỗi span và `correlation_id` phải thống nhất.
