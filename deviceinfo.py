import logging
import socket

logger = logging.getLogger(__name__)


def _read_text(path):
    try:
        with open(path, "rb") as file:
            # /proc/device-tree strings end with a NUL byte
            return file.read().decode(errors="replace").strip("\x00\n ")
    except OSError:
        return None


def _cpuinfo_field(name):
    text = _read_text("/proc/cpuinfo") or ""
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == name:
            return value.strip()
    return None


def _os_name():
    text = _read_text("/etc/os-release") or ""
    for line in text.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip('"')
    return None


def get_ip_address():
    """IP of the interface used for the default route, or None.

    Connecting a UDP socket sends no packet; it only makes the kernel pick the
    outgoing interface. It fails (OSError) when the Pi has no network route.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            return sock.getsockname()[0]
    except OSError:
        return None


def _static_info():
    return {
        "model": _read_text("/proc/device-tree/model"),
        "serial": _cpuinfo_field("Serial"),
        "hostname": socket.gethostname(),
        "os": _os_name(),
    }


# Model, serial, hostname and OS do not change while the program runs
_STATIC_INFO = _static_info()


def get_device_info():
    """Raspberry Pi identification: model, serial, hostname, OS and current IP."""
    return {**_STATIC_INFO, "ip": get_ip_address()}
