# Raspberry Pi distance monitor (HC-SR04 + OLED + web page)

Measures distance with an HC-SR04 and shows it on a 128x64 I2C OLED and on a
web page served by the Raspberry Pi.

## Hardware

| Part | Raspberry Pi pin |
|---|---|
| HC-SR04 TRIG | BCM 4 (pin 7) |
| HC-SR04 ECHO | BCM 17 (pin 11), through a divider of two 100 kOhm resistors |
| OLED SDA / SCL | BCM 2 / BCM 3 (pins 3 / 5), I2C bus 1, address 0x3C |
| OLED VCC | 3.3 V (pin 17) |
| GND (sensor, divider, OLED) | pin 6, 9 or 14 |

Enable I2C once (`sudo raspi-config` > Interface Options > I2C) and make sure
the user is in the `gpio` and `i2c` groups. `python3 scan_i2c.py` lists the
I2C addresses found (the OLED should show up as 0x3C).

## Run

```
./run.sh
```

This creates `.venv`, installs `requirements.txt` and starts both programs.
`RPi.GPIO` must already be installed (it is on Raspberry Pi OS; otherwise
`sudo apt install python3-rpi.gpio`).
Open `http://<raspberry-pi-ip>:5000` from another device on the network.
`./run.sh web` and `./run.sh display` start only one of them.

If the OLED shows shifted text or noise at the edges, its controller is an
SH1106: run `python3 writeDisplay.py --driver sh1106`.

## Files

| File | Role |
|---|---|
| `readHCSR.py` | HC-SR04 driver: TRIG pulse, ECHO timing, timeouts, validation, median of 5 samples |
| `webpage.py` | Flask server and sensor worker; the **only** process that reads the sensor |
| `deviceinfo.py` | Model, serial, hostname, OS and IP of the Raspberry Pi |
| `writeDisplay.py` | OLED output; reads `/api/data` from the web server |
| `scan_i2c.py` | I2C address scanner |
| `deploy/tp2-webpage.service` | Optional systemd unit to start the web server at boot |

## `/api/data`

`distance_cm` and `timestamp` are the last **valid** measurement and are kept
when a later one fails. `status` is `ok`, `error` (latest attempt failed, see
`error`), `stale` (no attempt finished recently) or `waiting`. Also returned:
`age_s`, `server_time`, `last_attempt`, `device` and the recent `log` entries.

If the server cannot start (for example port 5000 is busy), it writes the
reason to `server_status.json`, which the OLED program shows.

## Start at boot (optional)

Edit `User` and `WorkingDirectory` (and the `.venv` path in
`tp2-display.service`) to match the installation, then:

```
sudo cp deploy/tp2-webpage.service deploy/tp2-display.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tp2-webpage tp2-display
```

`tp2-webpage` uses the system `python3`, so Flask must be installed for it
(`sudo apt install python3-flask`). `tp2-display` uses the `.venv` created by
`./run.sh`. Logs: `sudo journalctl -u tp2-webpage -u tp2-display -f`.
