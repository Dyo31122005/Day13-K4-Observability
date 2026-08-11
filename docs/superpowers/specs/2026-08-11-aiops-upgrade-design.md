# AIOps upgrade — design spec

Date: 2026-08-11
Status: Approved by user, pending implementation plan

## Context

The Day 13 observability lab (`app/`, `config/`, `scripts/`, `submission/`) is functionally
complete: JSON logging with correlation IDs, PII redaction, OTEL + Langfuse tracing, a
six-panel dashboard (`scripts/dashboard.py`), declarative SLOs/alert rules
(`config/slo.yaml`, `config/alert_rules.yaml`), and a documented runbook (`docs/alerts.md`).
It is graded observability, not AIOps: alert rules are prose read by humans, metrics live in
unbounded in-memory lists with no timestamps, there is no automated detection loop, no
anomaly detection, and no remediation.

The user wants to upgrade the system toward real AIOps, for two combined goals: the lab's
bonus rubric (`RUBRIC.md` allows up to +10 for "automation hữu ích") and as a portfolio-grade
personal project. Scope was narrowed through a clarifying dialogue (see below) rather than
building every possible AIOps feature.

## Decisions made during brainstorming

- Priority areas (all four, user selected all): real alert evaluation loop, rolling-window
  metrics + Prometheus export, statistical anomaly detection, auto-remediation for one
  scenario.
- Auto-remediation scenario: **CostSpike only** — temporarily cap output tokens. Chosen
  because it cannot make a response *wrong*, only cheaper, unlike touching the RAG path or
  auto-disabling incidents.
- Alert delivery channel: **structured log event only** (`alert_fired` / `alert_resolved`),
  no Slack/webhook — no external channel available to test against in this session.
- Metrics persistence: **SQLite**, not pure in-memory — rolling windows must be correct
  (5m/15m/28d as declared in `config/slo.yaml`) and must survive a Railway restart/redeploy,
  which in-memory lists cannot do.
- Visualization: **real Grafana + Prometheus via Docker Compose**, local only (same pattern
  as the existing `docker-compose.jaeger.yml`), not deployed to Railway (Railway currently
  runs one service; standing up Prometheus/Grafana there is a separate, later decision).

## Hard constraint: additive only

Everything below must be additive to the already-graded/evidenced lab state:

- `GET /metrics` (JSON) keeps its exact current shape — `scripts/dashboard.py`,
  `tests/test_metrics.py`, and any grading tooling read it as-is today.
- `config/dashboard.yaml` contract and `scripts/validate_dashboard.py` are untouched.
- `config/alert_rules.yaml` keeps every existing prose field (`condition`, `severity`,
  `type`, `owner`, `runbook`) byte-identical; new fields are added alongside, not in place of.
- The existing 30 tests in `tests/` must stay green with no behavior changes.
- `submission/evidence/` and `submission/REPORT.md` content already covering the graded
  checkpoints is not modified by this work (new evidence may be added for the bonus).

## Components

### 1. Persistent rolling-window metrics store

