# AIOps Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Day 13 observability lab's declarative alert rules and unbounded in-memory metrics into a working AIOps loop — persistent rolling-window metrics, a real alert evaluation engine, statistical anomaly detection, one auto-remediation action, a Prometheus export, and a local Grafana dashboard.

**Architecture:** A SQLite-backed metrics store (`app/metrics_store.py`) is the new source of truth for rolling windows and baselines. An `asyncio` background task inside the existing FastAPI app (`app/alerting.py`) polls that store every 15s, evaluates rules loaded from an extended `config/alert_rules.yaml`, logs `alert_fired`/`alert_resolved`, and — for `CostSpike` only — calls a remediation handler that caps output tokens in `app/mock_llm.py`. A new `GET /metrics/prometheus` route exports the same rolling window in Prometheus text format; a local Docker Compose stack (Prometheus + Grafana) scrapes it.

**Tech Stack:** Python 3.12, FastAPI, stdlib `sqlite3`, `prometheus_client`, Docker Compose, Grafana/Prometheus official images.

## Global Constraints

- Everything is additive: `GET /metrics` (JSON) keeps its exact current shape; `config/dashboard.yaml`, `scripts/validate_dashboard.py`, and the 30 existing tests in `tests/` must be unaffected and stay green.
- `config/alert_rules.yaml` keeps every existing prose field (`condition`, `severity`, `type`, `owner`, `runbook`) byte-identical; new fields are added alongside.
- Auto-remediation is scoped to `CostSpike` only (cap output tokens). `HighLatencyP95` and `ElevatedErrorRate` are evaluated and logged but take no automated action.
- Alert delivery is structured logs only (`alert_fired` / `alert_resolved` / `remediation_applied` / `remediation_cleared`) — no Slack/webhook.
- Anomaly detection is statistical (mean + z-score·stddev), not ML.
- Grafana/Prometheus run locally via Docker Compose only, not deployed to Railway.
- Design reference: `docs/superpowers/specs/2026-08-11-aiops-upgrade-design.md`.

---

### Task 1: Persistent rolling-window metrics store

**Files:**
- Create: `app/metrics_store.py`
- Create: `tests/test_metrics_store.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `MetricsStore(db_path)`, `MetricsStore.record(kind, *, ts=None, correlation_id=None, latency_ms=None, cost_usd=None, tokens_in=None, tokens_out=None, quality_score=None, error_type=None)`, `MetricsStore.window_snapshot(minutes, *, now=None) -> dict` (keys: `traffic`, `error_total`, `error_rate_pct`, `latency_p50`, `latency_p95`, `latency_p99`, `avg_cost_usd`, `total_cost_usd`, `tokens_in_total`, `tokens_out_total`, `quality_avg`), `MetricsStore.baseline_snapshot(metric, lookback_minutes, exclude_minutes, *, now=None) -> dict` (keys: `mean`, `stddev`, `count`; `metric` is `"latency_ms"` or `"cost_usd"`), `MetricsStore.purge_older_than(days, *, now=None) -> int`, `MetricsStore.close()`, module-level `get_store() -> MetricsStore`, `reset_store() -> None`, `DEFAULT_DB_PATH`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_metrics_store.py
from __future__ import annotations

from app.metrics_store import MetricsStore


def test_window_snapshot_only_counts_events_inside_the_window() -> None:
    store = MetricsStore(":memory:")
    now = 1_000_000.0
    store.record("received", ts=now - 400, correlation_id="old")
    store.record(
        "response", ts=now - 400, latency_ms=999, cost_usd=1.0,
        tokens_in=10, tokens_out=10, quality_score=0.9,
    )
    store.record("received", ts=now - 10, correlation_id="new")
    store.record(
        "response", ts=now - 10, latency_ms=100, cost_usd=0.01,
        tokens_in=5, tokens_out=5, quality_score=0.8,
    )

    snapshot = store.window_snapshot(5, now=now)  # 5 min = 300s, excludes the -400s event

    assert snapshot["traffic"] == 1
    assert snapshot["latency_p50"] == 100.0
    assert snapshot["total_cost_usd"] == 0.01


def test_window_snapshot_computes_error_rate_from_received_and_error_events() -> None:
    store = MetricsStore(":memory:")
    now = 1_000_000.0
    for _ in range(4):
        store.record("received", ts=now - 5)
    store.record("error", ts=now - 5, error_type="RuntimeError")

    snapshot = store.window_snapshot(5, now=now)

    assert snapshot["traffic"] == 4
    assert snapshot["error_total"] == 1
    assert snapshot["error_rate_pct"] == 25.0


def test_baseline_snapshot_excludes_the_most_recent_window() -> None:
    store = MetricsStore(":memory:")
    now = 1_000_000.0
    store.record("response", ts=now - 3000, latency_ms=100)
    store.record("response", ts=now - 2000, latency_ms=200)
    store.record("response", ts=now - 60, latency_ms=9000)  # inside the excluded recent window

    baseline = store.baseline_snapshot("latency_ms", lookback_minutes=60, exclude_minutes=5, now=now)

    assert baseline["count"] == 2
    assert baseline["mean"] == 150.0


def test_purge_older_than_deletes_rows_past_retention() -> None:
    store = MetricsStore(":memory:")
    now = 1_000_000.0
    store.record("received", ts=now - 40 * 86400)
    store.record("received", ts=now - 1)

    deleted = store.purge_older_than(35, now=now)

    assert deleted == 1
    assert store.window_snapshot(100000, now=now)["traffic"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_metrics_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.metrics_store'`

- [ ] **Step 3: Implement the store**

