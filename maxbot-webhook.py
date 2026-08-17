#!/usr/bin/env python3
"""Production webhook transport for MAX bot.

Keeps the approved business logic in maxbot-acceptance-v2.py and replaces
Long Polling with the production Webhook delivery model required by MAX.
Standard library only.
"""

import hmac
import importlib.util
import json
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CORE_PATH = os.environ.get(
    "MAXBOT_CORE",
    os.path.join(BASE_DIR, "maxbot-acceptance-v2.py"),
)
SECRET_PATH = os.environ.get("MAXBOT_WEBHOOK_SECRET_FILE", "/root/.max_webhook_secret")
LISTEN_HOST = os.environ.get("MAXBOT_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("MAXBOT_LISTEN_PORT", "8787"))
MAX_BODY_BYTES = 1024 * 1024


def load_core():
    spec = importlib.util.spec_from_file_location("maxbot_core", CORE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load bot core: {CORE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bot = load_core()

# Use our own GitHub Pages images instead of signed VK CDN URLs.
# All files 1.jpg ... 20.jpg already exist in the site's repository.
bot.PHOTO_URLS = [
    f"https://portfolio-maxdizain.ru/images/kitchens/{number}.jpg"
    for number in range(1, 21)
]

with open(SECRET_PATH, "r", encoding="utf-8") as secret_file:
    WEBHOOK_SECRET = secret_file.read().strip()

if len(WEBHOOK_SECRET) < 5:
    raise RuntimeError("MAX webhook secret is missing or too short")

updates_queue = queue.Queue(maxsize=1000)


def worker():
    while True:
        update = updates_queue.get()
        try:
            bot.handle_update(update)
        except Exception as exc:
            print("WEBHOOK UPDATE ERROR", repr(exc), flush=True)
        finally:
            updates_queue.task_done()


class Handler(BaseHTTPRequestHandler):
    server_version = "MaximumFurnitureBot/1.0"

    def log_message(self, fmt, *args):
        print("WEB", fmt % args, flush=True)

    def _write(self, status, body=b"OK", content_type="text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._write(200, b"MAXBOT OK")
            return
        self._write(404, b"Not found")

    def do_POST(self):
        if self.path != "/webhook":
            self._write(404, b"Not found")
            return

        received_secret = self.headers.get("X-Max-Bot-Api-Secret", "")
        if not hmac.compare_digest(received_secret, WEBHOOK_SECRET):
            self._write(403, b"Forbidden")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._write(400, b"Bad Content-Length")
            return

        if length <= 0 or length > MAX_BODY_BYTES:
            self._write(413, b"Invalid body size")
            return

        try:
            update = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._write(400, b"Bad JSON")
            return

        try:
            updates_queue.put_nowait(update)
        except queue.Full:
            # A non-200 result asks MAX to retry instead of silently losing the event.
            self._write(503, b"Busy")
            return

        # MAX requires a successful response quickly; processing happens in worker().
        self._write(200, b"OK")


def main():
    threading.Thread(target=worker, name="maxbot-worker", daemon=True).start()
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    print(f"MAXBOT WEBHOOK START http://{LISTEN_HOST}:{LISTEN_PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
