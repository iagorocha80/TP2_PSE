import collections
import json
import logging
import os
import socket
import sys
import threading
import time

from flask import Flask, Response, jsonify
from werkzeug.serving import make_server

from deviceinfo import get_device_info
from readHCSR import read_distance_cm

app = Flask(__name__)
logger = logging.getLogger(__name__)

HOST = "0.0.0.0"
PORT = 5000
# Written at startup so another process (the OLED script) can show why the
# server is not answering on /api/data, e.g. when the port is already in use.
SERVER_STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_status.json")
POLL_INTERVAL_SECONDS = 1
_reading_lock = threading.Lock()
_worker_start_lock = threading.Lock()
_worker_wait = threading.Event()
_sensor_thread = None
MAX_LOG_ENTRIES = 20
_log_lock = threading.Lock()
_log_entries = collections.deque(maxlen=MAX_LOG_ENTRIES)
# Consumers (web page, OLED script) treat the data as stale when no measurement
# attempt completed for this long, e.g. if the sensor worker is stuck.
STALE_AFTER_SECONDS = 5 * POLL_INTERVAL_SECONDS
# distance_cm and timestamp always refer to the last VALID measurement and are
# kept when a later attempt fails; error/last_attempt describe the latest attempt.
_latest_reading = {
    "distance_cm": None,
    "timestamp": None,
    "error": None,
    "last_attempt": None,
}


class WebLogHandler(logging.Handler):
    """Keeps the most recent log records so the web page can show them."""

    def emit(self, record):
        entry = {
            "time": time.strftime("%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        with _log_lock:
            _log_entries.append(entry)


# Attached to the loggers of both modules instead of the root logger, so that
# logging.basicConfig() in __main__ still sets up the console output.
_web_log_handler = WebLogHandler(level=logging.INFO)
logger.addHandler(_web_log_handler)
logging.getLogger("readHCSR").addHandler(_web_log_handler)

PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Distance monitor</title>
  <style>
    * {
      box-sizing: border-box;
    }
    body {
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
      padding: 1rem;
      background: #f4f4f4;
      color: #222;
      font-family: Arial, sans-serif;
    }
    .monitor {
      width: min(100%, 24rem);
      overflow: hidden;
      border: 1px solid #d5d5d5;
      border-radius: 0.75rem;
      background: #fff;
      box-shadow: 0 0.5rem 1.5rem rgb(0 0 0 / 12%);
    }
    .monitor-header {
      padding: 0.85rem 1rem;
      background: #315fc2;
      color: #fff;
      text-align: center;
      font-size: 1.1rem;
      font-weight: 600;
    }
    .monitor-source {
      margin-top: 0.15rem;
      font-size: 0.8rem;
      font-weight: 400;
      opacity: 0.85;
    }
    .device {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 0.2rem 0.75rem;
      margin: 0;
      padding: 0.6rem 1rem;
      border-top: 1px solid #d5d5d5;
      font-size: 0.8rem;
    }
    .device dt {
      color: #666;
    }
    .device dd {
      margin: 0;
      overflow-wrap: anywhere;
    }
    .reading {
      min-height: 9rem;
      display: grid;
      align-content: center;
      justify-items: center;
      gap: 0.55rem;
      padding: 1.5rem;
      text-align: center;
    }
    .reading-label {
      color: #666;
      font-size: 0.95rem;
    }
    #reading-value {
      font-size: clamp(2rem, 10vw, 2.5rem);
      font-weight: 700;
      overflow-wrap: anywhere;
    }
    #reading-value.error {
      color: #b42318;
      font-size: 1rem;
      font-weight: 600;
    }
    #reading-value.old {
      color: #888;
    }
    #reading-status {
      color: #666;
      font-size: 0.85rem;
    }
    #reading-status.error {
      color: #b42318;
    }
    .events {
      margin: 0;
      padding: 0.6rem 1rem;
      max-height: 10rem;
      overflow-y: auto;
      border-top: 1px solid #d5d5d5;
      list-style: none;
      font: 0.8rem monospace;
    }
    .events:empty {
      display: none;
    }
    .events li.WARNING, .events li.ERROR, .events li.CRITICAL {
      color: #b42318;
    }
  </style>