```python
# app/metrics_store.py
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from statistics import mean, pstdev

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "metrics.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS request_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    correlation_id TEXT,
    latency_ms INTEGER,
    cost_usd REAL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    quality_score REAL,
    error_type TEXT
);
CREATE INDEX IF NOT EXISTS idx_request_events_ts ON request_events(ts);
"""

_BASELINE_FIELDS = {"latency_ms", "cost_usd"}


def _percentile(sorted_values: list[float], p: int) -> float:
    if not sorted_values:
        return 0.0
    idx = max(0, min(len(sorted_values) - 1, round((p / 100) * len(sorted_values) + 0.5) - 1))
    return float(sorted_values[idx])


class MetricsStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def record(
        self,
        kind: str,
        *,
        ts: float | None = None,
        correlation_id: str | None = None,
        latency_ms: int | None = None,
        cost_usd: float | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        quality_score: float | None = None,
        error_type: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO request_events "
                "(ts, kind, correlation_id, latency_ms, cost_usd, tokens_in, tokens_out, "
                "quality_score, error_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts if ts is not None else time.time(),
                    kind, correlation_id, latency_ms, cost_usd,
                    tokens_in, tokens_out, quality_score, error_type,
                ),
            )
            self._conn.commit()

    def window_snapshot(self, minutes: float, *, now: float | None = None) -> dict:
        now = now if now is not None else time.time()
        since = now - minutes * 60
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, latency_ms, cost_usd, tokens_in, tokens_out, quality_score "
                "FROM request_events WHERE ts >= ?",
                (since,),
            ).fetchall()

        received = [r for r in rows if r[0] == "received"]
        errors = [r for r in rows if r[0] == "error"]
        responses = [r for r in rows if r[0] == "response"]
        latencies = sorted(r[1] for r in responses if r[1] is not None)
        costs = [r[2] for r in responses if r[2] is not None]
        tokens_in_total = sum(r[3] for r in responses if r[3] is not None)
        tokens_out_total = sum(r[4] for r in responses if r[4] is not None)
        qualities = [r[5] for r in responses if r[5] is not None]
        traffic = len(received)
        error_total = len(errors)

        return {
            "traffic": traffic,
            "error_total": error_total,
            "error_rate_pct": round((error_total / traffic) * 100, 2) if traffic else 0.0,
            "latency_p50": _percentile(latencies, 50),
            "latency_p95": _percentile(latencies, 95),
            "latency_p99": _percentile(latencies, 99),
            "avg_cost_usd": round(mean(costs), 6) if costs else 0.0,
            "total_cost_usd": round(sum(costs), 6),
            "tokens_in_total": tokens_in_total,
            "tokens_out_total": tokens_out_total,
            "quality_avg": round(mean(qualities), 4) if qualities else 0.0,
        }

    def baseline_snapshot(
        self, metric: str, lookback_minutes: float, exclude_minutes: float, *, now: float | None = None
    ) -> dict:
        if metric not in _BASELINE_FIELDS:
            raise ValueError(f"Unsupported baseline metric: {metric}")
        now = now if now is not None else time.time()
        window_start = now - lookback_minutes * 60
        window_end = now - exclude_minutes * 60
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {metric} FROM request_events "
                f"WHERE kind = 'response' AND ts >= ? AND ts < ? AND {metric} IS NOT NULL",
                (window_start, window_end),
            ).fetchall()
        values = [r[0] for r in rows]
        if not values:
            return {"mean": 0.0, "stddev": 0.0, "count": 0}
        return {
            "mean": round(mean(values), 6),
            "stddev": round(pstdev(values), 6) if len(values) > 1 else 0.0,
            "count": len(values),
        }

    def purge_older_than(self, days: float, *, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        cutoff = now - days * 86400
        with self._lock:
            cur = self._conn.execute("DELETE FROM request_events WHERE ts < ?", (cutoff,))
            self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self._conn.close()


_default_store: MetricsStore | None = None


def get_store() -> MetricsStore:
    global _default_store
    if _default_store is None:
        _default_store = MetricsStore(DEFAULT_DB_PATH)
    return _default_store


def reset_store() -> None:
    global _default_store
    if _default_store is not None:
        _default_store.close()
    _default_store = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_metrics_store.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Add the DB file to `.gitignore`**

In `.gitignore`, add a line `data/metrics.db` next to the existing `data/logs.jsonl` entry (it's a runtime artifact, not source).

- [ ] **Step 6: Commit**

```bash
git add app/metrics_store.py tests/test_metrics_store.py .gitignore
git commit -m "feat: add SQLite-backed rolling-window metrics store"
```

---

### Task 2: Wire request recording into the metrics store

**Files:**
- Modify: `app/main.py`
- Modify: `app/agent.py`
- Create: `tests/test_metrics_store_wiring.py`

**Interfaces:**
- Consumes: `metrics_store.get_store()`, `MetricsStore.record(...)` from Task 1.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metrics_store_wiring.py
from __future__ import annotations

from fastapi.testclient import TestClient

from app import metrics_store
from app.main import app


def test_chat_request_is_recorded_in_the_metrics_store(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(metrics_store, "DEFAULT_DB_PATH", tmp_path / "metrics.db")
    metrics_store.reset_store()

    with TestClient(app) as client:
        response = client.post(
            "/chat",
            json={
                "user_id": "student-01",
                "session_id": "session-01",
                "feature": "qa",
                "message": "Explain observability",
            },
        )

    assert response.status_code == 200
    snapshot = metrics_store.get_store().window_snapshot(60)
    assert snapshot["traffic"] == 1
    assert snapshot["latency_p95"] > 0

    metrics_store.reset_store()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_metrics_store_wiring.py -v`
Expected: FAIL — `snapshot["traffic"] == 0` (nothing recorded yet)

- [ ] **Step 3: Wire `app/main.py`**

Add the import next to the other `.metrics` import (after the line `from .metrics import record_error, record_received, snapshot`):

```python
from .metrics_store import get_store
```

In `chat()`, right after the existing `record_received()` call:

```python
        record_received()
        get_store().record("received", correlation_id=request.state.correlation_id)
```

In `chat()`'s exception branch, right after `record_error(error_type)`:

```python
            record_error(error_type)
            get_store().record(
                "error", correlation_id=request.state.correlation_id, error_type=error_type
            )
```

In `global_exception_handler`, right after `record_error(error_type)`:

```python
    record_error(error_type)
    correlation_id = getattr(request.state, "correlation_id", "UNKNOWN")
    get_store().record("error", correlation_id=correlation_id, error_type=error_type)
```

