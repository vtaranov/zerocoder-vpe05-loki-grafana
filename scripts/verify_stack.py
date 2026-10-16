"""Functional checks against the deployed stack; no third party packages.

Run on the Docker host: python3 scripts/verify_stack.py
Admin credentials are read from .env and never printed.
"""
import base64
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
config = {}
if (ROOT / ".env").exists():
    for line in (ROOT / ".env").read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            config[key] = value
GRAFANA = "http://127.0.0.1:" + config.get("GRAFANA_PORT", "3000")
LOKI = "http://127.0.0.1:" + config.get("LOKI_PORT", "3100")
APP = "http://127.0.0.1:" + config.get("APP_PORT", "18085")
password = config.get("GRAFANA_ADMIN_PASSWORD", os.environ.get("GRAFANA_ADMIN_PASSWORD", ""))
user = config.get("GRAFANA_ADMIN_USER", "admin")
auth = "Basic " + base64.b64encode((user + ":" + password).encode()).decode()


def request(url, *, admin=False, status=200):
    headers = {"Authorization": auth} if admin else {}
    try:
        response = urlopen(Request(url, headers=headers), timeout=10)
    except HTTPError as error:
        response = error
    with response:
        body = response.read()
        assert response.status == status, (url, response.status, body[:300])
    try:
        return json.loads(body)
    except ValueError:
        return body.decode()


def wait_ready(url):
    deadline = time.monotonic() + 180
    while True:
        try:
            return request(url)
        except (AssertionError, URLError, TimeoutError, OSError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)


def main():
    wait_ready(LOKI + "/ready")
    grafana = wait_ready(GRAFANA + "/api/health")
    wait_ready(APP + "/health")
    print("PASS: Loki /ready, Grafana /api/health, demo /health")
    datasource = request(GRAFANA + "/api/datasources/uid/loki", admin=True)
    assert datasource["url"] == "http://loki:3100"
    assert datasource["type"] == "loki"
    print("PASS: datasource Loki -> http://loki:3100")
    ids = []
    for endpoint, code, level in [("info", 200, "INFO"), ("warning", 200, "WARNING"), ("error", 503, "ERROR")]:
        event = request(APP + "/api/" + endpoint, status=code)
        assert event["log_sent"] is True, event
        assert event["level"] == level
        ids.append(event["request_id"])
    request(APP + "/nonexistent", status=404)
    print("PASS: INFO, WARNING, ERROR via application -> Loki push HTTP 204; unknown route HTTP 404")
    health = request(GRAFANA + "/api/datasources/uid/loki/health", admin=True)
    assert health["status"] == "OK", health
    print("PASS: datasource health (same backend check as Save & test):", health["message"])
    selector = '{app="vpe05-demo"}'
    query = request(LOKI + "/loki/api/v1/query_range?" + urlencode({"query": selector, "since": "5m", "limit": 1000, "direction": "backward"}))
    assert query["status"] == "success"
    values = [item for stream in query["data"]["result"] for item in stream["values"]]
    found = {json.loads(message)["request_id"] for _, message in values}
    assert set(ids) <= found, (ids, found)
    print("PASS: all three unique request IDs found in Loki (end-to-end)")
    counts = request(LOKI + "/loki/api/v1/query?" + urlencode({"query": 'sum by (level) (count_over_time(' + selector + '[15m]))'}))
    levels = {item["metric"]["level"]: int(float(item["value"][1])) for item in counts["data"]["result"]}
    assert {"INFO", "WARNING", "ERROR"} <= levels.keys(), levels
    print("PASS: LogQL pie chart counts:", json.dumps(levels, sort_keys=True))
    for level in ("INFO", "WARNING", "ERROR"):
        filtered = request(LOKI + "/loki/api/v1/query_range?" + urlencode({"query": '{app="vpe05-demo", level="' + level + '"}', "since": "5m", "limit": 50}))
        assert filtered["data"]["result"]
        assert all(row["stream"]["level"] == level for row in filtered["data"]["result"])
    print("PASS: all three level filters return the correct streams")
    # Anonymous users can view dashboards but cannot create/edit one.
    board = request(GRAFANA + "/api/dashboards/uid/vpe05-logs")
    assert {panel["type"] for panel in board["dashboard"]["panels"]} == {"table", "piechart"}
    assert board["meta"]["canSave"] is False
    print("PASS: dashboard has table + pie chart, anonymous access is Viewer")
    print("Grafana version:", grafana["version"])
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
