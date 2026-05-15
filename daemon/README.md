# claude-stick daemon

Polls the same usage endpoint that Claude Desktop's Settings → Usage page uses, and pushes the result to the M5StickC Plus over BLE.

## Why this approach

The original plan was to read rate-limit headers from `/v1/messages` responses with an OAuth token. Those headers turned out to describe API throughput limits (RPM/TPM telemetry), not subscription quota usage, which lives at a different endpoint behind a different auth system. The daemon now hits `https://claude.ai/api/organizations/{org}/usage` directly, the same call Claude Desktop's web view makes when you click the refresh icon on the usage page.

A nice side effect: the new approach just reads the counter, it doesn't bump it. The old design used one Haiku call per minute (1,440 quota-consuming requests per day) which slightly polluted the very thing we were trying to measure.

## Prerequisites

- Python 3.11 or newer (Debian 13 has 3.13 by default)
- An active Claude.ai login in a browser, so we can extract a sessionKey
- The stick paired with the laptop via `bluetoothctl`

## One-time pair (BLE)

The stick needs to be paired before the daemon can connect. From the stick's screen 3, note the MAC address, then:

```bash
bluetoothctl
> scan le
> pair F4:12:FA:C0:8F:E5     # the MAC from your stick's screen 3
> trust F4:12:FA:C0:8F:E5
> exit
```

## Extract sessionKey and org UUID

The daemon reads two values from `~/.config/claude-stick/session.json`. To get them:

1. Open `https://claude.ai/settings/usage` in Chrome
2. Open DevTools (`Ctrl+Shift+I`) and switch to the **Network** tab
3. Click the small circular refresh icon next to "Last updated: …" on the usage page
4. A request to `/api/organizations/{uuid}/usage` will appear in Network. Click it
5. The org UUID is the path segment between `organizations/` and `/usage`
6. For the sessionKey, switch to the **Application** tab → **Cookies** → `https://claude.ai`, find the row labelled `sessionKey`, and copy the value (starts with `sk-ant-sid02-`)

Create the config file:

```bash
mkdir -p ~/.config/claude-stick
cat > ~/.config/claude-stick/session.json <<'EOF'
{
  "session_key": "sk-ant-sid02-...PASTE_HERE...",
  "org_uuid":    "PASTE_UUID_HERE"
}
EOF
chmod 600 ~/.config/claude-stick/session.json
```

The `chmod 600` keeps the file readable only by you. The sessionKey is your active claude.ai login credential, so treat it like a password.

## Install as a user service

From the daemon directory:

```bash
./install.sh
systemctl --user enable --now claude-stick-daemon
```

The install script creates a venv in `.venv/`, installs Python dependencies, drops a unit file in `~/.config/systemd/user/`, and reloads systemd.

## Run in the foreground (for testing)

```bash
python3 -m venv .venv          # if you haven't already
.venv/bin/pip install -r requirements.txt
.venv/bin/python claude_stick_daemon.py
```

## Logs

```bash
journalctl --user -u claude-stick-daemon -f
```

## Configuration

Environment variables, all optional:

| Variable | Default | Effect |
|----------|---------|--------|
| `LOG_LEVEL` | `INFO` | Set to `DEBUG` for verbose output |
| `POLL_INTERVAL_SEC` | `60` | How often to poll claude.ai |
| `CLAUDE_STICK_CONFIG` | `~/.config/claude-stick/session.json` | Override config path |
| `SCAN_TIMEOUT_SEC` | `10` | BLE scan timeout when no MAC is cached |
| `RECONNECT_BACKOFF_SEC` | `5` | Pause between reconnect attempts |

To override for the systemd-managed daemon:

```bash
systemctl --user edit claude-stick-daemon
```

Add an `[Service]` block with `Environment=KEY=value` lines, save, then restart.

## How it works

On startup the daemon reads `session.json`, scans for the BLE device named `claude-stick` (or uses the cached MAC at `~/.config/claude-stick/ble-address`), connects, and subscribes to the TX characteristic so the stick can request fresh data on demand.

Every 60 seconds (or sooner if the stick fires its `0x01` refresh request) the daemon makes a `GET https://claude.ai/api/organizations/{org}/usage` request with the sessionKey as a cookie. The JSON response includes:

```json
{
  "five_hour": {"utilization": 89.0, "resets_at": "2026-05-14T18:50:01..."},
  "seven_day": {"utilization": 8.0,  "resets_at": "2026-05-18T20:00:01..."},
  ...
}
```

The daemon maps `five_hour` to the session bar and `seven_day` to the weekly bar, converts the ISO timestamps to Unix epoch seconds, and writes the result as a compact JSON payload to the stick's RX characteristic:

```json
{"s":89.0,"sr":1747244401,"w":8.0,"wr":1747598401,"st":"ok","ts":1747232412}
```

The stick parses that, updates state, redraws.

## Troubleshooting

**"Session config not found"** — create `~/.config/claude-stick/session.json` per the extraction steps above.

**"session key rejected (401)"** — your sessionKey has expired. Sessions on claude.ai last a few weeks typically. Refresh it via DevTools and update the file. The daemon will pick up the new value on restart.

**"session key rejected (403)"** — could be expired session, or could be Cloudflare deciding our request looks suspicious. Check whether `claude.ai` works normally in your browser; if it does and the daemon still gets 403s, try extracting a fresh sessionKey.

**"usage response was not JSON (Cloudflare challenge?)"** — Cloudflare's bot manager is intercepting. Rare with 1 request per minute, but possible if your IP gets flagged. Usually self-resolves within minutes.

**"device 'claude-stick' not found in scan"** — the stick is off, asleep, or not advertising. Press the front button to wake it and check screen 3 reads "Advertising".

**"BLE write failed"** — usually transient. If persistent, the bonding may have broken (e.g. you re-flashed the stick); run `bluetoothctl remove F4:...` then re-pair.
