# claude-stick

A desk-side Claude usage meter for the M5StickC Plus 1.1, inspired by [Clawdmeter](https://github.com/HermannBjorgvin/Clawdmeter) but pared down to fit the smaller display and the original ESP32 BLE stack.

## Hardware

- M5StickC Plus 1.1 (ESP32-PICO-D4, 1.14" 135×240 TFT)
- USB-C cable for power and flashing

## What it does

Displays Claude Code session and weekly utilisation as bar graphs. A laptop daemon polls Anthropic's API every 60 seconds and pushes the data over BLE. The two side buttons act as BLE HID shortcuts for Claude Code's voice mode (Space) and mode toggle (Shift+Tab). Three screens cycle via the front button:

1. Main: bars with reset countdowns
2. Detail: precise percentages and absolute reset times
3. Connection: MAC, BLE/WiFi state, battery, firmware

## Build

```bash
cd firmware
pio run -t upload
pio device monitor
```

See `firmware/platformio.ini` for dependencies.

## Status

Early development. The current build is a layout-verification stub with hard-coded sample data; BLE, WiFi/NTP, and daemon integration come next.

## Repository layout

```
.
├── README.md
├── .gitignore
└── firmware/
    ├── platformio.ini
    └── src/
        ├── config.h         constants, UUIDs, layout coords
        ├── state.h          AppState contract for other modules
        ├── state.cpp        STUB: hard-coded sample data
        ├── display.h        public init/tick/invalidate
        ├── display.cpp      three screen renderers + sprite buffer
        └── main.cpp         STUB: setup() + loop() with any-button cycle
```