New module `app/metrics_store.py`. SQLite file at `data/metrics.db` (added to `.gitignore`
next to `data/logs.jsonl` — it's a runtime artifact, not source).

Schema (single table, no migration framework needed for one table):

```sql
CREATE TABLE IF NOT EXISTS request_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,              -- time.time(), UTC epoch seconds
    kind TEXT NOT NULL,            -- 'received' | 'response' | 'error'
    correlation_id TEXT,
    latency_ms INTEGER,
    cost_usd REAL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    quality_score REAL,
    error_type TEXT
);
CREATE INDEX IF NOT EXISTS idx_request_events_ts ON request_events(ts);
```

Connection is parameterized (default `data/metrics.db`, override via constructor arg) so
tests use `:memory:` or a temp file instead of the real DB.

Functions:

- `record(kind, **fields)` — called from the *same* call sites that already call
  `metrics.record_received/record_request/record_error` in `app/main.py` and
  `app/agent.py`. This is additive: the existing in-memory globals in `app/metrics.py` keep
  being written exactly as today; `metrics_store.record(...)` is a second call alongside them.
- `window_snapshot(minutes: float) -> dict` — same field shape as `metrics.snapshot()`
  (`traffic`, `error_rate_pct`, `latency_p50/p95/p99`, `avg_cost_usd`, `total_cost_usd`,
  `tokens_in_total`, `tokens_out_total`, `quality_avg`) but scoped to
  `WHERE ts >= now - minutes*60`.
- `baseline_snapshot(lookback_minutes: float, exclude_minutes: float) -> dict` — mean/stddev
  of `latency_ms` (and later other metrics if needed) over `[now - lookback, now - exclude]`,
  used by the anomaly rule.
- `purge_older_than(days: float)` — deletes rows past retention (35 days, longer than the
  28-day SLO window), called once per alert-loop tick.

### 2. Prometheus export

New route `GET /metrics/prometheus` in `app/main.py`, built with `prometheus_client`
(new dependency in `requirements.txt`). Values are computed from
`metrics_store.window_snapshot(5)` at scrape time (gauges, not counters, since they're
recomputed from the store each scrape — simpler and avoids double-counting across restarts).
Exposes: `day13_latency_p50_ms`, `day13_latency_p95_ms`, `day13_latency_p99_ms`,
`day13_error_rate_pct`, `day13_traffic_requests`, `day13_cost_usd_total`,
`day13_tokens_in_total`, `day13_tokens_out_total`, `day13_quality_avg`, and
`day13_alert_firing{rule="..."}` (1/0 gauge per rule, from the alert engine's state — see
below). Existing `GET /metrics` JSON route is untouched.

### 3. Alert evaluation engine

New module `app/alerting.py`. An `asyncio` background task started in the existing FastAPI
`startup` handler (`app/main.py`) and cancelled in `shutdown` — no separate process, fits how
Railway runs this as a single service.

`config/alert_rules.yaml` gets new machine-readable fields added to each existing rule,
alongside (not replacing) the prose fields already there:

```yaml
  - name: HighLatencyP95
    severity: critical
    condition: >
      ...   # unchanged prose, still read by humans / existing evidence
    type: symptom-based
    owner: D - SRE & Alerts Engineer
    runbook: docs/alerts.md#alert-1
    # new, additive:
    metric: latency_p95_ms
    window_minutes: 5
    operator: gt
    threshold: 3000
    duration_seconds: 300
```

`ElevatedErrorRate` gets the equivalent structured fields: `metric: error_rate_pct`,
`window_minutes: 5`, `operator: gt`, `threshold: 2`, `duration_seconds: 300`.

`CostSpike`'s existing prose is an OR of two different conditions (ratio vs. 24h baseline
over 15m, OR total cost over a 60m window exceeding a flat $2.5). The structured evaluator
implements only the first (ratio vs. baseline — it's the more meaningful signal and reuses
the same baseline mechanism `LatencyAnomaly` needs): `metric: avg_cost_usd_per_request`,
`window_minutes: 15`, `operator: gt_baseline_ratio`, `threshold: 2` (meaning 2x),
`baseline_lookback_minutes: 1440`, `duration_seconds: 900`. The flat-$2.5-total condition
stays prose-only/dashboard-only (the existing Cost panel in `scripts/dashboard.py` already
flags it visually via its threshold) — noted here explicitly so the implementer doesn't
need to invent an OR-of-conditions schema for one edge case.

A fourth rule is added, type `anomaly-based`, using `baseline_snapshot` instead of a fixed
threshold:

```yaml
  - name: LatencyAnomaly
    severity: warning
    condition: >
      latency_p95_ms (rolling 5m) > baseline_mean_60m + 3 * baseline_stddev_60m,
      for 60s. Baseline excludes the most recent 5m so a live spike cannot pull its
      own baseline up.
    type: anomaly-based
    owner: D - SRE & Alerts Engineer
    runbook: docs/alerts.md#alert-4
    metric: latency_p95_ms
    window_minutes: 5
    baseline_lookback_minutes: 60
    zscore_threshold: 3
    duration_seconds: 60
```

Evaluation loop (every ~15s):

1. For each rule, compute the current metric value from `metrics_store` (window or baseline
   comparison depending on `type`).
2. Track first-breach timestamp per rule in an in-process dict (`_breach_since: dict[str,
   float]`). A rule "fires" once the condition has been continuously true for
   `duration_seconds`, and only fires once per breach (edge-triggered) — matches the
   "duy trì N phút" language already in `docs/alerts.md`.
