"""
Constants and environment-variable overrides for claude-stick-pi.

All tunable values live here. Other modules import from here; nothing
in this file imports from other modules in pi/ (it must be loadable
first, with no transitive imports of Pillow or inky).
"""

import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────
# The session-config path matches the existing laptop daemon exactly,
# so a user who's already extracted their sessionKey for the M5StickC
# setup doesn't have to do it again.

CONFIG_DIR  = Path.home() / ".config" / "claude-stick"
LOCK_FILE   = CONFIG_DIR / "daemon.lock"
SESSION_CFG = Path(os.environ.get(
    "CLAUDE_STICK_CONFIG",
    str(CONFIG_DIR / "session.json"),
))

# ── Polling ────────────────────────────────────────────────────────────

POLL_INTERVAL_SEC = int(os.environ.get("POLL_INTERVAL_SEC", "60"))
HTTP_TIMEOUT_SEC  = float(os.environ.get("HTTP_TIMEOUT_SEC", "15"))

# ── Display refresh policy ─────────────────────────────────────────────
# MIN_REFRESH_INTERVAL is a floor: even when the visible signature
# changes, don't refresh more often than this. Caps panel wear.
# MAX_INK_AGE is a ceiling: refresh at least this often even when
# nothing changed, both to confirm the service is alive and to reset
# any ghosting accumulation.

MIN_REFRESH_INTERVAL_SEC = int(os.environ.get("MIN_REFRESH_INTERVAL_SEC", "300"))
MAX_INK_AGE_SEC          = int(os.environ.get("MAX_INK_AGE_SEC", "3600"))

# ── Status promotion ───────────────────────────────────────────────────
# After this many seconds without a successful poll, an "ok" status is
# auto-promoted to "stale" so the user sees that the data isn't fresh.

STALE_AFTER_SEC = int(os.environ.get("STALE_AFTER_SEC", "180"))

# ── Badge thresholds (utilisation percentages) ─────────────────────────

BADGE_WARN_PCT = 50.0
BADGE_HIGH_PCT = 80.0

# ── claude.ai endpoint ─────────────────────────────────────────────────

USAGE_URL_FMT = "https://claude.ai/api/organizations/{org}/usage"

CLIENT_HEADERS = {
    "anthropic-client-platform": "web_claude_ai",
    "anthropic-client-version": "1.0.0",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# ── Logging ────────────────────────────────────────────────────────────

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
