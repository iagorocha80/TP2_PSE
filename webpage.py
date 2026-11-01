import threading

from flask import Flask, Response, jsonify

from readHCSR import read_distance_cm

app = Flask(__name__)

POLL_INTERVAL_SECONDS = 1
_reading_lock = threading.Lock()
_worker_start_lock = threading.Lock()
_worker_wait = threading.Event()
_sensor_thread = None
_latest_reading = {
    "distance_cm": None,
    "error": None,
}

PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Distance monitor</title>
  <style>
    html, body {
      width: 100%;
      height: 100%;
      margin: 0;
    }
    body {
      display: grid;
      place-items: center;
      background: white;
      font: 1.25rem sans-serif;
    }
    #reading {
      box-sizing: border-box;
      min-width: 16rem;
      padding: 2rem;
      background: blue;
      color: white;
      text-align: center;
    }
  </style>
</head>
<body>
  <main id="reading" aria-live="polite">Waiting for sensor...</main>
  <script>
    const banner = document.getElementById("reading");

    async function updateReading() {
      try {
        const response = await fetch("/api/data", { cache: "no-store" });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();
        const text = data.error
          || (data.distance_cm === null
            ? "Waiting for sensor..."
            : `${Number(data.distance_cm).toFixed(1)} cm`);

        if (banner.textContent !== text) {
          banner.textContent = text;
        }
      } catch (error) {
        const text = `Web error: ${error.message}`;
        if (banner.textContent !== text) {
          banner.textContent = text;
        }
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

    while True:
        try:
            distance_cm = read_distance_cm()
        except (TimeoutError, ValueError, OSError, RuntimeError) as error:
            reading = {"distance_cm": None, "error": str(error)}
        else:
            reading = {"distance_cm": distance_cm, "error": None}

        with _reading_lock:
            _latest_reading = reading

        _worker_wait.wait(POLL_INTERVAL_SECONDS)


def start_sensor_worker():
    global _sensor_thread

    with _worker_start_lock:
        if _sensor_thread is None or not _sensor_thread.is_alive():
            _sensor_thread = threading.Thread(target=_sensor_worker, daemon=True)
            _sensor_thread.start()


@app.get("/")
def index():
    start_sensor_worker()
    return Response(PAGE, mimetype="text/html")


@app.get("/api/data")
def api_data():
    start_sensor_worker()
    with _reading_lock:
        reading = _latest_reading.copy()
    return jsonify(reading)


if __name__ == "__main__":
    start_sensor_worker()
    app.run(host="0.0.0.0", port=5000, debug=False)