# Template Alert và Runbook

Mỗi alert phải dựa trên triệu chứng người dùng hoặc SLO, không dựa trực tiếp vào tên implementation nội bộ.

## Alert 1

- Tên: high-error-rate
- Severity: critical
- SLI/SLO liên quan: `error_rate_pct ≤ 2%`.
- Điều kiện và thời gian duy trì: error rate lớn hơn 2% trong 5 phút liên tục.
- Ảnh hưởng tới người dùng: request thất bại hoặc nhận phản hồi 5xx.
- Ba bước kiểm tra đầu tiên: kiểm tra panel Errors và breakdown; mở trace thất bại gần nhất; dùng correlation ID của trace để tìm log `request_failed`.
- Mitigation tạm thời: tắt hoặc rollback tính năng/dependency gây lỗi, sau đó retry các request an toàn.
- Owner: on-call-api.

## Alert 2

- Tên: high-p95-latency
- Severity: warning
- SLI/SLO liên quan: `latency_p95_ms ≤ 3000`.
- Điều kiện và thời gian duy trì: P95 lớn hơn 3000 ms trong 10 phút liên tục.
- Ảnh hưởng tới người dùng: nhóm chậm nhất của người dùng nhận phản hồi chậm.
- Ba bước kiểm tra đầu tiên: so sánh P50/P95/P99 trên panel Latency; mở trace chậm nhất; tìm log cùng correlation ID để xác định span hoặc dependency chậm.
- Mitigation tạm thời: giảm tải bằng rate limit hoặc fallback, sau đó tắt feature/retrieval gây chậm nếu có bằng chứng.
- Owner: on-call-api.

## Alert 3

- Tên: low-quality-proxy
- Severity: warning
- SLI/SLO liên quan: `quality_score_avg ≥ 0.75`.
- Điều kiện và thời gian duy trì: quality proxy trung bình thấp hơn 0.75 trong 15 phút liên tục.
- Ảnh hưởng tới người dùng: câu trả lời có thể không đủ ngữ cảnh hoặc không hữu ích.
- Ba bước kiểm tra đầu tiên: xem panel Quality và traffic; đối chiếu trace có score thấp; tìm log cùng correlation ID để kiểm tra feature/prompt context.
- Mitigation tạm thời: rollback prompt label đã thay đổi gần nhất hoặc route sang prompt fallback đã kiểm chứng.
- Owner: ai-platform.
