# GPS/Compass Telemetry via RS232 USB-Serial Adapter

## Overview

The STM32 (GPS + compass) now communicates with the Pi over a **true RS232**
link rather than the Pi's onboard GPIO UART.  A USB-RS232 adapter converts
RS232 levels to USB and enumerates as a `/dev/ttyUSBx` device on the Pi.

The Pi's GPIO UART (`/dev/ttyAMA0`, pins 14/15) is now **dedicated to the
motor-control link** (Pi → Arduino) — see `navigation/motor_writer.py`.

## Wiring

```
STM32 TX ──► RS232-level TX ──► USB-RS232 adapter ──► Pi USB port
STM32 RX ◄── RS232-level RX ◄── USB-RS232 adapter ◄── Pi USB port
STM32 GND ── RS232 GND ──────── USB-RS232 adapter ──── Pi GND
```

## ⚠️ Hardware checkpoint: RS232 voltage levels

The STM32's UART pins output **3.3V TTL** (0–3.3 V).  True RS232 uses
**±3–15 V** signalling.  A **MAX3232-class level converter** (or an
ST3232, SP3232, etc.) **must** be placed between the STM32 TX/RX pins
and the DB9 connector of a standard USB-RS232 adapter.

If your USB-RS232 adapter has a DB9 female connector:

```
STM32 TX (3.3V TTL) ──► MAX3232 TTL-in ──► MAX3232 RS232-out ──► DB9 pin 2 (RX)
STM32 RX (3.3V TTL) ◄── MAX3232 TTL-out ◄── MAX3232 RS232-in  ◄── DB9 pin 3 (TX)
GND ────────────────── MAX3232 GND ─────────────────────────────── DB9 pin 5 (GND)
```

Some USB-RS232 adapters (e.g. FTDI-based) can be configured for 3.3V TTL
levels on their header pins, but **do not assume this** — verify with a
multimeter or oscilloscope before connecting.

If your USB-RS232 adapter is actually a **USB-TTL** adapter (3.3V signal,
no DB9), you may not need a MAX3232, but then it is TTL, not RS232.
The code is agnostic to this — it uses `pyserial` either way — but the
wiring and voltage levels must match the hardware you have.

## Baud rate and byte format

| Parameter    | Value   | Notes                           |
|--------------|---------|---------------------------------|
| Baud rate    | 115200  | Confirmed with STM32 firmware   |
| Data bits    | 8       | Standard                        |
| Parity       | None    |                                 |
| Stop bits    | 1       |                                 |
| Flow control | None    |                                 |

The STM32 firmware must be compiled for the same settings.

## Frame format (unchanged)

```
LAT,LNG,HEADING\n
37.774900,-122.419400,045.3\n
```

One line per update, 10 Hz recommended.  LAT in range [-90, 90], LNG in
range [-180, 180], HEADING in degrees [0, 360).  No leading/trailing
whitespace.

## Environment variables

| Variable                | Default       | Description                                     |
|-------------------------|---------------|-------------------------------------------------|
| `AERONAV_GPS_PORT`      | `/dev/ttyUSB0` | Serial device path for the USB-RS232 adapter    |
| `AERONAV_GPS_BAUD`      | `115200`       | Baud rate matching STM32 firmware               |
| `AERONAV_USE_REAL_TELEMETRY` | `0`       | Set to `1` to use real hardware (not mock)      |

## udev rules for a stable device symlink

USB-serial adapters do not reliably keep the same `/dev/ttyUSBx` number
across reboots or replugs.  Create a udev rule that assigns a fixed
symlink (e.g. `/dev/aeronav-gps`) based on the adapter's USB vendor and
product IDs.

### Step 1 — Identify the adapter

Plug in the USB-RS232 adapter and run:

```bash
lsusb
udevadm info -a -n /dev/ttyUSB0 | grep -i "idVendor\|idProduct"
```

Note the `idVendor` and `idProduct` values (e.g. `1a86` and `7523` for a
common CH340-based adapter).

### Step 2 — Create the udev rule

Create `/etc/udev/rules.d/99-aeronav-gps.rules` as root:

```bash
sudo nano /etc/udev/rules.d/99-aeronav-gps.rules
```

With content:

```
# USB-RS232 adapter for AeroNav GPS/compass telemetry
# Replace idVendor and idProduct with your adapter's values
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", SYMLINK+="aeronav-gps"
```

### Step 3 — Reload and test

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
ls -l /dev/aeronav-gps
```

You should see a symlink: `lrwxrwxrwx 1 root root 7 ... /dev/aeronav-gps -> ttyUSB0`.

### Step 4 — Point the env var at the symlink

```bash
export AERONAV_GPS_PORT=/dev/aeronav-gps
```

Add this to `/etc/environment` or to the systemd service file for the
AeroNav backend so it is always set at boot.

## Permissions

The `pyserial` library needs read/write access to the serial device.  On
Raspberry Pi OS, the running user must be in the `dialout` group:

```bash
sudo usermod -a -G dialout $USER
# log out and back in for the group change to take effect
```

This applies to **both**:
- `/dev/ttyUSB0` (or the stable symlink `/dev/aeronav-gps`) for the
  USB-RS232 GPS/compass link
- `/dev/ttyAMA0` for the Pi GPIO UART motor-control link to the Arduino
