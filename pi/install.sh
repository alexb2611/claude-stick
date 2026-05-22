#!/usr/bin/env bash
#
# Install (or update) the claude-stick Inky pHAT service on this host.
# Idempotent: safe to re-run after pulling new code.
set -euo pipefail

PI_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PY="$HOME/.virtualenvs/pimoroni/bin/python"
UNIT_NAME="claude-stick-pi.service"
UNIT_TARGET_DIR="$HOME/.config/systemd/user"

step() { echo "==> $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

# 1. Verify the Pimoroni venv exists ────────────────────────────────────
step "Checking Pimoroni venv at $VENV_PY"
if [[ ! -x "$VENV_PY" ]]; then
    fail "Pimoroni venv not found. Install it via Pimoroni's inky setup
         (curl https://get.pimoroni.com/inky | bash) then re-run."
fi

# 2. Install Python deps ────────────────────────────────────────────────
step "Installing Python dependencies into Pimoroni venv"
"$VENV_PY" -m pip install --quiet -r "$PI_DIR/requirements.txt"

# 3. Install systemd unit ──────────────────────────────────────────────
step "Installing systemd user unit to $UNIT_TARGET_DIR"
mkdir -p "$UNIT_TARGET_DIR"
cp "$PI_DIR/$UNIT_NAME" "$UNIT_TARGET_DIR/$UNIT_NAME"

# 4. Enable linger so the service runs without login ───────────────────
step "Enabling lingering for user $USER"
loginctl enable-linger "$USER"

# 5. Reload + (re)start ────────────────────────────────────────────────
step "Reloading systemd and (re)starting the service"
systemctl --user daemon-reload
systemctl --user enable --now "$UNIT_NAME"

# 6. Tail journal so the user can see the first cycle ──────────────────
step "Tailing journal for ~5 seconds (Ctrl-C to skip)"
timeout 5 journalctl --user -u "$UNIT_NAME" -f --no-pager || true

step "Done. Check status with: systemctl --user status $UNIT_NAME"
