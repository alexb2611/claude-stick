# claude-stick Inky pHAT port — design

**Status:** approved, ready for implementation planning
**Date:** 2026-05-22
**Target hardware:** Raspberry Pi Zero 2 W + Pimoroni Inky pHAT V2 (`InkyPHAT_SSD1608`, 250×122 black-only)
**Host:** `pi@inkyzero.local`, Debian 13 trixie, Python 3.13, Pimoroni venv at `~/.virtualenvs/pimoroni/`

## 1. Context

The existing `claude-stick` project is an M5StickC Plus 1.1 firmware (ESP32, 240×135 colour TFT) that displays Claude Code usage from claude.ai. A laptop-side Python daemon polls `https://claude.ai/api/organizations/{org}/usage` and pushes the result to the stick over BLE. The stick also exposes BLE-HID side buttons for Space (voice mode) and Shift+Tab (mode toggle).

This document specifies a port of the same idea to a Pi + Inky pHAT. The hardware is fundamentally different (e-paper, monochrome, no buttons, no battery, mains-powered, full Linux) and several firmware concepts dissolve in the new context.

## 2. Goals & non-goals

**Goals.**
- Show Claude Code session and weekly utilisation on the Inky pHAT, refreshed automatically.
- Single Python service on the Pi that does the polling *and* the rendering — no second machine, no BLE link.
- Reuse the existing claude.ai-polling logic and session-config conventions verbatim where possible (the polling code in `daemon/claude_stick_daemon.py` is already battle-tested).
- Keep the existing M5StickC firmware and laptop daemon working untouched.
- Respect the e-paper panel's refresh cost: cap redraws far below the poll cadence.

