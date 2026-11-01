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
  </style>
</head>
<body>
  <main class="monitor" aria-label="Distance monitor">
    <header class="monitor-header">Distance Monitor</header>
    <section class="reading" aria-live="polite">
      <div class="reading-label">Distance</div>
      <div id="reading-value">Waiting for sensor...</div>
    </section>
  </main>
  <script>
    const readingValue = document.getElementById("reading-value");

    async function updateReading() {
      try {
        const response = await fetch("/api/data", { cache: "no-store" });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();
        const hasError = Boolean(data.error);
        const text = hasError
          ? data.error
          : (data.distance_cm === null
            ? "Waiting for sensor..."
            : `${Number(data.distance_cm).toFixed(1)} cm`);

        if (readingValue.textContent !== text) {
          readingValue.textContent = text;
        }
        readingValue.classList.toggle("error", hasError);
      } catch (error) {
        const text = `Web error: ${error.message}`;
        if (readingValue.textContent !== text) {
          readingValue.textContent = text;
        }
        readingValue.classList.add("error");
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