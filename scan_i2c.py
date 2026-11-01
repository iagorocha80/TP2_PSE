"""Scan an I2C bus and list the addresses that acknowledge.

Equivalent to `i2cdetect -y 1`, without extra dependencies: it opens the bus
device, selects each 7-bit address with the I2C_SLAVE ioctl and tries to read
one byte. A device that exists ACKs its address; otherwise the read fails.
"""
import fcntl
import os
import sys

I2C_SLAVE = 0x0703
FIRST_ADDRESS = 0x03
LAST_ADDRESS = 0x77

# Typical addresses of the devices used in this project
KNOWN_DEVICES = {
    0x3C: "OLED SSD1306/SH1106 (default address)",
    0x3D: "OLED SSD1306/SH1106 (alternative address)",
}


def scan(bus):
    path = f"/dev/i2c-{bus}"
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        sys.exit(f"{path} not found: enable I2C with 'sudo raspi-config' "
                 "(Interface Options > I2C) and reboot.")
    except PermissionError:
        sys.exit(f"No permission for {path}: add the user to the 'i2c' group "
                 "(sudo usermod -aG i2c $USER) and log in again.")

    found = []
    try:
        for address in range(FIRST_ADDRESS, LAST_ADDRESS + 1):
            try:
                fcntl.ioctl(fd, I2C_SLAVE, address)
                os.read(fd, 1)
            except OSError:
                continue
            found.append(address)
    finally:
        os.close(fd)
    return found


if __name__ == "__main__":
    bus = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"Scanning I2C bus {bus} (addresses 0x{FIRST_ADDRESS:02X}-0x{LAST_ADDRESS:02X})...")
    addresses = scan(bus)

    if not addresses:
        print("No device found. Check SDA/SCL, VCC, GND and that I2C is enabled.")
        sys.exit(1)

    for address in addresses:
        print(f"  0x{address:02X}  {KNOWN_DEVICES.get(address, 'unknown device')}")
