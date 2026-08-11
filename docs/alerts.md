# Template Alert và Runbook

Mỗi alert phải dựa trên triệu chứng người dùng hoặc SLO, không dựa trực tiếp vào tên implementation nội bộ.

Ngưỡng và số liệu tham chiếu trong runbook này lấy từ baseline đo thực tế
(`python scripts/load_test.py --concurrency 5`, n=40 request, xem
`submission/evidence/`) và ba kịch bản practice trong
`python scripts/inject_incident.py --scenario <rag_slow|tool_fail|cost_spike>`.

## Alert 1

- Tên: HighLatencyP95
- Severity: Critical
- SLI/SLO liên quan: `latency_p95_ms` — objective ≤ 3000ms (`config/slo.yaml`), panel Latency trong `config/dashboard.yaml`.
- Điều kiện và thời gian duy trì: P95 (cửa sổ trượt 5 phút) > 3000ms, duy trì liên tục 5 phút trước khi bắn alert (tránh flap do một request chậm đơn lẻ).
- Ảnh hưởng tới người dùng: Câu trả lời chậm rõ rệt, có thể timeout ở client hoặc gateway; trải nghiệm chat cảm giác "treo". Baseline đo được P95 ~150ms; incident practice `rag_slow` đẩy P95 lên ~2651ms (~17.6x) — nếu retrieval/dependency chậm hơn nữa sẽ vượt ngưỡng 3000ms và kích hoạt alert này.
- Ba bước kiểm tra đầu tiên:
  1. Mở panel Latency trên dashboard, xác định khoảng thời gian P95 vượt ngưỡng và có tăng liên tục hay chỉ một đợt ngắn.
  2. Mở một trace nằm trong khoảng đó trên Langfuse, so sánh thời lượng từng span (retrieval/RAG vs LLM call) để khoanh vùng span bất thường.
  3. Lấy correlation ID từ trace/response header, tìm log tương ứng trong `data/logs.jsonl` để xác nhận span chậm khớp với log nào (ví dụ log liên quan retrieval/tool call).
- Mitigation tạm thời: Bật lại timeout/circuit breaker ngắn hơn cho bước retrieval nếu có; tạm giảm concurrency hoặc feature bị ảnh hưởng; thông báo người dùng nếu kéo dài; theo dõi panel Latency sau khi áp dụng để xác nhận P95 hạ về dưới ngưỡng.
- Owner: D - SRE & Alerts Engineer (escalate sang phần RAG/tracing nếu root cause nằm ở sub-component RAG).

## Alert 2

- Tên: ElevatedErrorRate
- Severity: Critical
- SLI/SLO liên quan: `error_rate_pct` — objective ≤ 2% (`config/slo.yaml`), panel Errors trong `config/dashboard.yaml`.
- Điều kiện và thời gian duy trì: `error_rate_pct` (request_failed / request_received, cửa sổ trượt 5 phút) > 2%, duy trì liên tục 5 phút.
- Ảnh hưởng tới người dùng: Request trả lỗi (5xx) thay vì câu trả lời, người dùng phải thử lại hoặc mất tác vụ hoàn toàn. Incident practice `tool_fail` gây 100% request lỗi với `error_type=RuntimeError`, message "Vector store timeout" — cho thấy một dependency downstream fail có thể kéo error rate lên rất nhanh, vượt xa ngưỡng 2%.
- Ba bước kiểm tra đầu tiên:
  1. Mở panel Errors, xem error rate và breakdown theo `error_type` để biết lỗi tập trung vào loại nào (vd. RuntimeError, timeout, validation).
  2. Mở trace của một request lỗi trong khoảng thời gian đó, xác định span nào raise exception.
  3. Tìm log `request_failed` cùng correlation ID để đọc `detail`/message gốc của lỗi (vd. "Vector store timeout") và xác nhận đây là lỗi dependency hay lỗi logic nội bộ.
- Mitigation tạm thời: Nếu lỗi đến từ một dependency cụ thể (vd. vector store), cân nhắc retry có giới hạn hoặc fallback tạm thời (trả lời không kèm retrieval); nếu lỗi lan rộng, tạm dừng feature bị ảnh hưởng để tránh request thất bại hàng loạt; theo dõi panel Errors đến khi error rate về dưới 2%.
- Owner: D - SRE & Alerts Engineer (escalate sang chủ dependency liên quan nếu root cause ngoài phạm vi app).

## Alert 3

- Tên: CostSpike
- Severity: Warning
- SLI/SLO liên quan: `daily_cost_usd` — objective ≤ $2.5 (`config/slo.yaml`), panel Cost trong `config/dashboard.yaml`.
- Điều kiện và thời gian duy trì: avg cost/request (cửa sổ trượt 15 phút) > 2x avg cost/request baseline (24h) trong 15 phút liên tục, HOẶC tổng `cost_usd` trong cửa sổ dashboard (60 phút) > $2.5.
- Ảnh hưởng tới người dùng: Không ảnh hưởng trực tiếp latency/error, nhưng là rủi ro ngân sách — nếu không phát hiện sớm có thể vượt budget ngày mà không ai biết cho tới cuối kỳ. Baseline đo được avg $0.00215/request; incident practice `cost_spike` đẩy avg lên $0.00788/request (~3.7x baseline) trong khi latency/error không đổi — alert này là tín hiệu duy nhất phát hiện được sự cố loại này.
- Ba bước kiểm tra đầu tiên:
  1. Mở panel Cost và panel Tokens, xác nhận cost tăng có đi kèm tăng traffic/tokens tương ứng hay không (nếu traffic không đổi nhưng cost tăng → nghi ngờ thay đổi ở model/pricing/logic tính cost).
  2. Mở một trace gần đây, so sánh `tokens_in`/`tokens_out`/`cost_usd` với baseline để xem cost tăng do tokens tăng hay do đơn giá thay đổi.
  3. Tìm log `response_sent` cùng khoảng thời gian, đối chiếu `cost_usd` từng request để xác định request nào bất thường hoặc tăng đồng loạt.
- Mitigation tạm thời: Nếu do một feature/model cụ thể, tạm giới hạn hoặc chuyển về cấu hình rẻ hơn; nếu nghi ngờ lỗi tính cost, tạm dừng ghi nhận cost mới cho tới khi xác minh; báo cho chủ ngân sách trước khi tổng cost chạm ngưỡng $2.5 trong cửa sổ.
- Owner: D - SRE & Alerts Engineer (phối hợp Metrics & Dashboard để đối chiếu số liệu cost/token).
