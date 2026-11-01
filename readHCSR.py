
import RPi.GPIO as GPIO
import time

# Set GPIO Pins (BCM numbering)
TRIG = 4
ECHO = 17
ECHO_TIMEOUT = 0.04

print("Distance Measurement In Progress")


def setup_gpio():
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(TRIG, GPIO.OUT)
    GPIO.setup(ECHO, GPIO.IN)
    GPIO.output(TRIG, False)


def cleanup_gpio():
    GPIO.cleanup()


def distance():
    # Set TRIG to LOW
    GPIO.output(TRIG, False)
    time.sleep(1)

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

    return distance

if __name__ == '__main__':
    try:
        while True:
            try:
                setup_gpio()
                dist = distance()
                print("Measured Distance = %.1f cm" % dist)
            except TimeoutError as error:
                print("Measurement failed:", error)
            finally:
                cleanup_gpio()
            time.sleep(1)
    except KeyboardInterrupt:
        print("Measurement stopped by User")
