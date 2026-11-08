"""SSD1306 OLED driver over I2C using only the Linux I2C device.

No I2C library: like Arduino's Wire, a transfer is "select the slave address,
then write bytes". Here that is an ioctl on /dev/i2c-N followed by os.write().
Used by writeDisplay.py.

Every transfer starts with a control byte that tells the controller what the
following bytes are: 0x00 = commands, 0x40 = display data (pixels).
"""
import fcntl
import os

I2C_SLAVE = 0x0703
CONTROL_COMMAND = 0x00
CONTROL_DATA = 0x40
# The I2C write is cut in chunks so one transfer stays short
DATA_CHUNK = 32
PAGE_HEIGHT = 8


class RawOled:
    def __init__(self, port=1, address=0x3C, width=128, height=64):
        self.width = width
        self.height = height
        self.fd = os.open(f"/dev/i2c-{port}", os.O_RDWR)
        # From now on, writes on this file descriptor go to this device
        fcntl.ioctl(self.fd, I2C_SLAVE, address)
        self._initialise()

    def _command(self, *values):
        os.write(self.fd, bytes([CONTROL_COMMAND, *values]))

    def _initialise(self):
        """Standard start-up sequence of the SSD1306 with the internal charge pump."""
        self._command(0xAE)              # display off
        self._command(0xD5, 0x80)        # clock divider and oscillator frequency
        self._command(0xA8, self.height - 1)  # multiplex ratio: number of rows
        self._command(0xD3, 0x00)        # display offset: none
        self._command(0x40)              # start line 0
        self._command(0x8D, 0x14)        # enable the internal charge pump (no external VCC)
        self._command(0x20, 0x02)        # page addressing mode
        self._command(0xA1)              # segment remap: column 127 on the left
        self._command(0xC8)              # COM scan direction: flipped
        self._command(0xDA, 0x12)        # COM pins layout of a 128x64 panel
        self._command(0x81, 0xCF)        # contrast
        self._command(0xD9, 0xF1)        # pre-charge period
        self._command(0xDB, 0x40)        # VCOMH deselect level
        self._command(0xA4)              # show the RAM content
        self._command(0xA6)              # normal (not inverted) display
        self._command(0xAF)              # display on

    def _pages(self, image):
        """The image as one list of bytes per page.

        The screen memory is organised in pages of 8 rows. A page has one byte
        per column, and the least significant bit is the top pixel of the page.
        """
        pixels = image.load()
        pages = []
        for page in range(self.height // PAGE_HEIGHT):
            columns = []
            for x in range(self.width):
                byte = 0
                for bit in range(PAGE_HEIGHT):
                    if pixels[x, page * PAGE_HEIGHT + bit]:
                        byte |= 1 << bit
                columns.append(byte)
            pages.append(columns)
        return pages

    def display(self, image):
        """Show a 1-bit PIL image of exactly width x height pixels."""
        if image.mode != "1" or image.size != (self.width, self.height):
            raise ValueError(f"expected a 1-bit {self.width}x{self.height} image")

        for page, columns in enumerate(self._pages(image)):
            # Select the page and the first column (low and high nibble, both 0)
            self._command(0xB0 | page)
            self._command(0x00)
            self._command(0x10)
            for start in range(0, self.width, DATA_CHUNK):
                chunk = bytes(columns[start:start + DATA_CHUNK])
                os.write(self.fd, bytes([CONTROL_DATA]) + chunk)

    def close(self):
        """Turn the display off and release the device file."""
        try:
            self._command(0xAE)
        finally:
            os.close(self.fd)
