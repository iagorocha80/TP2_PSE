"""Show the distance measured by the web server on a 128x64 I2C OLED.

This script never touches the sensor: it only reads /api/data from the web
server (webpage.py), so there is a single reader of the HC-SR04. When the
server does not answer, it shows why, using server_status.json written by
webpage.py at startup.
"""
import argparse
import json
import logging
import os
import time
import urllib.error
import urllib.request

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

API_URL = "http://localhost:5000/api/data"
SERVER_STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_status.json")

I2C_PORT = 1
I2C_ADDRESS = 0x3C
WIDTH = 128
HEIGHT = 64

# The web server measures about once per 1.3 s, so redrawing faster only
# repeats the same value; 1 s keeps the age shown on the OLED up to date.
UPDATE_PERIOD_SECONDS = 1
REQUEST_TIMEOUT_SECONDS = 2
# Wait before trying to open the OLED again after an I2C failure
REOPEN_PERIOD_SECONDS = 5

FONT_DIR = "/usr/share/fonts/truetype/dejavu/"
FONT_REGULAR = FONT_DIR + "DejaVuSansMono.ttf"
FONT_BOLD = FONT_DIR + "DejaVuSansMono-Bold.ttf"


def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        # Pillow's built-in font, when the DejaVu fonts are not installed
        try:
            return ImageFont.load_default(size)
        except TypeError:
            return ImageFont.load_default()


FONT_SMALL = load_font(FONT_REGULAR, 9)
FONT_HEADER = load_font(FONT_BOLD, 10)
FONT_BIG = load_font(FONT_BOLD, 22)
FONT_MEDIUM = load_font(FONT_BOLD, 16)


def fetch_data(url):
    """Latest data from the web server, or raises OSError/ValueError."""
    with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.load(response)


def read_server_status():
    """Content of server_status.json, or None if it is missing or unreadable."""
    try:
        with open(SERVER_STATUS_FILE) as file:
            return json.load(file)
    except (OSError, ValueError):
        return None


def wrap_text(draw, text, font, max_lines):
    """Split text into at most max_lines lines that fit the display width."""
    lines = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) <= WIDTH or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "~"
    return lines


def draw_header(draw, left, right):
    draw.text((0, 0), left, font=FONT_HEADER, fill=1)
    right_width = draw.textlength(right, font=FONT_HEADER)
    draw.text((WIDTH - right_width, 0), right, font=FONT_HEADER, fill=1)
    draw.line((0, 12, WIDTH, 12), fill=1)


def draw_lines(draw, lines, top):
    for index, line in enumerate(lines):
        draw.text((0, top + index * 10), line, font=FONT_SMALL, fill=1)


def render_server_down(draw, reason):
    """No answer from the web server: show why, from server_status.json."""
    server_status = read_server_status()
    if server_status and server_status.get("state") == "failed":
        draw_header(draw, "Web server", "FAILED")
        text = server_status.get("error") or "Server failed to start"
    else:
        draw_header(draw, "Web server", "NO LINK")
        text = f"No answer from server: {reason}"
    draw_lines(draw, wrap_text(draw, text, FONT_SMALL, 4), 16)


def render_data(draw, data):
    status = data.get("status")
    distance_cm = data.get("distance_cm")
    age_s = data.get("age_s")
    device = data.get("device") or {}

    labels = {"ok": "OK", "error": "ERROR", "stale": "STALE", "waiting": "WAIT"}
    label = labels.get(status, "?")
    big_text = "-- cm" if distance_cm is None else f"{distance_cm:.1f} cm"

    if status == "error":
        # Smaller distance (the last valid one) to leave 3 lines for the error
        if age_s is not None:
            label = f"ERROR {age_s:.0f}s"
        draw_header(draw, "Distance", label)
        draw.text((0, 14), big_text, font=FONT_MEDIUM, fill=1)
        text = str(data.get("error"))
        draw_lines(draw, wrap_text(draw, text, FONT_SMALL, 3), 34)
        return

    draw_header(draw, "Distance", label)
    draw.text((0, 15), big_text, font=FONT_BIG, fill=1)

    if status == "stale":
        line = f"No update for {age_s:.0f} s" if age_s is not None else "No update"
    elif status == "waiting":
        line = "Waiting for sensor..."
    else:
        line = f"Updated {age_s:.0f} s ago" if age_s is not None else ""
    draw_lines(draw, [line, f"IP {device.get('ip') or 'unknown'}"], 43)


def render_frame(data, reason):
    """Build the 1-bit image to show. data is None when the server is down."""
    image = Image.new("1", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(image)
    if data is None:
        render_server_down(draw, reason)
    else:
        render_data(draw, data)
    return image


def open_display(driver, address):
    """Open the OLED over I2C, or return None (and log why) if that fails."""
    # Imported here so the rendering code can be tested without luma installed
    from luma.core.interface.serial import i2c
    from luma.oled.device import sh1106, ssd1306

    device_class = {"ssd1306": ssd1306, "sh1106": sh1106}[driver]
    try:
        serial = i2c(port=I2C_PORT, address=address)
        device = device_class(serial, width=WIDTH, height=HEIGHT)
    except Exception as error:
        # DeviceNotFoundError (no /dev/i2c-N), OSError (no ACK at the address)...
        logger.error("OLED not available at 0x%02X on I2C bus %d: %s", address, I2C_PORT, error)
        return None
    logger.info("OLED opened (%s, bus %d, address 0x%02X)", driver, I2C_PORT, address)
    return device


def run(args):
    device = None
    last_state = None

    while True:
        if device is None:
            device = open_display(args.driver, args.address)
            if device is None:
                time.sleep(REOPEN_PERIOD_SECONDS)
                continue

        try:
            data = fetch_data(args.url)
            reason = None
        except (OSError, ValueError) as error:
            # URLError and timeouts are OSError; invalid JSON is ValueError
            data = None
            reason = getattr(error, "reason", error)

        # Log only when the state changes, not once per refresh
        state = "down" if data is None else data.get("status")
        if state != last_state:
            if data is None:
                logger.warning("Web server not reachable: %s", reason)
            else:
                logger.info("Server data status: %s", state)
            last_state = state

        try:
            device.display(render_frame(data, reason))
        except OSError as error:
            logger.error("I2C write to the OLED failed: %s", error)
            device = None
            continue

        time.sleep(UPDATE_PERIOD_SECONDS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=API_URL, help="web server /api/data URL")
    parser.add_argument("--driver", choices=("ssd1306", "sh1106"), default="ssd1306")
    parser.add_argument("--address", type=lambda value: int(value, 0), default=I2C_ADDRESS)
    parser.add_argument("--preview", metavar="PNG",
                        help="save one frame as a PNG instead of using the OLED")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.preview:
        try:
            data, reason = fetch_data(args.url), None
        except (OSError, ValueError) as error:
            data, reason = None, getattr(error, "reason", error)
        render_frame(data, reason).resize((WIDTH * 4, HEIGHT * 4), Image.NEAREST).save(args.preview)
        print(f"Saved {args.preview}")
        return

    try:
        run(args)
    except KeyboardInterrupt:
        logger.info("Stopped by user")


if __name__ == "__main__":
    main()
