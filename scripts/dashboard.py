"""Serve the six-panel Day 13 dashboard from the JSONL log contract.

Run with: python scripts/dashboard.py
Then open http://127.0.0.1:8001. The page refreshes every 30 seconds.
"""

from __future__ import annotations

import argparse
import html
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import mean

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = REPO_ROOT / "data" / "logs.jsonl"
CONFIG_PATH = REPO_ROOT / "config" / "dashboard.yaml"
HTML_PATH = Path(__file__).resolve().parent / "dashboard.html"


def as_number(record: dict, field: str) -> float | None:
    value = record.get(field)
    return float(value) if isinstance(value, (int, float)) else None


def percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((p / 100) * len(ordered) + 0.5) - 1))
    return ordered[index]


def load_records(log_path: Path) -> list[dict]:
    records: list[dict] = []
    for line in log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []:
        try:
            record = json.loads(line)
            record["_timestamp"] = datetime.fromisoformat(record["ts"].replace("Z", "+00:00"))
            records.append(record)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return records


def summarise(records: list[dict], window_minutes: int) -> dict:
    latest = max((record["_timestamp"] for record in records), default=None)
    window_start = latest - timedelta(minutes=window_minutes) if latest else None
    scoped = [record for record in records if not window_start or record["_timestamp"] >= window_start]
    received = [record for record in scoped if record.get("event") == "request_received"]
    failed = [record for record in scoped if record.get("event") == "request_failed"]
    responses = [record for record in scoped if record.get("event") == "response_sent"]
    latencies = [value for record in responses if (value := as_number(record, "latency_ms")) is not None]
    costs = [value for record in responses if (value := as_number(record, "cost_usd")) is not None]
    tokens_in = sum(value for record in responses if (value := as_number(record, "tokens_in")) is not None)
    tokens_out = sum(value for record in responses if (value := as_number(record, "tokens_out")) is not None)
    qualities = [value for record in responses if (value := as_number(record, "quality_score")) is not None]
    costs_by_minute: defaultdict[str, float] = defaultdict(float)
    for record in responses:
        value = as_number(record, "cost_usd")
        if value is not None:
            costs_by_minute[record["_timestamp"].strftime("%H:%M")] += value

    # Build per-minute latency series for charts
    latency_by_minute: defaultdict[str, list[float]] = defaultdict(list)
    for record in responses:
        lat = as_number(record, "latency_ms")
        if lat is not None:
            latency_by_minute[record["_timestamp"].strftime("%H:%M")].append(lat)

    latency_series = []
    for minute in sorted(latency_by_minute.keys()):
        vals = latency_by_minute[minute]
        latency_series.append({
            "minute": minute,
            "p50": percentile(vals, 50),
            "p95": percentile(vals, 95),
            "p99": percentile(vals, 99),
        })

    # Build per-minute traffic series
    traffic_by_minute: defaultdict[str, int] = defaultdict(int)
    for record in received:
        traffic_by_minute[record["_timestamp"].strftime("%H:%M")] += 1

    traffic_series = []
    for minute in sorted(traffic_by_minute.keys()):
        traffic_series.append({"minute": minute, "count": traffic_by_minute[minute]})

    # Recent logs (last 15 entries, newest first)
    recent_logs = []
    for record in reversed(scoped[-15:]):
        entry = {
            "ts": record.get("ts", ""),
            "event": record.get("event", ""),
            "service": record.get("service", ""),
            "correlation_id": record.get("correlation_id", ""),
            "raw": record,
        }
        lat = as_number(record, "latency_ms")
        if lat is not None:
            entry["latency_ms"] = lat
        recent_logs.append(entry)

    return {
        "latest": latest.isoformat() if latest else "No valid log timestamp",
        "records": len(scoped),
        "latency": {"p50": percentile(latencies, 50), "p95": percentile(latencies, 95), "p99": percentile(latencies, 99)},
        "latency_series": latency_series,
        "traffic": {"count": len(received), "per_minute": len(received) / window_minutes},
        "traffic_series": traffic_series,
        "errors": {"rate": (len(failed) / len(received) * 100) if received else 0.0, "breakdown": dict(Counter(record.get("error_type") or "unknown" for record in failed))},
        "cost": {"total": sum(costs), "by_minute": dict(sorted(costs_by_minute.items()))},
        "tokens": {"input": tokens_in, "output": tokens_out},
        "quality": mean(qualities) if qualities else 0.0,
        "recent_logs": recent_logs,
    }


def dashboard_summary() -> dict:
    """Return the current dashboard dataset for the standalone server or FastAPI."""

    dashboard = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["dashboard"]
    return summarise(load_records(LOG_PATH), dashboard["time_range_minutes"])


def status(value: float, operator: str, limit: float) -> str:
    return "good" if (value <= limit if operator == "lte" else value >= limit) else "bad"


def card(title: str, unit: str, threshold: str, state: str, body: str) -> str:
    return f'<section class="card {state}"><h2>{html.escape(title)}</h2><p class="threshold">SLO: {html.escape(threshold)}</p>{body}<p class="unit">Unit: {html.escape(unit)}</p></section>'