3. On fire: `log.warning("alert_fired", rule=..., severity=..., value=..., threshold=...)`,
   update in-memory firing state (used by `/metrics/prometheus` and `/alerts/status`), and
   call the rule's remediation handler if registered (CostSpike only, see below).
4. On recovery (condition no longer true while previously firing): `log.info(
   "alert_resolved", rule=..., ...)`, clear firing state, call the remediation "undo".

New route `GET /alerts/status` returns current firing/resolved state per rule — for demoing
the loop live during grading without waiting on log files.

### 4. Auto-remediation (CostSpike only)

`app/mock_llm.py` gets a module-level `TOKEN_CAP: int | None = None`. When set,
`FakeLLM.generate` clamps `output_tokens = min(output_tokens, TOKEN_CAP)` before computing
usage — same mechanism the `cost_spike` incident already uses in reverse
(`output_tokens *= 4`).

The alerting engine's `CostSpike` remediation handler:
- On fire: `mock_llm.TOKEN_CAP = 60`, `log.warning("remediation_applied", rule="CostSpike",
  action="cap_output_tokens", cap=60)`.
- On resolve: `mock_llm.TOKEN_CAP = None`, `log.info("remediation_cleared", rule="CostSpike")`.

### 5. Grafana + Prometheus via Docker Compose (local)

New `docker-compose.monitoring.yml`, following the existing `docker-compose.jaeger.yml`
pattern (local-only, optional, documented in `SETUP.md`):

- `prometheus` service (official `prom/prometheus` image) with
  `observability/prometheus/prometheus.yml` scraping the host app's
  `/metrics/prometheus` (via `host.docker.internal`, which Docker Desktop on Windows
  supports).
- `grafana` service (official `grafana/grafana` image) with provisioning files under
  `observability/grafana/provisioning/`: a Prometheus datasource pointed at the `prometheus`
  service, and one pre-loaded dashboard JSON with 6 panels matching
  `config/dashboard.yaml` (latency/traffic/errors/cost/tokens/quality) plus a 7th panel
  showing `day13_alert_firing` per rule.

This is local tooling for demo/portfolio use, not part of the Railway deployment.

## Testing strategy

- `tests/test_metrics_store.py`: `window_snapshot`/`baseline_snapshot` correctness using an
  injected fake clock and `:memory:` SQLite — insert events at known timestamps, assert
  windowing and baseline math.
- `tests/test_alerting.py`: rule evaluation is a pure function
  `evaluate(rule, metrics_store, now) -> AlertDecision` that the background loop calls —
  tested directly with fabricated snapshots/timestamps, no real `asyncio.sleep` or wall-clock
  waiting. Covers: fires after duration, doesn't fire before duration, resolves, doesn't
  double-fire while still breaching.
- `tests/test_mock_llm_remediation.py`: `TOKEN_CAP` clamps `output_tokens`; unset means no
  clamping (existing `cost_spike` behavior unaffected when cap is `None`).
- One light integration test: enable `cost_spike` incident, call the evaluator directly
  (not the loop) enough times to cross `duration_seconds`, assert `alert_fired` +
  `TOKEN_CAP == 60`, then disable the incident and assert `alert_resolved` +
  `TOKEN_CAP is None`.
- Existing 30 tests must stay green unmodified (verifies the additive constraint).

## Non-goals (explicit)

- No Slack/PagerDuty/webhook delivery — log-only, per user's choice.
- No deploying Prometheus/Grafana to Railway — local Docker Compose only.
- No ML-based anomaly detection — mean/stddev z-score only, per user's choice ("thống kê,
  không cần ML nặng").
- No changes to `GET /metrics` JSON shape, `config/dashboard.yaml`, or
  `scripts/validate_dashboard.py`.
- No auto-remediation for `HighLatencyP95` or `ElevatedErrorRate` — evaluated and logged like
  CostSpike, but no automated action (per user's choice to scope remediation to one
  scenario).

## Open risk

SQLite writes happen synchronously on the request path (`metrics_store.record(...)` from
`app/main.py`/`app/agent.py`). For this app's traffic levels (lab load test, not production
scale) this is fine; noting it here rather than adding write-batching complexity that YAGNI
doesn't justify yet.
