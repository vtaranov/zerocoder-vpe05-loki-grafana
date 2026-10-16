"""Restart only this Compose project and prove an earlier Loki event survives."""
import subprocess
from urllib.parse import urlencode
from verify_stack import ROOT, APP, LOKI, GRAFANA, request, wait_ready

marker = request(APP + "/api/info")["request_id"]
subprocess.run(["docker", "compose", "restart"], cwd=ROOT, check=True)
wait_ready(LOKI + "/ready")
wait_ready(GRAFANA + "/api/health")
wait_ready(APP + "/health")
result = request(LOKI + "/loki/api/v1/query_range?" + urlencode({
    "query": '{app="vpe05-demo"} |= "' + marker + '"', "since": "10m", "limit": 100,
}))
assert any(marker in message for stream in result["data"]["result"]
           for _, message in stream["values"]), "Event was lost after restart"
board = request(GRAFANA + "/api/dashboards/uid/vpe05-logs")
assert len(board["dashboard"]["panels"]) == 2
print("PASS: event", marker, "and dashboard survived Compose restart")