def render(summary: dict, dashboard: dict) -> str:
    panels = {panel["id"]: panel for panel in dashboard["panels"]}
    latency_panel, traffic_panel, errors_panel = panels["latency"], panels["traffic"], panels["errors"]
    cost_panel, tokens_panel, quality_panel = panels["cost"], panels["tokens"], panels["quality"]
    latency_limit = latency_panel["threshold"]["value"]
    traffic_limit = traffic_panel["threshold"]["value"]
    error_limit = errors_panel["threshold"]["value"]
    cost_limit = cost_panel["threshold"]["value"]
    token_limit = tokens_panel["threshold"]["value"]
    quality_limit = quality_panel["threshold"]["value"]
    cost_points = ", ".join(f"{label}: ${value:.4f}" for label, value in summary["cost"]["by_minute"].items()) or "No cost events"
    breakdown = ", ".join(f"{name}: {count}" for name, count in summary["errors"]["breakdown"].items()) or "No failed requests"
    cards = "".join([
        card("Latency percentiles", "ms", "P95 ≤ 3000 ms", status(summary["latency"]["p95"], "lte", latency_limit), f'<div class="metrics"><b>P50 {summary["latency"]["p50"]:.0f}</b><b>P95 {summary["latency"]["p95"]:.0f}</b><b>P99 {summary["latency"]["p99"]:.0f}</b></div>'),
        card("Request traffic", "requests_per_minute", "Rate ≥ 1 request/min", status(summary["traffic"]["per_minute"], "gte", traffic_limit), f'<div class="metrics"><b>{summary["traffic"]["count"]} requests</b><b>{summary["traffic"]["per_minute"]:.2f} / min</b></div>'),
        card("Error rate and breakdown", "percent", "Error rate ≤ 2%", status(summary["errors"]["rate"], "lte", error_limit), f'<div class="metrics"><b>{summary["errors"]["rate"]:.2f}%</b></div><p>Breakdown: {html.escape(breakdown)}</p>'),
        card("Cost over time", "usd", "Total ≤ $2.50", status(summary["cost"]["total"], "lte", cost_limit), f'<div class="metrics"><b>${summary["cost"]["total"]:.4f} total</b></div><p class="series">{html.escape(cost_points)}</p>'),
        card("Input and output tokens", "tokens", "Total ≤ 50,000", status(summary["tokens"]["input"] + summary["tokens"]["output"], "lte", token_limit), f'<div class="metrics"><b>Input {summary["tokens"]["input"]:,.0f}</b><b>Output {summary["tokens"]["output"]:,.0f}</b></div>'),
        card("Quality proxy", "score_0_to_1", "Mean ≥ 0.75", status(summary["quality"], "gte", quality_limit), f'<div class="metrics"><b>{summary["quality"]:.2f}</b></div>'),
    ])
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="refresh" content="30"><title>{html.escape(dashboard["title"])}</title><style>
    body{{font-family:Arial,sans-serif;margin:0;background:#0b1120;color:#e5e7eb}} main{{max-width:1200px;margin:auto;padding:30px}} .meta{{color:#94a3b8}} .grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}}.card{{background:#172033;border:1px solid #334155;border-left:7px solid #22c55e;border-radius:10px;padding:18px}}.card.bad{{border-left-color:#ef4444}}h2{{font-size:18px;margin:0 0 8px}}.threshold,.unit{{color:#94a3b8;font-size:13px}}.metrics{{display:flex;gap:14px;flex-wrap:wrap;font-size:20px;margin:18px 0}}.series{{font-family:monospace;font-size:12px;color:#cbd5e1}}@media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}
    </style></head><body><main><h1>{html.escape(dashboard["title"])}</h1><p class="meta">Window: {dashboard["time_range_minutes"]} minutes · Refresh: {dashboard["refresh_seconds"]} seconds · Latest log: {html.escape(summary["latest"])}</p><p class="meta">Valid records in window: {summary["records"]}</p><div class="grid">{cards}</div></main></body></html>'''


def make_handler(log_path: Path, config_path: Path):
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/data":
                dashboard = yaml.safe_load(config_path.read_text(encoding="utf-8"))["dashboard"]
                summary = summarise(load_records(log_path), dashboard["time_range_minutes"])
                payload = json.dumps(summary, default=str).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(payload)
                return

            if self.path not in {"/", "/index.html"}:
                # Try to serve the HTML file for the new dashboard
                self.send_error(404)
                return

            # Serve the premium HTML dashboard
            if HTML_PATH.exists():
                page = HTML_PATH.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
            else:
                # Fallback to inline rendered dashboard
                dashboard = yaml.safe_load(config_path.read_text(encoding="utf-8"))["dashboard"]
                page = render(summarise(load_records(log_path), dashboard["time_range_minutes"]), dashboard).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)

        def log_message(self, _: str, *args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            if self.path.startswith("/api/incidents/"):
                import urllib.request
                import urllib.error
                target_url = "http://127.0.0.1:8000/incidents/" + self.path[len("/api/incidents/"):]
                req = urllib.request.Request(target_url, method="POST")
                try:
                    with urllib.request.urlopen(req) as response:
                        body = response.read()
                        self.send_response(response.status)
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(body)
                except urllib.error.HTTPError as e:
                    self.send_response(e.code)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(e.read())
                except Exception as e:
                    self.send_error(500, str(e))
                return
            self.send_error(404)

    return DashboardHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the Day 13 six-panel dashboard")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(LOG_PATH, CONFIG_PATH))
    print(f"Dashboard: http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