(Move the `correlation_id = ...` line above `get_store().record(...)` if it isn't already before it — it currently sits right after in the existing code; keep the existing line, just add the `get_store()` call after it.)

In `http_exception_handler`, inside the existing `if exc.status_code >= 500:` block, right after `record_error(f"HTTP_{exc.status_code}")`:

```python
    if exc.status_code >= 500:
        record_error(f"HTTP_{exc.status_code}")
        get_store().record(
            "error",
            correlation_id=getattr(request.state, "correlation_id", "UNKNOWN"),
            error_type=f"HTTP_{exc.status_code}",
        )
```

- [ ] **Step 4: Wire `app/agent.py`**

Add the import next to `from . import metrics`:

```python
from . import metrics, metrics_store
```

Right after the existing `metrics.record_request(...)` call in `run()`:

```python
        metrics.record_request(
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            quality_score=quality_score,
        )
        metrics_store.get_store().record(
            "response",
            correlation_id=correlation_id,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            quality_score=quality_score,
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_metrics_store_wiring.py -v`
Expected: PASS

- [ ] **Step 6: Run the full existing suite to confirm nothing broke**

Run: `python -m pytest -q`
Expected: all tests pass (previous count + 1)

- [ ] **Step 7: Commit**

```bash
git add app/main.py app/agent.py tests/test_metrics_store_wiring.py
git commit -m "feat: record every request into the metrics store"
```

---

### Task 3: Prometheus export endpoint

**Files:**
- Create: `app/prometheus_export.py`
- Modify: `app/main.py`
- Modify: `requirements.txt`
- Create: `tests/test_prometheus_export.py`

**Interfaces:**
- Consumes: `metrics_store.get_store().window_snapshot(5)` from Task 1.
- Produces: `prometheus_export.render() -> bytes`, `prometheus_export.CONTENT_TYPE`.

- [ ] **Step 1: Add the dependency**

In `requirements.txt`, add a line:

```
prometheus-client==0.21.1
```

Install it: `pip install prometheus-client==0.21.1`

- [ ] **Step 2: Write the failing test**

```python
# tests/test_prometheus_export.py
from __future__ import annotations

from fastapi.testclient import TestClient

from app import metrics_store
from app.main import app


def test_prometheus_endpoint_exposes_gauges(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(metrics_store, "DEFAULT_DB_PATH", tmp_path / "metrics.db")
    metrics_store.reset_store()

    with TestClient(app) as client:
        client.post(
            "/chat",
            json={
                "user_id": "student-01",
                "session_id": "session-01",
                "feature": "qa",
                "message": "Explain observability",
            },
        )
        response = client.get("/metrics/prometheus")

    assert response.status_code == 200
    assert "day13_latency_p95_ms" in response.text
    assert "day13_traffic_requests" in response.text

    metrics_store.reset_store()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_prometheus_export.py -v`
Expected: FAIL — 404 on `/metrics/prometheus`

- [ ] **Step 4: Implement the export module**

```python
# app/prometheus_export.py
from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest

from . import metrics_store

CONTENT_TYPE = CONTENT_TYPE_LATEST


def render() -> bytes:
    registry = CollectorRegistry()
    snapshot = metrics_store.get_store().window_snapshot(5)

    Gauge("day13_latency_p50_ms", "Latency p50 in ms (rolling 5m)", registry=registry).set(snapshot["latency_p50"])
    Gauge("day13_latency_p95_ms", "Latency p95 in ms (rolling 5m)", registry=registry).set(snapshot["latency_p95"])
    Gauge("day13_latency_p99_ms", "Latency p99 in ms (rolling 5m)", registry=registry).set(snapshot["latency_p99"])
    Gauge("day13_error_rate_pct", "Error rate percent (rolling 5m)", registry=registry).set(snapshot["error_rate_pct"])
    Gauge("day13_traffic_requests", "Requests received (rolling 5m)", registry=registry).set(snapshot["traffic"])
    Gauge("day13_cost_usd_total", "Total cost in USD (rolling 5m)", registry=registry).set(snapshot["total_cost_usd"])
    Gauge("day13_tokens_in_total", "Total input tokens (rolling 5m)", registry=registry).set(snapshot["tokens_in_total"])
    Gauge("day13_tokens_out_total", "Total output tokens (rolling 5m)", registry=registry).set(snapshot["tokens_out_total"])
    Gauge("day13_quality_avg", "Average quality score (rolling 5m)", registry=registry).set(snapshot["quality_avg"])

    return generate_latest(registry)
```

- [ ] **Step 5: Add the route in `app/main.py`**

Add to the imports:

```python
from . import prometheus_export
```

Change the `fastapi.responses` import line to also bring in `Response`:

```python
from fastapi.responses import FileResponse, JSONResponse, Response
```

Add the route (near the existing `/metrics` route):

```python
@app.get("/metrics/prometheus", include_in_schema=False)
async def metrics_prometheus() -> Response:
    return Response(content=prometheus_export.render(), media_type=prometheus_export.CONTENT_TYPE)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_prometheus_export.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/prometheus_export.py app/main.py requirements.txt tests/test_prometheus_export.py
git commit -m "feat: add Prometheus text-format export at /metrics/prometheus"
```

---

### Task 4: Auto-remediation primitive — token cap in the fake LLM

**Files:**
- Modify: `app/mock_llm.py`
- Create: `tests/test_mock_llm_remediation.py`

**Interfaces:**
- Produces: `mock_llm.TOKEN_CAP: int | None` (module-level, default `None`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mock_llm_remediation.py
from __future__ import annotations

from app import mock_llm
from app.incidents import STATE


def test_token_cap_limits_output_tokens_even_during_cost_spike_incident() -> None:
    STATE["cost_spike"] = True
    mock_llm.TOKEN_CAP = 60
    try:
        response = mock_llm.FakeLLM().generate("Explain observability in one sentence")
        assert response.usage.output_tokens <= 60
    finally:
        STATE["cost_spike"] = False
        mock_llm.TOKEN_CAP = None


def test_no_cap_leaves_output_tokens_unbounded() -> None:
    mock_llm.TOKEN_CAP = None
    response = mock_llm.FakeLLM().generate("Explain observability in one sentence")
    assert response.usage.output_tokens >= 80
```

- [ ] **Step 2: Run tests to verify the cap test fails**

Run: `python -m pytest tests/test_mock_llm_remediation.py -v`
Expected: `test_token_cap_limits_output_tokens_even_during_cost_spike_incident` FAILS (`AttributeError: module 'app.mock_llm' has no attribute 'TOKEN_CAP'`); the second test passes already.

- [ ] **Step 3: Add the cap to `app/mock_llm.py`**

Add the module-level variable right after the `from .otel import ...` import line:

```python
TOKEN_CAP: int | None = None
```

In `FakeLLM.generate`, right after the existing `if STATE["cost_spike"]: output_tokens *= 4` block:

```python
                if STATE["cost_spike"]:
                    output_tokens *= 4
                if TOKEN_CAP is not None:
                    output_tokens = min(output_tokens, TOKEN_CAP)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mock_llm_remediation.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/mock_llm.py tests/test_mock_llm_remediation.py
git commit -m "feat: add TOKEN_CAP remediation primitive to the fake LLM"
```

---

### Task 5: Extend `config/alert_rules.yaml` with machine-readable fields + LatencyAnomaly rule

**Files:**
- Modify: `config/alert_rules.yaml`
- Modify: `docs/alerts.md`
- Create: `tests/test_alert_rules_config.py`

**Interfaces:**
- Produces: YAML fields `metric`, `window_minutes`, `operator`, `threshold`, `duration_seconds`, `baseline_lookback_minutes`, `zscore_threshold` per rule — consumed by `alerting.load_rules()` in Task 6.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_alert_rules_config.py
from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "alert_rules.yaml"


def test_alert_rules_have_required_structured_fields() -> None:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    rules = {rule["name"]: rule for rule in data["alerts"]}

    assert rules["HighLatencyP95"]["metric"] == "latency_p95_ms"
    assert rules["HighLatencyP95"]["threshold"] == 3000
    assert rules["HighLatencyP95"]["duration_seconds"] == 300

    assert rules["ElevatedErrorRate"]["metric"] == "error_rate_pct"
    assert rules["ElevatedErrorRate"]["threshold"] == 2
    assert rules["ElevatedErrorRate"]["duration_seconds"] == 300

    assert rules["CostSpike"]["operator"] == "gt_baseline_ratio"
    assert rules["CostSpike"]["threshold"] == 2
    assert rules["CostSpike"]["baseline_lookback_minutes"] == 1440

    assert rules["LatencyAnomaly"]["type"] == "anomaly-based"
    assert rules["LatencyAnomaly"]["zscore_threshold"] == 3
    assert rules["LatencyAnomaly"]["baseline_lookback_minutes"] == 60

    # Existing prose fields used as grading evidence must stay untouched.
    assert "for 5m" in rules["HighLatencyP95"]["condition"]
    assert rules["HighLatencyP95"]["owner"] == "D - SRE & Alerts Engineer"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_alert_rules_config.py -v`
Expected: FAIL — `KeyError: 'metric'`

- [ ] **Step 3: Rewrite `config/alert_rules.yaml`**

Replace the full file contents with:

```yaml
alerts:
  - name: HighLatencyP95
    severity: critical
    condition: >
      latency_p95_ms (rolling 5m, panel Latency trong config/dashboard.yaml)
      > 3000 for 5m
    type: symptom-based
    owner: D - SRE & Alerts Engineer
    runbook: docs/alerts.md#alert-1
    metric: latency_p95_ms
    window_minutes: 5
    operator: gt
    threshold: 3000
    duration_seconds: 300

  - name: ElevatedErrorRate
    severity: critical
    condition: >
      error_rate_pct (rolling 5m, panel Errors trong config/dashboard.yaml)
      > 2 for 5m
    type: symptom-based
    owner: D - SRE & Alerts Engineer
    runbook: docs/alerts.md#alert-2
    metric: error_rate_pct
    window_minutes: 5
    operator: gt
    threshold: 2
    duration_seconds: 300

  - name: CostSpike
    severity: warning
    condition: >
      avg_cost_usd_per_request (rolling 15m) > 2x avg_cost_usd_per_request
      baseline (rolling 24h) for 15m, OR total_cost_usd (panel Cost, cửa sổ
      60 phút) > 2.5
    type: symptom-based
    owner: D - SRE & Alerts Engineer
    runbook: docs/alerts.md#alert-3
    metric: avg_cost_usd_per_request
    window_minutes: 15
    operator: gt_baseline_ratio
    threshold: 2
    baseline_lookback_minutes: 1440
    duration_seconds: 900
    note: >
      The automated evaluator implements only the baseline-ratio half of the
      condition above. The flat total_cost_usd > 2.5 (60m window) half stays
      documentation/dashboard-only — see
      docs/superpowers/specs/2026-08-11-aiops-upgrade-design.md for why.

  - name: LatencyAnomaly
    severity: warning
    condition: >
      latency_p95_ms (rolling 5m) > baseline_mean_60m + 3 * baseline_stddev_60m,
      for 60s. Baseline excludes the most recent 5m so a live spike cannot
      pull its own baseline up.
    type: anomaly-based
    owner: D - SRE & Alerts Engineer
    runbook: docs/alerts.md#alert-4
    metric: latency_p95_ms
    window_minutes: 5
    baseline_lookback_minutes: 60
    zscore_threshold: 3
    duration_seconds: 60
```

- [ ] **Step 4: Add Alert 4 to `docs/alerts.md`**

Append after the existing "## Alert 3" section:

```markdown
## Alert 4

- Tên: LatencyAnomaly
- Severity: Warning
- SLI/SLO liên quan: `latency_p95_ms`, cùng panel Latency trong `config/dashboard.yaml`, nhưng so với baseline động thay vì ngưỡng tĩnh 3000ms của Alert 1.
- Điều kiện và thời gian duy trì: P95 (cửa sổ trượt 5 phút) > baseline_mean(60 phút, loại trừ 5 phút gần nhất) + 3 × baseline_stddev(60 phút), duy trì liên tục 60 giây.
- Ảnh hưởng tới người dùng: Phát hiện latency bất thường sớm hơn Alert 1 — bắt được xu hướng tăng dần chưa chạm ngưỡng tuyệt đối 3000ms, trước khi nó thực sự vi phạm SLO.
- Ba bước kiểm tra đầu tiên: giống Alert 1 (panel Latency → trace → log theo correlation ID).
- Mitigation tạm thời: giống Alert 1; ngoài ra alert này tự thấy baseline nên không cần tay chỉnh ngưỡng theo traffic pattern mới.
- Owner: D - SRE & Alerts Engineer.
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_alert_rules_config.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add config/alert_rules.yaml docs/alerts.md tests/test_alert_rules_config.py
git commit -m "feat: add machine-readable alert fields and LatencyAnomaly rule"
```

---

### Task 6: Alert evaluation engine core

**Files:**
- Create: `app/alerting.py`
- Create: `tests/test_alerting.py`

**Interfaces:**
- Consumes: `MetricsStore` from Task 1, `mock_llm.TOKEN_CAP` from Task 4, YAML fields from Task 5.
- Produces: `AlertRule`, `ConditionResult`, `AlertEngineState` dataclasses; `load_rules(path=CONFIG_PATH) -> list[AlertRule]`; `evaluate_condition(rule, store, now) -> ConditionResult`; `run_evaluation_cycle(store, rules, state, *, now=None) -> list[str]`; `REMEDIATION_HANDLERS: dict[str, tuple[Callable, Callable]]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_alerting.py
from __future__ import annotations

from app import mock_llm
from app.alerting import AlertEngineState, AlertRule, run_evaluation_cycle
from app.metrics_store import MetricsStore


def _latency_rule(duration_seconds: float = 300) -> AlertRule:
    return AlertRule(
        name="HighLatencyP95",
        severity="critical",
        rule_type="symptom-based",
        metric="latency_p95_ms",
        window_minutes=5,
        operator="gt",
        threshold=3000,
        duration_seconds=duration_seconds,
    )


def test_rule_does_not_fire_before_duration_elapses() -> None:
    store = MetricsStore(":memory:")
    now = 1_000_000.0
    store.record("response", ts=now, latency_ms=5000)
    state = AlertEngineState()

    events = run_evaluation_cycle(store, [_latency_rule()], state, now=now)

    assert events == []
    assert "HighLatencyP95" not in state.firing


def test_rule_fires_once_duration_elapses_and_does_not_refire_while_still_breaching() -> None:
    store = MetricsStore(":memory:")
    now = 1_000_000.0
    state = AlertEngineState()
    rule = _latency_rule()

    store.record("response", ts=now, latency_ms=5000)
    run_evaluation_cycle(store, [rule], state, now=now)  # breach starts, not fired yet

    store.record("response", ts=now + 301, latency_ms=5000)
    events = run_evaluation_cycle(store, [rule], state, now=now + 301)

    assert events == ["HighLatencyP95:fired"]
    assert "HighLatencyP95" in state.firing

    store.record("response", ts=now + 320, latency_ms=5000)
    events_again = run_evaluation_cycle(store, [rule], state, now=now + 320)
    assert events_again == []


def test_rule_resolves_when_condition_clears() -> None:
    store = MetricsStore(":memory:")
    now = 1_000_000.0
    state = AlertEngineState()
    state.firing.add("HighLatencyP95")
    state.breach_since["HighLatencyP95"] = now - 400
    store.record("response", ts=now, latency_ms=100)  # back under threshold

    events = run_evaluation_cycle(store, [_latency_rule()], state, now=now)

    assert events == ["HighLatencyP95:resolved"]
    assert "HighLatencyP95" not in state.firing


def test_latency_anomaly_fires_from_baseline_not_fixed_threshold() -> None:
    store = MetricsStore(":memory:")
    now = 1_000_000.0
    state = AlertEngineState()
    rule = AlertRule(
        name="LatencyAnomaly",
        severity="warning",
        rule_type="anomaly-based",
        metric="latency_p95_ms",
        window_minutes=5,
        operator=None,
        threshold=None,
        duration_seconds=60,
        baseline_lookback_minutes=60,
        zscore_threshold=3,
    )
    # Stable baseline around 150ms.
    for i in range(20):
        store.record("response", ts=now - 3000 + i * 10, latency_ms=150)
    # Recent window spikes to 900ms - well under the 3000ms fixed threshold,
    # but far above baseline_mean + 3*stddev when baseline stddev is ~0.
    store.record("response", ts=now - 5, latency_ms=900)

    run_evaluation_cycle(store, [rule], state, now=now)
    events = run_evaluation_cycle(store, [rule], state, now=now + 61)

    assert "LatencyAnomaly:fired" in events


def test_cost_spike_fire_and_resolve_drive_token_cap_remediation() -> None:
    store = MetricsStore(":memory:")
    t0 = 1_000_000.0
    state = AlertEngineState()
    rule = AlertRule(
        name="CostSpike",
        severity="warning",
        rule_type="symptom-based",
        metric="avg_cost_usd_per_request",
        window_minutes=15,
        operator="gt_baseline_ratio",
        threshold=2,
        duration_seconds=900,
        baseline_lookback_minutes=1440,
    )
    mock_llm.TOKEN_CAP = None

    # Cheap baseline requests an hour before t0 - well inside the 24h lookback.
    for i in range(10):
        store.record("response", ts=t0 - 3600 + i, cost_usd=0.002)

    # Expensive requests inside the 15-minute window at the first evaluation.
    for i in range(5):
        store.record("response", ts=t0 - 60 + i, cost_usd=0.02)
    run_evaluation_cycle(store, [rule], state, now=t0)
    assert "CostSpike" not in state.firing  # breach just started

    # Expensive requests inside the 15-minute window at the second evaluation.
    t1 = t0 + 901
    for i in range(5):
        store.record("response", ts=t1 - 60 + i, cost_usd=0.02)
    events = run_evaluation_cycle(store, [rule], state, now=t1)

    assert events == ["CostSpike:fired"]
    assert mock_llm.TOKEN_CAP == 60

    # Well past both spikes, only cheap requests remain in the 15-minute window.
    t2 = t1 + 900
    for i in range(5):
        store.record("response", ts=t2 - 60 + i, cost_usd=0.002)
    events2 = run_evaluation_cycle(store, [rule], state, now=t2)

    assert events2 == ["CostSpike:resolved"]
    assert mock_llm.TOKEN_CAP is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_alerting.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.alerting'`

- [ ] **Step 3: Implement `app/alerting.py`**

```python
# app/alerting.py
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from . import mock_llm
from .logging_config import get_logger
from .metrics_store import MetricsStore

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "alert_rules.yaml"

log = get_logger()


@dataclass
class AlertRule:
    name: str
    severity: str
    rule_type: str
    metric: str
    window_minutes: float
    operator: str | None
    threshold: float | None
    duration_seconds: float
    baseline_lookback_minutes: float | None = None
    zscore_threshold: float | None = None


@dataclass
class ConditionResult:
    breached: bool
    value: float | None
    threshold: float | None


@dataclass
class AlertEngineState:
    breach_since: dict[str, float] = field(default_factory=dict)
    firing: set[str] = field(default_factory=set)
    last_result: dict[str, ConditionResult] = field(default_factory=dict)


def load_rules(path: Path = CONFIG_PATH) -> list[AlertRule]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        AlertRule(
            name=entry["name"],
            severity=entry["severity"],
            rule_type=entry["type"],
            metric=entry["metric"],
            window_minutes=entry["window_minutes"],
            operator=entry.get("operator"),
            threshold=entry.get("threshold"),
            duration_seconds=entry["duration_seconds"],
            baseline_lookback_minutes=entry.get("baseline_lookback_minutes"),
            zscore_threshold=entry.get("zscore_threshold"),
        )
        for entry in data["alerts"]
    ]


_SIMPLE_METRICS: dict[str, Callable[[dict], float]] = {
    "latency_p95_ms": lambda w: w["latency_p95"],
    "error_rate_pct": lambda w: w["error_rate_pct"],
}

_OPERATORS: dict[str, Callable[[float, float], bool]] = {
    "gt": lambda v, t: v > t,
    "gte": lambda v, t: v >= t,
    "lt": lambda v, t: v < t,
    "lte": lambda v, t: v <= t,
}


def evaluate_condition(rule: AlertRule, store: MetricsStore, now: float) -> ConditionResult:
    if rule.rule_type == "anomaly-based":
        window = store.window_snapshot(rule.window_minutes, now=now)
        baseline = store.baseline_snapshot(
            "latency_ms", rule.baseline_lookback_minutes, rule.window_minutes, now=now
        )
        if baseline["count"] < 5:
            return ConditionResult(breached=False, value=None, threshold=None)
        limit = baseline["mean"] + rule.zscore_threshold * baseline["stddev"]
        value = window["latency_p95"]
        return ConditionResult(breached=value > limit, value=value, threshold=round(limit, 2))

    if rule.operator == "gt_baseline_ratio":
        window = store.window_snapshot(rule.window_minutes, now=now)
        baseline = store.baseline_snapshot(
            "cost_usd", rule.baseline_lookback_minutes, rule.window_minutes, now=now
        )
        if baseline["count"] < 5 or baseline["mean"] <= 0:
            return ConditionResult(breached=False, value=None, threshold=None)
        ratio = window["avg_cost_usd"] / baseline["mean"]
        return ConditionResult(breached=ratio > rule.threshold, value=round(ratio, 2), threshold=rule.threshold)

    window = store.window_snapshot(rule.window_minutes, now=now)
    value = _SIMPLE_METRICS[rule.metric](window)
    breached = _OPERATORS[rule.operator](value, rule.threshold)
    return ConditionResult(breached=breached, value=value, threshold=rule.threshold)


def _apply_cost_spike_remediation() -> None:
    mock_llm.TOKEN_CAP = 60
    log.warning("remediation_applied", rule="CostSpike", action="cap_output_tokens", cap=60)


def _clear_cost_spike_remediation() -> None:
    mock_llm.TOKEN_CAP = None
    log.info("remediation_cleared", rule="CostSpike")


REMEDIATION_HANDLERS: dict[str, tuple[Callable[[], None], Callable[[], None]]] = {
    "CostSpike": (_apply_cost_spike_remediation, _clear_cost_spike_remediation),
}


def run_evaluation_cycle(
    store: MetricsStore,
    rules: list[AlertRule],
    state: AlertEngineState,
    *,
    now: float | None = None,
) -> list[str]:
    now = now if now is not None else time.time()
    events: list[str] = []
    for rule in rules:
        result = evaluate_condition(rule, store, now)
        state.last_result[rule.name] = result
        if result.breached:
            since = state.breach_since.setdefault(rule.name, now)
            duration = now - since
            if duration >= rule.duration_seconds and rule.name not in state.firing:
                state.firing.add(rule.name)
                log.warning(
                    "alert_fired",
                    rule=rule.name,
                    severity=rule.severity,
                    value=result.value,
                    threshold=result.threshold,
                )
                events.append(f"{rule.name}:fired")
                if rule.name in REMEDIATION_HANDLERS:
                    REMEDIATION_HANDLERS[rule.name][0]()
        else:
            state.breach_since.pop(rule.name, None)
            if rule.name in state.firing:
                state.firing.discard(rule.name)
                log.info("alert_resolved", rule=rule.name, severity=rule.severity)
                events.append(f"{rule.name}:resolved")
                if rule.name in REMEDIATION_HANDLERS:
                    REMEDIATION_HANDLERS[rule.name][1]()
    return events
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_alerting.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/alerting.py tests/test_alerting.py
git commit -m "feat: add alert evaluation engine with anomaly detection and remediation hooks"
```

---

### Task 7: Wire the background loop, `/alerts/status`, and the Prometheus alert gauge

**Files:**
- Modify: `app/alerting.py`
- Modify: `app/main.py`
- Modify: `app/prometheus_export.py`
- Create: `tests/test_alerts_status_route.py`

**Interfaces:**
- Produces: `alerting.start_background_loop()`, `alerting.stop_background_loop()`, `alerting.status_snapshot() -> dict` (keys: `firing: list[str]`, `results: dict[str, dict]`), `alerting.EVAL_INTERVAL_SECONDS`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_alerts_status_route.py
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_alerts_status_route_returns_firing_and_results_shape() -> None:
    with TestClient(app) as client:
        response = client.get("/alerts/status")

    assert response.status_code == 200
    body = response.json()
    assert "firing" in body
    assert "results" in body
    assert isinstance(body["firing"], list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_alerts_status_route.py -v`
Expected: FAIL — 404 on `/alerts/status`

- [ ] **Step 3: Add the background loop to `app/alerting.py`**

Add `import asyncio` to the top imports. Append at the bottom of the file:

```python
EVAL_INTERVAL_SECONDS = 15

_engine_state = AlertEngineState()
_loop_task: "asyncio.Task | None" = None


async def _background_loop() -> None:
    from .metrics_store import get_store

    rules = load_rules()
    store = get_store()
    while True:
        try:
            run_evaluation_cycle(store, rules, _engine_state)
        except Exception:
            log.exception("alert_loop_error")
        await asyncio.sleep(EVAL_INTERVAL_SECONDS)


def start_background_loop() -> None:
    global _loop_task
    _loop_task = asyncio.create_task(_background_loop())


def stop_background_loop() -> None:
    global _loop_task
    if _loop_task is not None:
        _loop_task.cancel()
        _loop_task = None


def status_snapshot() -> dict:
    return {
        "firing": sorted(_engine_state.firing),
        "results": {
            name: {"value": r.value, "threshold": r.threshold, "breached": r.breached}
            for name, r in _engine_state.last_result.items()
        },
    }
```

(`get_store` is imported inside `_background_loop` rather than at module top level to avoid the reader needing to track a second cross-module import in this diff — `metrics_store` has no import back to `alerting`, so a top-level import would also be safe; either is fine, this keeps the diff localized to the function that uses it.)

- [ ] **Step 4: Wire into `app/main.py`**

Add to imports:

```python
from . import alerting
```

Modify `startup()` and `shutdown()`:

```python
@app.on_event("startup")
async def startup() -> None:
    otel_enabled = configure_telemetry()
    log.info(
        "app_started",
        service=os.getenv("APP_NAME", "day13-observability-lab"),
        env=os.getenv("APP_ENV", "dev"),
        payload={"tracing_enabled": tracing_enabled(), "otel_enabled": otel_enabled},
    )
    alerting.start_background_loop()


@app.on_event("shutdown")
async def shutdown() -> None:
    alerting.stop_background_loop()
    shutdown_telemetry()
```

Add the route:

```python
@app.get("/alerts/status")
async def alerts_status() -> dict:
    return alerting.status_snapshot()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_alerts_status_route.py -v`
Expected: PASS

- [ ] **Step 6: Extend `app/prometheus_export.py` with the alert-firing gauge**

Add to imports:

```python
from . import alerting, metrics_store
```

(replacing the previous `from . import metrics_store` line)

At the end of `render()`, before `return generate_latest(registry)`:

```python
    alert_gauge = Gauge(
        "day13_alert_firing", "1 if the alert rule is currently firing", ["rule"], registry=registry
    )
    status = alerting.status_snapshot()
    firing = set(status["firing"])
    for rule_name in status["results"]:
        alert_gauge.labels(rule=rule_name).set(1.0 if rule_name in firing else 0.0)

    return generate_latest(registry)
```

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add app/alerting.py app/main.py app/prometheus_export.py tests/test_alerts_status_route.py
git commit -m "feat: run the alert loop in-process and expose /alerts/status"
```

---

### Task 8: Local Grafana + Prometheus via Docker Compose

**Files:**
- Create: `docker-compose.monitoring.yml`
- Create: `observability/prometheus/prometheus.yml`
- Create: `observability/grafana/provisioning/datasources/prometheus.yml`
- Create: `observability/grafana/provisioning/dashboards/dashboard.yml`
- Create: `observability/grafana/provisioning/dashboards/day13-observability.json`
- Modify: `SETUP.md`

This task has no Python tests — it's verified by manually starting the stack. No step here modifies application code, so the existing test suite is unaffected by construction.

- [ ] **Step 1: Prometheus scrape config**

```yaml
# observability/prometheus/prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: day13-observability-lab
    metrics_path: /metrics/prometheus
    static_configs:
      - targets: ["host.docker.internal:8000"]
```

- [ ] **Step 2: Grafana datasource provisioning**

```yaml
# observability/grafana/provisioning/datasources/prometheus.yml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

- [ ] **Step 3: Grafana dashboard provider config**

```yaml
# observability/grafana/provisioning/dashboards/dashboard.yml
apiVersion: 1

providers:
  - name: Day 13 Observability
    orgId: 1
    folder: ""
    type: file
    options:
      path: /etc/grafana/provisioning/dashboards
```

- [ ] **Step 4: Grafana dashboard JSON (6 panels + alert-firing table)**

```json
{
  "uid": "day13-observability",
  "title": "Day 13 AI Observability",
  "schemaVersion": 39,
  "version": 1,
  "refresh": "30s",
  "time": { "from": "now-1h", "to": "now" },
  "panels": [
    {
      "id": 1,
      "title": "Latency percentiles (ms)",
      "type": "timeseries",
      "gridPos": { "h": 8, "w": 8, "x": 0, "y": 0 },
      "targets": [
        { "expr": "day13_latency_p50_ms", "legendFormat": "p50", "refId": "A" },
        { "expr": "day13_latency_p95_ms", "legendFormat": "p95", "refId": "B" },
        { "expr": "day13_latency_p99_ms", "legendFormat": "p99", "refId": "C" }
      ]
    },
    {
      "id": 2,
      "title": "Traffic (requests, rolling 5m)",
      "type": "stat",
      "gridPos": { "h": 8, "w": 8, "x": 8, "y": 0 },
      "targets": [{ "expr": "day13_traffic_requests", "refId": "A" }]
    },
    {
      "id": 3,
      "title": "Error rate (%)",
      "type": "stat",
      "gridPos": { "h": 8, "w": 8, "x": 16, "y": 0 },
      "targets": [{ "expr": "day13_error_rate_pct", "refId": "A" }]
    },
    {
      "id": 4,
      "title": "Cost (USD, rolling 5m)",
      "type": "stat",
      "gridPos": { "h": 8, "w": 8, "x": 0, "y": 8 },
      "targets": [{ "expr": "day13_cost_usd_total", "refId": "A" }]
    },
    {
      "id": 5,
      "title": "Tokens in/out",
      "type": "timeseries",
      "gridPos": { "h": 8, "w": 8, "x": 8, "y": 8 },
      "targets": [
        { "expr": "day13_tokens_in_total", "legendFormat": "in", "refId": "A" },
        { "expr": "day13_tokens_out_total", "legendFormat": "out", "refId": "B" }
      ]
    },
    {
      "id": 6,
      "title": "Quality score (avg)",
      "type": "stat",
      "gridPos": { "h": 8, "w": 8, "x": 16, "y": 8 },
      "targets": [{ "expr": "day13_quality_avg", "refId": "A" }]
    },
    {
      "id": 7,
      "title": "Alert firing state",
      "type": "table",
      "gridPos": { "h": 8, "w": 24, "x": 0, "y": 16 },
      "targets": [{ "expr": "day13_alert_firing", "format": "table", "instant": true, "refId": "A" }]
    }
  ]
}
```

- [ ] **Step 5: Docker Compose stack**

```yaml
# docker-compose.monitoring.yml
services:
  prometheus:
    image: prom/prometheus:v2.55.1
    volumes:
      - ./observability/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana:11.2.0
    environment:
      GF_AUTH_ANONYMOUS_ENABLED: "true"
      GF_AUTH_ANONYMOUS_ORG_ROLE: "Admin"
    volumes:
      - ./observability/grafana/provisioning:/etc/grafana/provisioning:ro
    ports:
      - "3000:3000"
    depends_on:
      - prometheus
```

- [ ] **Step 6: Document it in `SETUP.md`**

Add a new subsection after the existing "3.1 Jaeger local qua OTLP" section:

```markdown
### 3.2 Grafana + Prometheus local (tùy chọn, cho phần nâng cấp AIOps)

Chạy API trước (`uvicorn app.main:app --reload --env-file .env`), sau đó:

```bash
docker compose -f docker-compose.monitoring.yml up -d
```

Mở Grafana tại `http://localhost:3000` (đăng nhập ẩn danh, quyền Admin đã
bật sẵn cho môi trường lab) — dashboard "Day 13 AI Observability" đã được
provision sẵn với 6 panel + 1 panel trạng thái alert. Prometheus scrape
`GET /metrics/prometheus` của app mỗi 15 giây qua
`host.docker.internal:8000`.

Dừng stack: `docker compose -f docker-compose.monitoring.yml down`.
```

- [ ] **Step 7: Manually verify the stack**

Run:

```bash
uvicorn app.main:app --env-file .env &
docker compose -f docker-compose.monitoring.yml up -d
python scripts/load_test.py
```

Open `http://localhost:9090/targets` — the `day13-observability-lab` target should be `UP`. Open `http://localhost:3000/d/day13-observability` — all 7 panels should show data. Then:

```bash
docker compose -f docker-compose.monitoring.yml down
```

- [ ] **Step 8: Commit**

```bash
git add docker-compose.monitoring.yml observability SETUP.md
git commit -m "feat: add local Grafana + Prometheus stack for the metrics export"
```

---

### Task 9: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest -q`
Expected: all tests pass (original 30 + new tests from Tasks 1–7)

- [ ] **Step 2: Confirm the existing lab contract is untouched**

Run: `python scripts/validate_dashboard.py`
Expected: `HỢP LỆ: 6/6 panel có trong dashboard contract.`

Run: `python scripts/validate_logs.py`
Expected: unaffected by this work (it reads `data/logs.jsonl`, which nothing in this plan touches)

- [ ] **Step 3: Manual smoke test of the full loop**

```bash
uvicorn app.main:app --env-file .env &
python scripts/inject_incident.py --scenario cost_spike
python scripts/load_test.py --concurrency 5
```

Wait at least 20 seconds (background loop tick), then:

```bash
curl -s http://127.0.0.1:8000/alerts/status
```

Expected: `CostSpike` present under `results`; after enough load-test traffic pushes the 15-minute ratio and 900s duration past the threshold, it appears in `firing` and `data/logs.jsonl`/stdout shows `remediation_applied`. Disable the incident and confirm `remediation_cleared` appears once the window clears:

```bash
python scripts/inject_incident.py --scenario cost_spike --disable
```

- [ ] **Step 4: Check `git status` is clean**

Run: `git status --short`
Expected: no output (everything committed across Tasks 1–8)

---

## Self-review notes

- **Spec coverage:** rolling-window store (Task 1–2), Prometheus export (Task 3, extended Task 7), anomaly detection (Task 5–6), alert engine (Task 6–7), CostSpike remediation (Task 4, 6), Grafana/Prometheus Docker Compose (Task 8) — every component in the design spec has a task.
- **Additive constraint:** no task modifies `GET /metrics` JSON shape, `config/dashboard.yaml`, or `scripts/validate_dashboard.py`; Task 5 only adds fields to `config/alert_rules.yaml`, never removes or edits existing prose.
- **Type consistency checked:** `AlertRule`/`ConditionResult`/`AlertEngineState` field names match between Task 6's implementation and Task 7's `status_snapshot()`; `MetricsStore.record()`'s keyword names match what Task 2's call sites pass; `prometheus_export.render()` signature is stable across Task 3 and Task 7's extension.