</head>
<body>
  <main class="monitor" aria-label="Distance monitor">
    <header class="monitor-header">
      Distance Monitor
      <div class="monitor-source">Raspberry Pi &middot; HC-SR04</div>
    </header>
    <section class="reading" aria-live="polite">
      <div class="reading-label">Distance</div>
      <div id="reading-value">Waiting for sensor...</div>
      <div id="reading-status"></div>
    </section>
    <dl id="device" class="device" aria-label="Raspberry Pi"></dl>
    <ul id="events" class="events" aria-label="Recent events"></ul>
  </main>
  <script>
    const readingValue = document.getElementById("reading-value");
    const readingStatus = document.getElementById("reading-status");
    const deviceList = document.getElementById("device");
    const eventsList = document.getElementById("events");

    function renderDevice(device) {
      const labels = [
        ["model", "Model"], ["hostname", "Hostname"], ["serial", "Serial"],
        ["ip", "IP"], ["os", "OS"],
      ];
      deviceList.replaceChildren(...labels.flatMap(([key, label]) => {
        const term = document.createElement("dt");
        term.textContent = label;
        const value = document.createElement("dd");
        value.textContent = device[key] || "unknown";
        return [term, value];
      }));
    }

    function renderEvents(entries) {
      // Newest first; textContent avoids interpreting messages as HTML
      eventsList.replaceChildren(...entries.slice().reverse().map((entry) => {
        const item = document.createElement("li");
        item.className = entry.level;
        item.textContent = `${entry.time} ${entry.level}: ${entry.message}`;
        return item;
      }));
    }

    async function updateReading() {
      try {
        const response = await fetch("/api/data", { cache: "no-store" });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();
        const hasValue = data.distance_cm !== null;
        const isOk = data.status === "ok";
        let text;
        let statusText = "";
        if (hasValue) {
          text = `${Number(data.distance_cm).toFixed(1)} cm`;
          statusText = isOk
            ? `Raspberry Pi \u00b7 HC-SR04 \u00b7 ${Math.round(data.age_s)} s ago`
            : `Last valid value, ${Math.round(data.age_s)} s ago`;
        } else {
          text = data.status === "waiting" ? "Waiting for sensor..." : data.error;
        }
        if (!isOk && data.status !== "waiting") {
          statusText = `${data.error || "Sensor data is stale"}` + (statusText ? ` \u2014 ${statusText}` : "");
        }

        readingValue.textContent = text;
        readingValue.classList.toggle("error", !hasValue && data.status !== "waiting");
        readingValue.classList.toggle("old", hasValue && !isOk);
        readingStatus.textContent = statusText;
        readingStatus.classList.toggle("error", !isOk && data.status !== "waiting");
        renderDevice(data.device || {});
        renderEvents(data.log || []);
      } catch (error) {
        const text = `Web error: ${error.message}`;
        if (readingValue.textContent !== text) {
          readingValue.textContent = text;
        }
        readingValue.classList.add("error");
        readingStatus.textContent = "";
      }
    }

    updateReading();
    window.setInterval(updateReading, 1000);
  </script>
</body>
</html>
"""


def _sensor_worker():
    global _latest_reading

    last_error = None
    while True:
        try:
            distance_cm = read_distance_cm()
        except Exception as error:
            if not isinstance(error, (TimeoutError, ValueError, OSError, RuntimeError)):
                logger.exception("Unexpected error in sensor worker")
            error_text = str(error)
            # Log only when the error changes, to avoid one line per second
            if error_text != last_error:
                logger.warning("Sensor error: %s", error_text)
            last_error = error_text
            update = {"error": error_text}
        else:
            if last_error is not None:
                logger.info("Sensor recovered: %.1f cm", distance_cm)
            last_error = None
            logger.debug("Distance: %.1f cm", distance_cm)
            update = {
                "distance_cm": distance_cm,
                "timestamp": time.time(),
                "error": None,
            }

        update["last_attempt"] = time.time()
        with _reading_lock:
            # Merge so a failed attempt keeps the last valid distance/timestamp
            _latest_reading = {**_latest_reading, **update}

        _worker_wait.wait(POLL_INTERVAL_SECONDS)


def start_sensor_worker():
    global _sensor_thread

    with _worker_start_lock:
        if _sensor_thread is None or not _sensor_thread.is_alive():
            _sensor_thread = threading.Thread(target=_sensor_worker, daemon=True)
            _sensor_thread.start()
            logger.info("Sensor worker started")


def _status(reading, now):
    """ok: latest attempt succeeded; error: it failed (distance_cm is the last
    valid value, if any); stale: no attempt finished recently (worker stuck or
    dead); waiting: nothing has been measured yet."""
    if reading["last_attempt"] is None:
        return "waiting"
    if now - reading["last_attempt"] > STALE_AFTER_SECONDS:
        return "stale"
    return "error" if reading["error"] else "ok"


@app.get("/")
def index():
    start_sensor_worker()
    return Response(PAGE, mimetype="text/html")


@app.get("/api/data")
def api_data():
    start_sensor_worker()
    with _reading_lock:
        reading = _latest_reading.copy()
    now = time.time()
    reading["server_time"] = now
    reading["age_s"] = None if reading["timestamp"] is None else now - reading["timestamp"]
    reading["status"] = _status(reading, now)
    reading["device"] = get_device_info()
    with _log_lock:
        reading["log"] = list(_log_entries)
    return jsonify(reading)


def write_server_status(state, error=None):
    """Atomically record the server state ("running" or "failed") in a file."""
    status = {"state": state, "error": error, "time": time.time(), "port": PORT}
    tmp_path = SERVER_STATUS_FILE + ".tmp"
    try:
        with open(tmp_path, "w") as file:
            json.dump(status, file)
        os.replace(tmp_path, SERVER_STATUS_FILE)
    except OSError as error:
        logger.error("Could not write %s: %s", SERVER_STATUS_FILE, error)


def run_server():
    # Binding happens here, so a busy port fails before the sensor worker starts.
    # werkzeug calls sys.exit(1) itself on EADDRINUSE, so probe the port first to
    # get a normal OSError with the real reason.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((HOST, PORT))
        server = make_server(HOST, PORT, app, threaded=True)
    except OSError as error:
        message = f"Web server could not start on port {PORT}: {error}"
        logger.error(message)
        write_server_status("failed", message)
        return 1

    start_sensor_worker()
    write_server_status("running")
    logger.info("Web server listening on %s:%d", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Web server stopped by user")
    except Exception as error:
        message = f"Web server stopped unexpectedly: {error}"
        logger.exception(message)
        write_server_status("failed", message)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    sys.exit(run_server())
