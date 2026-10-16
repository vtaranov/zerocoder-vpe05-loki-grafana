import contextlib
import io
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import app


class SenderTests(unittest.TestCase):
    @patch("app.urlopen")
    def test_loki_contract(self, mocked):
        mocked.return_value.__enter__.return_value.status = 204
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(app.send_log_to_loki("Тест", "WARNING", "/api/warning", "request-1"))
        request = mocked.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(mocked.call_args.kwargs["timeout"], 2)
        stream = json.loads(request.data)["streams"][0]
        self.assertEqual(stream["stream"], {"app": "vpe05-demo", "level": "WARNING"})
        timestamp, message = stream["values"][0]
        self.assertIsInstance(timestamp, str)
        self.assertGreater(int(timestamp), 10**18)
        event = json.loads(message)
        self.assertEqual(event["message"], "Тест")
        self.assertEqual(event["request_id"], "request-1")
        self.assertEqual(event["route"], "/api/warning")

    @patch("app.urlopen", side_effect=URLError("offline"))
    def test_loki_outage_is_nonfatal(self, mocked):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(app.send_log_to_loki("test"))

    @patch("app.urlopen")
    def test_unexpected_loki_status_is_failure(self, mocked):
        mocked.return_value.__enter__.return_value.status = 200
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(app.send_log_to_loki("test"))

    def test_invalid_level_is_rejected(self):
        with self.assertRaises(ValueError):
            app.send_log_to_loki("test", "ARBITRARY")


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    @patch("app.send_log_to_loki")
    def test_health_does_not_emit_logs(self, sender):
        with urlopen(self.base + "/health", timeout=3) as response:
            self.assertEqual(json.load(response)["status"], "ok")
        sender.assert_not_called()

    @patch("app.send_log_to_loki", return_value=True)
    def test_all_levels_and_404(self, sender):
        for route, status, level in [("info", 200, "INFO"), ("warning", 200, "WARNING"),
                                     ("error", 503, "ERROR"), ("unknown", 404, "WARNING")]:
            with self.subTest(route=route):
                try:
                    response = urlopen(self.base + "/api/" + route, timeout=3)
                except HTTPError as error:
                    response = error
                with response:
                    self.assertEqual(response.status, status)
                    payload = json.load(response)
                    self.assertEqual(payload["level"], level)
                    self.assertTrue(payload["log_sent"])
                self.assertEqual(sender.call_args.args[1], level)

    @patch("app.send_log_to_loki", return_value=False)
    def test_api_stays_available_when_logging_fails(self, sender):
        with urlopen(self.base + "/api/info", timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertFalse(json.load(response)["log_sent"])


if __name__ == "__main__":
    unittest.main()
