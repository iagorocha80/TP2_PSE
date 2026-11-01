"""Collect raw HC-SR04 timing data (TRIG pulse, ECHO latency and duration).

Uses the same sequence as readHCSR.distance(), instrumented with
time.perf_counter_ns(), and writes a CSV with every sample plus a text
summary (also printed) to the evidences folder, for the technical report.

Stop the web server first (sudo systemctl stop tp2-webpage): two processes
reading the sensor at the same time corrupt each other's ECHO pulses.
"""
import argparse
import csv
import os
import socket
import statistics
import time

import RPi.GPIO as GPIO

import readHCSR

SPEED_OF_SOUND_CM_S = 34300
DEFAULT_SAMPLES = 200
SERVER_PORT = 5000
CSV_FIELDS = [
    "sample", "status", "trig_pulse_us", "echo_latency_us", "echo_us",
    "polls_during_echo", "distance_cm", "cycle_ms",
]


def web_server_running():
    """True if something answers on the web server port (the sensor reader)."""
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", SERVER_PORT)) == 0


def measure_once():
    """One measurement; returns a dict with the timings in the CSV fields."""
    timeout_ns = int(readHCSR.ECHO_TIMEOUT * 1e9)
    row = {"status": "ok", "trig_pulse_us": None, "echo_latency_us": None,
           "echo_us": None, "polls_during_echo": None, "distance_cm": None}

    GPIO.output(readHCSR.TRIG, False)
    time.sleep(readHCSR.SETTLE_TIME)

    # TRIG pulse, with its real width (time.sleep(10 us) sleeps longer)
    pulse_start = time.perf_counter_ns()
    GPIO.output(readHCSR.TRIG, True)
    time.sleep(0.00001)
    GPIO.output(readHCSR.TRIG, False)
    pulse_end = time.perf_counter_ns()
    row["trig_pulse_us"] = (pulse_end - pulse_start) / 1000

    # Wait for ECHO to go HIGH (same loop as readHCSR.distance())
    deadline = pulse_end + timeout_ns
    while GPIO.input(readHCSR.ECHO) == 0:
        if time.perf_counter_ns() >= deadline:
            row["status"] = "timeout_rise"
            return row
    echo_start = time.perf_counter_ns()
    row["echo_latency_us"] = (echo_start - pulse_end) / 1000

    # Wait for ECHO to go LOW, counting the polling iterations
    polls = 0
    deadline = echo_start + timeout_ns
    while GPIO.input(readHCSR.ECHO) == 1:
        polls += 1
        if time.perf_counter_ns() >= deadline:
            row["status"] = "timeout_fall"
            return row
    echo_end = time.perf_counter_ns()

    row["echo_us"] = (echo_end - echo_start) / 1000
    row["polls_during_echo"] = polls
    row["distance_cm"] = row["echo_us"] / 1e6 * SPEED_OF_SOUND_CM_S / 2
    if not readHCSR.MIN_DISTANCE_CM <= row["distance_cm"] <= readHCSR.MAX_DISTANCE_CM:
        row["status"] = "out_of_range"
    return row


def describe(label, values, unit, decimals=1):
    """Text table row with mean, stdev, median, min and max."""
    if not values:
        return f"{label:<26}no data"
    stdev = statistics.stdev(values) if len(values) > 1 else 0
    cells = [statistics.mean(values), stdev, statistics.median(values), min(values), max(values)]
    return f"{label:<26}" + "".join(f"{value:>12.{decimals}f}" for value in cells) + f"  {unit}"


def build_summary(rows, args, started):
    ok = [row for row in rows if row["status"] == "ok"]
    statuses = {}
    for row in rows:
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1

    distances = [row["distance_cm"] for row in ok]
    echo_us = [row["echo_us"] for row in ok]
    polls = [row["polls_during_echo"] for row in ok]

    reference = "not set (static scene)"
    if args.reference_cm is not None:
        reference = f"{args.reference_cm:g} cm"
    lines = [
        "HC-SR04 ECHO timing measurement",
        "===============================",
        f"Date:               {started}",
        f"Samples:            {len(rows)} ({', '.join(f'{count} {name}' for name, count in statuses.items())})",
        f"Reference distance: {reference}",
        f"Speed of sound:     {SPEED_OF_SOUND_CM_S / 100:.0f} m/s",
        f"Settle time:        {readHCSR.SETTLE_TIME * 1000:.0f} ms before each TRIG pulse",
        f"ECHO timeout:       {readHCSR.ECHO_TIMEOUT * 1000:.0f} ms",
        f"Pins:               TRIG BCM {readHCSR.TRIG}, ECHO BCM {readHCSR.ECHO}",
        "",
        f"{'':<26}" + "".join(f"{name:>12}" for name in ("mean", "std dev", "median", "min", "max")),
        describe("Distance", distances, "cm", 2),
        describe("ECHO duration", echo_us, "us", 0),
        describe("ECHO latency", [row["echo_latency_us"] for row in ok], "us", 0),
        describe("TRIG pulse (req. 10 us)", [row["trig_pulse_us"] for row in rows], "us", 0),
        describe("Cycle time", [row["cycle_ms"] for row in rows], "ms", 1),
        "",
        "ECHO latency = TRIG falling edge to ECHO rising edge.",
    ]
    if ok:
        # Distance step produced by one polling iteration of the ECHO loop
        per_poll_us = statistics.mean(echo_us) / statistics.mean(polls)
        resolution_cm = per_poll_us / 1e6 * SPEED_OF_SOUND_CM_S / 2
        lines.append(
            f"Polling resolution: {statistics.mean(polls):.0f} loop iterations per pulse, "
            f"about {per_poll_us:.1f} us each, i.e. a distance step of about {resolution_cm:.2f} cm."
        )
    if distances and args.reference_cm is not None:
        error = statistics.mean(distances) - args.reference_cm
        lines.append(f"Mean error against the reference: {error:+.2f} cm")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--reference-cm", type=float,
                        help="real distance to the target, for the validation table")
    parser.add_argument("--outdir", default="evidences")
    args = parser.parse_args()

    if web_server_running():
        raise SystemExit(f"Something is listening on port {SERVER_PORT}: stop the web server "
                         "(sudo systemctl stop tp2-webpage) before measuring.")

    os.makedirs(args.outdir, exist_ok=True)
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    tag = time.strftime("%Y%m%d_%H%M%S")
    if args.reference_cm is not None:
        tag = f"{args.reference_cm:g}cm_{tag}"
    csv_path = os.path.join(args.outdir, f"echo_raw_{tag}.csv")
    summary_path = os.path.join(args.outdir, f"echo_summary_{tag}.txt")

    readHCSR.setup_gpio()
    rows = []
    try:
        for sample in range(1, args.samples + 1):
            cycle_start = time.perf_counter_ns()
            row = measure_once()
            row["sample"] = sample
            row["cycle_ms"] = (time.perf_counter_ns() - cycle_start) / 1e6
            rows.append(row)
    finally:
        readHCSR.cleanup_gpio()

    with open(csv_path, "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: (f"{value:.3f}" if isinstance(value, float) else value)
                for key, value in row.items()
            })
    summary = build_summary(rows, args, started)
    with open(summary_path, "w") as file:
        file.write(summary)
    print(summary)
    print(f"Saved {csv_path}\nSaved {summary_path}")


if __name__ == "__main__":
    main()
