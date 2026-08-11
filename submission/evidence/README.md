# Evidence index — Role D (SRE & Alerts Engineer)

Tạo bằng `scripts/load_test.py` + `scripts/inject_incident.py` + `scripts/dashboard.py`
sau khi merge đầy đủ phần A (Trà), B (Tuấn Anh) và C (Mai Anh) vào `main`
(commit `402cfbd`). Server: `uvicorn app.main:app`. Dashboard: `python scripts/dashboard.py --port 8001`.

| File | Nội dung |
|---|---|
| `dashboard-validator.txt` | Kết quả `validate_dashboard.py` (Mai Anh) |
| `metrics-baseline.json` / `dashboard-baseline.html` | `/metrics` + dashboard HTML khi hệ thống bình thường (20 request, error 0%, P95 151ms, quality 0.88) |
| `metrics-alert1-rag_slow.json` / `dashboard-alert1-rag_slow.html` / `log-alert1-rag_slow-correlation.jsonl` | Bật `rag_slow`: P95 151ms → 2651ms (~17.6x). Log mẫu theo correlation_id `req-f7a97cae` cho thấy `request_received` → `response_sent` cùng ID. |
| `metrics-alert2-tool_fail.json` / `dashboard-alert2-tool_fail.html` / `log-alert2-tool_fail-correlation.jsonl` | Bật `tool_fail`: error_rate_pct 0% → 50% (vượt ngưỡng alert `ElevatedErrorRate` > 2%). Log mẫu `req-521e82b8` cho thấy chuỗi `request_received` → `request_failed` (`RuntimeError: Vector store timeout`) → `http_error` (500), cùng một correlation_id xuyên suốt. |
| `metrics-alert3-cost_spike.json` / `dashboard-alert3-cost_spike.html` / `log-alert3-cost_spike-correlation.jsonl` | Bật `cost_spike`: cost/request tăng từ ~$0.0023 lên ~$0.0092 (~4x). Log mẫu `req-bfc67b13`. |

Ghi chú: với mức độ incident practice (`rag_slow`), P95 tăng rõ rệt (đúng yêu cầu
[DASHBOARD_SETUP.md](../../docs/DASHBOARD_SETUP.md)) nhưng chưa vượt ngưỡng alert
3000ms — đây là biên an toàn có chủ đích đã ghi trong `config/slo.yaml`, không phải
lỗi. Alert `ElevatedErrorRate` và `CostSpike` đều vượt ngưỡng rõ ràng ở mức practice.

Sau khi test xong, đã tắt cả 3 incident (`/health` xác nhận `false`) và chạy lại
`validate_logs.py` (100/100, 56 correlation ID, 0 PII leak) + `validate_dashboard.py`
(6/6 panel hợp lệ) trên toàn bộ log của phiên làm việc.
