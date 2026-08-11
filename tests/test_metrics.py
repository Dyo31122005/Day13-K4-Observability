from app import metrics


def setup_function() -> None:
    metrics.REQUEST_LATENCIES.clear()
    metrics.REQUEST_COSTS.clear()
    metrics.REQUEST_TOKENS_IN.clear()
    metrics.REQUEST_TOKENS_OUT.clear()
    metrics.ERRORS.clear()
    metrics.QUALITY_SCORES.clear()
    metrics.TRAFFIC = 0


def test_percentile_basic() -> None:
    assert metrics.percentile([100, 200, 300, 400], 50) >= 100


def test_snapshot_reports_zero_error_rate_without_requests() -> None:
    assert metrics.snapshot()["error_rate_pct"] == 0.0
    assert metrics.snapshot()["error_total"] == 0


def test_error_rate_uses_all_received_requests_and_keeps_breakdown() -> None:
    for _ in range(10):
        metrics.record_received()
    metrics.record_error("TimeoutError")

    snapshot = metrics.snapshot()

    assert snapshot["traffic"] == 10
    assert snapshot["error_total"] == 1
    assert snapshot["error_rate_pct"] == 10.0
    assert snapshot["error_breakdown"] == {"TimeoutError": 1}
