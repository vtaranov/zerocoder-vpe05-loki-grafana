"""A small synthetic service sending structured logs straight to Loki; stdlib only."""
import json
import os
import random
import signal
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

LOKI_URL = os.environ.get("LOKI_URL", "http://localhost:3100/loki/api/v1/push")
STOP = threading.Event()
LEVELS = ("INFO", "WARNING", "ERROR")


def send_log_to_loki(message, level="INFO", route="background", request_id=None):
    """POST streams -> stream labels -> values[timestamp_ns_string, JSON_message].

    IDs and paths go into the JSON message, avoiding high-cardinality index labels.
    A logging outage returns False and never raises into the request handler.
    """
    if level not in LEVELS:
        raise ValueError("Unsupported log level")
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level, "message": message, "route": route,
        "request_id": request_id or uuid.uuid4().hex[:12],
    }
    payload = {"streams": [{
        "stream": {"app": "vpe05-demo", "level": level},
        "values": [[str(time.time_ns()), json.dumps(event, ensure_ascii=False)]],
    }]}
    request = Request(LOKI_URL, data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=2) as response:
            if response.status != 204:
                print(json.dumps({"logging_error": f"Loki HTTP {response.status}"}), flush=True)
                return False
        print(json.dumps(event, ensure_ascii=False), flush=True)
        return True
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        print(json.dumps({"logging_error": type(error).__name__, "event": event},
                         ensure_ascii=False), flush=True)
        return False


PAGE = """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VPe05 · Demo API</title><style>
body{font:17px system-ui;max-width:760px;margin:64px auto;padding:0 24px;background:#111827;color:#e5e7eb}
h1{font-size:36px}p{line-height:1.6;color:#cbd5e1}a{color:#93c5fd}li{margin:18px 0}
code{background:#1e293b;padding:4px 8px;border-radius:6px}small{color:#94a3b8}
</style><small>ZEROCODER / VPe05</small><h1>Мониторинг и логирование</h1>
<p>Тестовое приложение отправляет события напрямую в Loki. Это учебная симуляция:
действия не изменяют реальные заказы и не обращаются к сторонним сервисам.</p>
<ul><li><a href="/api/info">INFO — успешный запрос</a></li>
<li><a href="/api/warning">WARNING — медленный ответ внешнего сервиса</a></li>
<li><a href="/api/error">ERROR — имитация ошибки HTTP 503</a></li>
<li><a href="/health">Проверка состояния приложения</a></li></ul>
<p>В фоне каждые 15 секунд появляется новое событие. На дашборде Grafana
можно фильтровать логи по уровню и менять период.</p>
<script>document.write('<p><a href="'+location.protocol+'//'+location.hostname+':3000/d/vpe05-logs/vpe05-loki-grafana">Открыть дашборд Grafana →</a></p>')</script>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Health checks do not generate log noise.

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            return self.respond(200, {"status": "ok", "app": "vpe05-demo"})
        if path == "/":
            send_log_to_loki("Открыта главная страница", route=path)
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        routes = {
            "/api/info": (200, "INFO", "Запрос обработан успешно"),
            "/api/warning": (200, "WARNING", "Имитация: внешний сервис отвечает медленно"),
            "/api/error": (503, "ERROR", "Имитация: внешний сервис недоступен"),
        }
        status, level, message = routes.get(path, (404, "WARNING", "Маршрут не найден"))
        request_id = uuid.uuid4().hex[:12]
        sent = send_log_to_loki(message, level, path, request_id)
        self.respond(status, {"message": message, "level": level,
                              "request_id": request_id, "log_sent": sent})

    def respond(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def generate_logs():
    # The official Loki image is distroless. Check its readiness over HTTP here.
    ready_url = LOKI_URL.removesuffix("/loki/api/v1/push") + "/ready"
    while not STOP.is_set():
        try:
            with urlopen(ready_url, timeout=2) as response:
                if response.status == 200:
                    break
        except (URLError, TimeoutError, OSError):
            pass
        STOP.wait(2)
    # Emit all three levels on startup, then simulate one event per interval.
    messages = {
        "INFO": "Симуляция: заказ обработан успешно",
        "WARNING": "Симуляция: остаток товара ниже порога",
        "ERROR": "Симуляция: платёж отклонён тестовым провайдером",
    }
    for level in LEVELS:
        if STOP.is_set():
            return
        send_log_to_loki(messages[level], level)
    interval = max(5, float(os.environ.get("LOG_INTERVAL_SECONDS", "15")))
    while not STOP.wait(interval):
        level = random.choices(LEVELS, weights=(7, 2, 1))[0]
        send_log_to_loki(messages[level], level)


def main():
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    server.daemon_threads = True

    def shutdown(signum, frame):
        STOP.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, shutdown)
    worker = threading.Thread(target=generate_logs, daemon=True)
    worker.start()
    try:
        server.serve_forever()
    finally:
        STOP.set()
        server.server_close()
        worker.join(timeout=3)


if __name__ == "__main__":
    main()
