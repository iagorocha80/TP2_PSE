
import atexit
import logging
import statistics
import time

import RPi.GPIO as GPIO

logger = logging.getLogger(__name__)

# Set GPIO Pins (BCM numbering)
TRIG = 4
ECHO = 17
ECHO_TIMEOUT = 0.04
# The datasheet gives 2 cm as the minimum range, but with an object touching
# the sensor it reported 2.4-4.4 cm (dead zone and multiple reflections),
# so nothing closer than 5 cm is trusted.
MIN_DISTANCE_CM = 5
MAX_DISTANCE_CM = 450
MEDIAN_SAMPLES = 5
# A real target gives samples within a few mm of each other (std dev 0.08 cm
# measured); a wider spread or too few valid samples means an unreliable reading.
MIN_VALID_SAMPLES = 3
MAX_SAMPLE_SPREAD_CM = 2
# Idle time between measurements: lets echoes of the previous burst die out
# (the HC-SR04 datasheet recommends >= 60 ms between cycles).
SETTLE_TIME = 0.06

_gpio_ready = False


def setup_gpio():
    """Configure the pins once; later calls do nothing.

    Cleanup is registered with atexit so the pins are released when the
    program ends, including on Ctrl+C.
    """
    global _gpio_ready
    if _gpio_ready:
        return

    GPIO.setmode(GPIO.BCM)
    GPIO.setup(TRIG, GPIO.OUT)
    GPIO.setup(ECHO, GPIO.IN)
    GPIO.output(TRIG, False)
    _gpio_ready = True
    atexit.register(cleanup_gpio)
    logger.info("GPIO initialised (TRIG=BCM%d, ECHO=BCM%d)", TRIG, ECHO)


def cleanup_gpio():
    global _gpio_ready
    if _gpio_ready:
        GPIO.cleanup()
        _gpio_ready = False
        logger.info("GPIO released")


def distance():
    # Set TRIG to LOW
    GPIO.output(TRIG, False)
    time.sleep(SETTLE_TIME)

    # Send 10us pulse to TRIG
    GPIO.output(TRIG, True)
    time.sleep(0.00001)
    GPIO.output(TRIG, False)

    deadline = time.monotonic() + ECHO_TIMEOUT
    while GPIO.input(ECHO) == 0:
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out waiting for ECHO to go HIGH")
    start_time = time.monotonic()

    deadline = start_time + ECHO_TIMEOUT
    while GPIO.input(ECHO) == 1:
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out waiting for ECHO to go LOW")
    stop_time = time.monotonic()

    # Time difference between start and arrival
    time_elapsed = stop_time - start_time
    # Multiply with speed of sound (34300 cm/s)
    # and divide by 2, because there and back
    distance = (time_elapsed * 34300) / 2

    if not MIN_DISTANCE_CM <= distance <= MAX_DISTANCE_CM:
        raise ValueError(
            f"Distance out of bounds: {distance:.1f} cm "
            f"(valid range: {MIN_DISTANCE_CM}-{MAX_DISTANCE_CM} cm)"
        )

    return distance


def median_distance(samples=MEDIAN_SAMPLES):
    """Median of several measurements, which rejects isolated outliers.

    Failed samples are logged and skipped. If none succeeds, the last error
    is raised so the caller sees why the sensor failed. If too few samples
    are valid or they disagree too much, the reading is rejected with a
    ValueError instead of returning a number that cannot be trusted.
    """
    values = []
    last_error = None
    for _ in range(samples):
        try:
            values.append(distance())
        except (TimeoutError, ValueError) as error:
            last_error = error
            logger.debug("Discarded sample: %s", error)

    if not values:
        raise last_error

    if len(values) < MIN_VALID_SAMPLES:
        raise ValueError(
            f"Unreliable reading: only {len(values)} of {samples} samples valid "
            f"(need {MIN_VALID_SAMPLES})"
        )
    spread = max(values) - min(values)
    if spread > MAX_SAMPLE_SPREAD_CM:
        raise ValueError(
            f"Unstable reading: samples differ by {spread:.1f} cm "
            f"(max {MAX_SAMPLE_SPREAD_CM} cm)"
        )

    result = statistics.median(values)
    logger.debug("Median %.1f cm from %d/%d valid samples", result, len(values), samples)
    return result


def read_distance_cm():
    """Read one filtered distance in centimetres.

    Initialises the GPIO on first use, so callers do not need to know about it.
    """
    setup_gpio()
    return median_distance()


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    print("Distance Measurement In Progress")
    setup_gpio()
    try:
        while True:
            try:
                dist = read_distance_cm()
                print("Measured Distance = %.1f cm" % dist)
            except (TimeoutError, ValueError) as error:
                logger.warning("Sensor error: %s", error)
            time.sleep(1)
    except KeyboardInterrupt:
        print("Measurement stopped by User")
    finally:
        cleanup_gpio()
