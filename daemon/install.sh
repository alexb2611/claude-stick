#!/usr/bin/env bash
# Install (or refresh) the claude-stick daemon as a systemd --user
# service. Idempotent: safe to re-run after pulling updates.

set -euo pipefail

DAEMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$DAEMON_DIR/.venv"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

echo "==> Creating venv (if missing) at $VENV_DIR"
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

echo "==> Installing Python dependencies"
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$DAEMON_DIR/requirements.txt" --quiet

echo "==> Installing systemd user unit"
mkdir -p "$SYSTEMD_USER_DIR"
cp "$DAEMON_DIR/claude-stick-daemon.service" \
   "$SYSTEMD_USER_DIR/claude-stick-daemon.service"

systemctl --user daemon-reload

cat <<'EOF'

==> Installed.

To start the daemon now and have it launch on every login:

  systemctl --user enable --now claude-stick-daemon

Useful commands:

  systemctl --user status   claude-stick-daemon
  systemctl --user restart  claude-stick-daemon
  systemctl --user stop     claude-stick-daemon
  journalctl --user -u claude-stick-daemon -f      # tail logs

If you haven't paired the stick yet, do that first:

  bluetoothctl
  > scan le
  > pair F4:12:FA:C0:8F:E5     # use the MAC shown on the stick's screen 3
  > trust F4:12:FA:C0:8F:E5
  > exit

EOF