**Non-goals.**
- BLE HID shortcuts (no buttons on this pHAT, and the Pi isn't tethered to a host as a HID device).
- Multi-screen cycling (no input mechanism; a single unified screen carries everything that matters).
- Battery / WiFi RSSI / MAC display (irrelevant on a Pi).
- Per-second countdown precision (e-paper can't visibly update at that rate, and the panel is for glance reading anyway).
- Refactoring the existing `daemon/` into a shared package — the duplicated polling code is ~80 lines and unlikely to drift.

## 3. Hardware constraints

Confirmed via SSH probe on 2026-05-22:

- **Display:** Inky pHAT V2, controller SSD1608, resolution 250×122, single colour channel (black). EEPROM at I²C 0x50 read via `inky.eeprom.read_eeprom()` returned `Display: 250x122, Color: black, PCB Variant: 1.2`.
- **GPIO buttons:** none. (This is the standard pHAT, not the 4-button Inky Impression.)
- **Refresh time:** approximately 3 seconds for a full refresh; quoted lifetime ~100 000 full refreshes.
- **Ink behaviour:** retains image essentially forever without power; ghosting accumulates with many partial refreshes against the same content.
- **SPI/I²C:** both enabled in `/boot/firmware/config.txt`.
- **Python environment:** `inky 2.4.0` is installed in `~/.virtualenvs/pimoroni/`, together with `Pillow`, `numpy`, `gpiod`, and the Hanken Grotesk font package (`font-hanken-grotesk`). The system Python does **not** have `inky` on its path — every invocation must use the venv's interpreter.

## 4. Architecture

A single asyncio Python service, `claude-stick-pi`, runs as a systemd user unit under `pi` with linger enabled.

```
            ┌────────────────────────────────────┐
            │  claude-stick-pi (single process)  │
            │                                    │
   60 s tick│  ┌─────────┐    ┌────────────┐     │
  ────────► │  │ poller  │──► │   state    │     │
            │  └─────────┘    └─────┬──────┘     │
            │                       │            │
            │                       ▼            │     SPI    ┌───────────┐
            │              ┌────────────────┐    │ ─────────► │ Inky pHAT │
            │              │   renderer     │────│            │  250×122  │
            │              └────────────────┘    │            └───────────┘
            └────────────────────────────────────┘
                  │                        ▲
                  ▼                        │
           journalctl                 ~/.config/claude-stick/session.json
```

Three internal modules + an orchestrator, deliberately mirroring the firmware's `state.h` / `display.cpp` split because that boundary worked well there:

- **`poller.py`** — pure I/O. `poll_usage(session_key, org_uuid) -> dict`. Direct port of the polling block in `daemon/claude_stick_daemon.py:171-260`, including its `status="net|auth|rate"` failure mapping. No BLE-related code.
- **`state.py`** — pure data. A `Snapshot` dataclass plus `visible_signature() -> tuple`, `effective_status`, and `badge()` helpers. Same stale-promotion rule as `firmware/src/state.cpp:155-161` (`ok → stale` once `now - last_poll_at > 180 s`).
- **`renderer.py`** — pure presentation. `render(snapshot) -> PIL.Image`, `push(image, display)` calls `display.set_image(...); display.show()`. Splitting render from push lets `tools/preview.py` exercise the renderer without an Inky panel.
- **`claude_stick_pi.py`** — orchestration. Owns the asyncio loop, the 60-second poll cadence, signal handlers, the single-instance flock, the redraw-decision logic, and the auto-detected Inky display object.
- **`layout.py`** — pixel-coordinate constants and font-loading helpers. Imported by `renderer.py`.
- **`config.py`** — paths, URLs, intervals, env-var overrides.

## 5. Repo layout

The Inky port lives in a new top-level `pi/` directory, sibling to `firmware/` and `daemon/`. The existing M5StickC code is untouched.

```
claude-stick/
├── firmware/                       unchanged
├── daemon/                         unchanged
└── pi/
    ├── claude_stick_pi.py          entry point: async main, signals, lock
    ├── poller.py                   poll_usage(), build_payload()
    ├── state.py                    Snapshot, visible_signature, effective_status
    ├── renderer.py                 render(snapshot) -> Image; push(image, display)
    ├── layout.py                   pixel coordinates, font sizes, badge thresholds
    ├── config.py                   paths, URLs, env-var defaults
    ├── requirements.txt            httpx (the only dep not already in the Pimoroni venv)
    ├── install.sh                  install deps + systemd unit + linger
    ├── claude-stick-pi.service     systemd user unit
    ├── tools/
    │   ├── preview.py              render to PNG via inky.mock
    │   └── fixtures/
    │       ├── default.json        sample snapshot for golden testing
    │       ├── first_run.json      no successful poll yet
    │       ├── auth_failed.json    status: auth
    │       ├── high_session.json   session ≥ 80%
    │       └── weekly_just_reset.json
    ├── tests/
    │   ├── test_poller.py          payload mapping under various claude.ai responses
    │   ├── test_state.py           visible-signature, stale-promotion, badge thresholds
    │   ├── test_renderer.py        dimensions, font-row assertions, golden PNG
    │   └── golden/
    │       └── default.png         committed reference frame
    ├── requirements-dev.txt        pytest, httpx[mock]
    └── README.md
```

`docs/superpowers/specs/2026-05-22-inky-port-design.md` (this file) is committed to git as the design of record.

## 6. Visual design

A single unified screen, rendered into a 250×122 palette-mode (`mode="P"`) PIL image with three palette entries (the inky driver's standard: 0=WHITE, 1=BLACK, 2=accent-unused).

### 6.1 Pixel allocation

Panel size is 250 × 122 (rows 0..121 inclusive). All coordinates below are given as **`y_start, height` half-open** — region occupies rows `y_start..y_start+height-1`. This matches Pillow's coordinate convention so the constants can be used directly in `ImageDraw` calls without translation.

| Region | y_start | height | Content |
|---|---|---|---|
| top pad | 0 | 2 | — |
| SESSION row | 2 | 24 | `SESSION` label (x=6, baseline ~y=22), badge box (x=170), `42%` (right-aligned, right edge x=244, baseline ~y=24) |
| (gap) | 26 | 2 | — |
| SESSION bar | 28 | 12 | outer rect x=6..239 (234 wide), 1 px border; fill occupies x=7..238, y=29..38 (232 × 10 interior) |
| (gap) | 40 | 2 | — |
| SESSION reset | 42 | 12 | `resets 17:42 · 3h 12m` at x=6, baseline ~y=51 |
| (gap) | 54 | 2 | — |
| WEEKLY row | 56 | 24 | mirror of SESSION row |
| (gap) | 80 | 2 | — |
| WEEKLY bar | 82 | 12 | mirror of SESSION bar |
| (gap) | 94 | 2 | — |
| WEEKLY reset | 96 | 12 | mirror of SESSION reset |
| (gap) | 108 | 2 | — |
| status footer | 110 | 12 | `status: ok` at x=6, baseline ~y=119 |

Sum: 2 + 24 + 2 + 12 + 2 + 12 + 2 + 24 + 2 + 12 + 2 + 12 + 2 + 12 = **122 px exactly** (rows 0..121, no overflow).

Horizontal margins: 6 px on left (`x=6` for all left-aligned text) and 6 px on right (right-aligned text ends at `x=243` or `x=244`). All x-coordinates are stored as named constants in `layout.py`; no magic numbers in `renderer.py`.

### 6.2 Fonts

All from the `font-hanken-grotesk` package, which is already installed in the Pimoroni venv:

- **20 px Bold** — percentages (`42%`, `67%`)
- **10 px SemiBold** — section labels (`SESSION`, `WEEKLY`)
- **9 px Regular** — reset countdowns, status footer

At startup the service asserts `font.getbbox(sample)[3] <= allocated_row_height` for each row. A font-package upgrade that shifts metrics will fail-fast at startup rather than silently overlap rows.

### 6.3 Threshold encoding

A 13×13 px badge box sits to the left of each percentage, severity encoded in the badge alone:

- **OK** (utilisation < 50%): hollow 1-px outlined box containing a centred small dot `·`
- **WARN** (50% ≤ utilisation < 80%): hollow 1-px outlined box containing `!`
- **HIGH** (utilisation ≥ 80%): solid black box containing inverted (white) `!`

Bars themselves stay simple solid fills — no patterns, no inversions — so the eye lands first on the badge and only then on the bar.

### 6.4 Reset countdown format

Same buckets as `firmware/src/display.cpp:97-112` *except* sub-minute values are not rendered (e-paper can't update fast enough to show seconds meaningfully):

| Duration | Format |
|---|---|
| < 60 s | *(rendered as empty / just the bar)* |
| < 1 h | `42m` |
| < 1 day | `3h 12m` |
| ≥ 1 day | `4d 6h` |

The full reset-line text is `resets <absolute time> · <duration>`, where absolute time is `HH:MM` for same-day, `Day HH:MM` otherwise. Identical to the firmware's `formatReset()`.

### 6.5 Status footer

Single line: `status: <word>` where `<word>` is one of `ok | stale | rate | auth | net`. Same enum as `firmware/src/state.h:25-32` (which maps from the daemon's status strings in `payload.cpp:23-30`). The status reflects the *data pipeline* only; SPI/Pillow failures don't affect this field (they show up in `journalctl` instead).

### 6.6 Empty / first-run state

Before any successful poll:
- percentages render as `--`
- bars render as empty outlined rectangles (no fill)
- reset lines render as `polling...` (three ASCII dots — Hanken Grotesk's `U+2026` ellipsis is a single glyph whose width and 1bpp rendering aren't worth the surprise)
- status footer is `status: net`

## 7. Refresh model

Hybrid: data is polled at a fixed cadence, but display redraws are gated by visible-change detection plus floor and ceiling time windows.

### 7.1 Poll cadence

60 seconds between polls (matches the existing daemon's `POLL_INTERVAL_SEC` default). Configurable via `POLL_INTERVAL_SEC` env var.

### 7.2 Visible-signature decision

After each poll, the service builds a `Snapshot` and computes its `visible_signature()` — a hashable tuple of every text string and badge level that affects rendered pixels:

```
(session_pct_int, session_badge, session_countdown_text, session_reset_text,
 weekly_pct_int,  weekly_badge,  weekly_countdown_text,  weekly_reset_text,
 status_text)
```

The redraw decision is then:

```
if last_redraw_at is None:                                          redraw  # first run
age = now - last_redraw_at
if signature == last_signature and age < MAX_INK_AGE:               skip
if signature != last_signature and age < MIN_REFRESH_INTERVAL:      skip    # floor
otherwise:                                                          redraw
```

Defaults:
- `MIN_REFRESH_INTERVAL_SEC = 300` (5 minutes — countdown text may lag reality by up to this much)
- `MAX_INK_AGE_SEC = 3600` (1 hour — one keep-alive refresh even if nothing changed)

Both are env-var overridable. The 100 k-refresh panel lifetime at one refresh per 5 min works out to ~1 year; at one refresh per hour it's ~11 years.

## 8. Failure modes

### 8.1 Startup failures (crash with clear errors)

These are fixable-once-then-forget conditions. The service exits with status 1 and a descriptive message; systemd surfaces it via `journalctl` and respects `RestartSec=10` so retry pressure stays gentle.

| Condition | Detection | Error message |
|---|---|---|
| `~/.config/claude-stick/session.json` missing | `FileNotFoundError` | extraction steps + path |
| session.json malformed JSON | `json.JSONDecodeError` | line/col + path |
| session.json missing `session_key` or `org_uuid` | key check | which field |
| Pimoroni venv missing | `ModuleNotFoundError: inky` at import | pointer to Pimoroni installer |
| Lock file held by another instance | `flock()` returns `EWOULDBLOCK` | holder PID + how to stop |
| Font row over-allocation | startup assertion fails | row name + actual vs allocated height |

### 8.2 Runtime failures (log, advance status, keep loop alive)

The data pipeline and the display pipeline fail independently and resolve independently.

| Condition | Resulting `status:` footer | Display behaviour |
|---|---|---|
| `httpx.RequestError` (network/DNS) | `net` | bars hold last-known values |
| Cloudflare HTML response | `net` (JSON decode fails) | bars hold |
| 401 / 403 (session expired) | `auth` + warning log pointing to DevTools refresh | bars hold |
| 429 (rate limited) | `rate` | bars hold |
| Other 4xx / 5xx | `net` | bars hold |
| Bucket missing from response | `ok` | render `--` for that bar |
| utilisation outside 0..100 | reject sample, log; `status` unchanged | bars hold |
| `inky.show()` raises | unchanged (display failure ≠ data failure) | log warning; next cycle retries |
| Pillow `render()` raises | unchanged | log warning; next cycle retries |

### 8.3 Single-instance lock

The flock pattern from `daemon/claude_stick_daemon.py:85-105` is reused at `~/.config/claude-stick/daemon.lock`. Its primary purpose on the Pi is to prevent accidentally running a foreground `python claude_stick_pi.py` while systemd is also running the unit — two processes writing SPI simultaneously would corrupt the display.

## 9. Service management

### 9.1 systemd user unit

`pi/claude-stick-pi.service`, installed to `~/.config/systemd/user/`:

```ini
[Unit]
Description=claude-stick Inky display daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/home/pi/.virtualenvs/pimoroni/bin/python /home/pi/claude-stick/pi/claude_stick_pi.py
Restart=always
RestartSec=10
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

Notable choices:
- **`TimeoutStopSec=30`** — a SIGTERM mid-`inky.show()` could tear the SPI write down partway and leave the panel in an undefined state. Inky's full refresh is ~3 s; 30 s is generous.
- **`After=network-online.target`** — first poll happens at startup; without the wait the daemon shows `status: net` for one cycle on every boot. Cosmetic but unnecessary.
- **No `User=`** — this is a `systemctl --user` unit; the user is `pi`. `loginctl enable-linger pi` (done by `install.sh`) makes it survive logout.

### 9.2 Install workflow

`pi/install.sh`:

1. Assert the Pimoroni venv exists at `~/.virtualenvs/pimoroni/`.
2. `pip install -r requirements.txt` into that venv (currently just `httpx`).
3. Copy `claude-stick-pi.service` to `~/.config/systemd/user/`.
4. `loginctl enable-linger pi` (idempotent).
5. `systemctl --user daemon-reload && systemctl --user enable --now claude-stick-pi`.
6. Tail `journalctl --user -u claude-stick-pi` for 5 s so the user sees the first poll cycle live.

### 9.3 Update workflow

```bash
ssh pi@inkyzero.local
cd ~/claude-stick && git pull
systemctl --user restart claude-stick-pi
```

The venv and unit file change rarely; routine updates are pure code.

### 9.4 Session config

`~/.config/claude-stick/session.json` — identical schema, path, and extraction procedure to the existing daemon (`daemon/README.md` lines 29-51). The session key and org UUID are the user's claude.ai credentials and must be `chmod 600`.

## 10. Testing strategy

### 10.1 Unit tests (off-Pi)

`pytest` against `poller.py` and `state.py`. These are pure functions and run anywhere.

Key cases for `test_poller.py`:
- Full claude.ai response → expected payload dict
- Missing `five_hour` bucket → `s = None`
- ISO and epoch-string `resets_at` formats both parse
- 401, 403, 429 → `status` = `auth`, `auth`, `rate`
- `httpx.RequestError` → `status = "net"`
- HTML response body → `status = "net"`
- Utilisation outside 0..100 → rejected with explicit log

Key cases for `test_state.py`:
- `visible_signature()` stable under sub-integer-% changes
- Signature changes when rounded integer % changes
- `status = "ok"` with `now - last_poll_at > 180` → `effective_status = "stale"`
- Daemon-flagged statuses (`auth`, `rate`, `net`) do *not* auto-promote
- Badge thresholds: 49% → ok, 50% → warn, 79% → warn, 80% → high

### 10.2 Renderer golden tests (off-Pi)

Uses `inky.mock.InkyMockPHAT(colour="black")` to render to in-memory PIL images, comparing byte-exact against PNGs in `pi/tests/golden/`.

- `test_renders_correct_dimensions`: image is 250×122 in `mode == "P"`.
- `test_font_rows_fit_allocation`: re-runs the same startup assertion that the service does, so CI catches font-package regressions.
- `test_golden_sample`: pixel-for-pixel equality against committed reference.

Golden images regenerate via `python tools/preview.py --update-golden`, committed in the same PR as the intentional layout change.

### 10.3 Hardware verification (on Pi)

Manual but mechanical, documented in `pi/README.md`:

1. `./install.sh` succeeds; first log lines appear within 5 s.
2. Panel renders the expected layout with real claude.ai data.
3. Block claude.ai with iptables → footer shows `status: net` within one cycle.
4. Restore network → footer returns to `status: ok` and bars update.
5. Set an invalid sessionKey → footer shows `status: auth` and the journal contains the "refresh sessionKey via DevTools" guidance message.
6. `sudo reboot` → panel returns to the same content within ~30 s.

## 11. What we drop from the existing codebase

### 11.1 Firmware (all of `firmware/src/`)

- `ble_server.cpp/h` — entire BLE server, advertising watchdog, callbacks
- `wifi_time.cpp/h` — the OS handles network + NTP
- `payload.cpp/h` — JSON parser; we have the canonical Python at hand
- `display.cpp/h` — sprite-based 5 Hz redraw, TFT-specific colour palette, three-screen cycling, button-driven screen advance
- `state.cpp` — FreeRTOS mutex, M5.Power battery reads, BLE MAC formatting (only the snapshot/visible-signature concepts survive, re-expressed in Python)
- `main.cpp` — button polling, M5Unified setup

### 11.2 Daemon (selectively)

The polling functions (`poll_usage`, `build_payload`, `parse_epoch`, `load_session_config`) are copied verbatim into `pi/poller.py`. The BLE half — `find_device`, `on_tx_notify`, the `BleakClient` connection loop, MAC caching at `~/.config/claude-stick/ble-address`, the BLE-specific "another daemon is running" hint message — is left behind. The single-instance flock pattern *is* kept, since it's still useful (preventing accidental SPI write contention).

### 11.3 Concepts that don't apply

- Multi-screen cycling (no input device)
- Trend arrows / delta-pp display (the precision view they support is gone with the Detail screen)
- Battery indicator (mains-powered)
- BLE state / RSSI / MAC display (no BLE)
- WiFi SSID / RSSI display (OS handles WiFi; not part of the user's mental model here)
- NTP-sync timestamp (OS handles NTP)
- Firmware version row (replaced implicitly by `git rev-parse` if anyone cares)

## 12. Open questions

None at design time. All identified preferences resolved during brainstorming.

## 13. Future work (out of scope)

These came up but are deliberately deferred:

- **Sparkline of utilisation history.** Would need persistent storage and a different layout; could replace the reset countdown row in a future revision.
- **Web dashboard on the Pi.** The Pi has the data; serving a tiny `/status` HTTP page would cost ~30 lines. Useful but not requested.
- **Multi-bucket display.** claude.ai's response has more buckets than `five_hour` and `seven_day` (e.g., `opus_seven_day`). Surface them if the user wants finer attribution.
- **Refresh-on-demand.** A small button wired to GPIO + a kernel debounce → trigger an immediate poll. Trivial in principle; needs a button.
