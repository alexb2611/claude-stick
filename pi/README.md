# claude-stick: Inky pHAT variant

A Pi-resident port of [claude-stick](../README.md) for hosts running a
Pimoroni Inky pHAT V2. One Python service polls
`https://claude.ai/api/organizations/{org}/usage` every 60 s and renders
session + weekly utilisation to the 250×122 e-paper panel.

## Hardware

- Raspberry Pi Zero 2 W (or any Pi with the Inky pHAT footprint)
- Pimoroni Inky pHAT V2 (`InkyPHAT_SSD1608`, black variant)
- Debian 13 trixie, Python 3.13
- Pimoroni venv at `~/.virtualenvs/pimoroni/`
  (`curl https://get.pimoroni.com/inky | bash` installs this)

## Install

```bash
git clone <this-repo> ~/claude-stick
cd ~/claude-stick/pi
./install.sh
```

The installer pip-installs `httpx` into the Pimoroni venv, copies the
systemd user unit, enables lingering, and tails the journal so you can
see the first poll cycle.

## Session config

Reuses the laptop-daemon convention exactly. Create
`~/.config/claude-stick/session.json` with:

```json
{
  "session_key": "sk-ant-sid02-...",
  "org_uuid":    "..."
}
```

Extract both values from DevTools on `https://claude.ai/settings/usage`
(full instructions in [`../daemon/README.md`](../daemon/README.md)).
`chmod 600 ~/.config/claude-stick/session.json` — that sessionKey is
your live login.

## Refresh policy

The service polls every 60 s but redraws the panel only when something
visibly changes, subject to a 5-minute floor and a 1-hour keep-alive
ceiling. Defaults are in [`config.py`](config.py); override via env
vars in the systemd drop-in:

```bash
systemctl --user edit claude-stick-pi
# In the [Service] block add:
#   Environment=MIN_REFRESH_INTERVAL_SEC=600
#   Environment=POLL_INTERVAL_SEC=120
```

## Status footer

A single line at the bottom of the panel reflects the *data pipeline*:

| Word | Meaning |
|---|---|
| `ok` | recent successful poll |
| `stale` | last successful poll > 180 s ago |
| `rate` | claude.ai returned 429 |
| `auth` | sessionKey rejected (401/403) — refresh it via DevTools |
| `net` | network unreachable or claude.ai returned junk |

SPI / display failures appear in `journalctl`, not on the panel — they
don't affect this footer.

## Developing off-Pi

The renderer, state, poller, and layout modules are pure Python and run
anywhere. From `pi/`:

```bash
pip install -r requirements-dev.txt
pytest tests/
python tools/preview.py --show     # opens a PNG of the default fixture
```

Golden tests compare bytewise against committed PNGs in
`tests/golden/`. When you intentionally change the layout, regenerate
via `python tools/preview.py --update-golden` and commit the new
fixtures in the same change.

## Troubleshooting

`status: auth` and the journal says "session key rejected" — refresh
the sessionKey via DevTools (sessions on claude.ai expire every few
weeks) and update `session.json`. `systemctl --user restart claude-stick-pi`.

`No module named 'inky'` at startup — the service is running outside
the Pimoroni venv. Verify the `ExecStart=` path in the unit matches
your venv's `bin/python`.

`could not acquire lock` — another instance is running. Probably you
started a foreground `python claude_stick_pi.py` while systemd's copy
is also active. Stop one.
