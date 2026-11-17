#!/usr/bin/env bash
# AI Camera Intelligence — edge agent installer for Linux (x86_64 / aarch64, incl. NVIDIA Jetson).
#
# Installs the agent as a systemd service on the EDGE COMPUTER that sits next to the cameras
# (mini PC, NVR box, Jetson, Raspberry Pi-class ARM64 board). It does NOT install anything into
# camera firmware — cameras are reached over the network (RTSP / ONVIF) or USB.
#
#   sudo ./install.sh [--source DIR] [--with-claude] [--no-start]
#
# Layout:
#   /opt/aci-agent            code + virtualenv (root-owned, read-only for the service)
#   /etc/aci-agent/agent.env  configuration + secrets (root, mode 600)
#   /var/lib/aci-agent        databases, key file, backups (aci-agent, mode 700)
#   /usr/local/bin/aci-agentctl  start / stop / restart / status / logs / update / uninstall
#
# Requirements: systemd, Python 3.12 available to `uv` (uv downloads it if the system has none),
# and `uv` itself (https://docs.astral.sh/uv/ — install it first; this script never pipes a
# download into a shell).
set -euo pipefail

APP_USER=aci-agent
PREFIX=/opt/aci-agent
CONF_DIR=/etc/aci-agent
DATA_DIR=/var/lib/aci-agent
SERVICE=aci-agent
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$(cd "$SCRIPT_DIR/../.." && pwd)"
WITH_CLAUDE=0
START=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="$(cd "$2" && pwd)"; shift 2 ;;
    --with-claude) WITH_CLAUDE=1; shift ;;
    --no-start) START=0; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root (sudo ./install.sh)"
[[ "$(uname -s)" == "Linux" ]] || die "Linux only"
command -v systemctl >/dev/null || die "systemd is required"
[[ -f "$SOURCE/pyproject.toml" && -d "$SOURCE/aci_agent" ]] || die "agent sources not found in $SOURCE"
UV="$(command -v uv || true)"
[[ -n "$UV" ]] || die "uv is not installed — see https://docs.astral.sh/uv/getting-started/installation/"

ARCH="$(uname -m)"
BOARD="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
log "Installing edge agent on $ARCH ${BOARD:+($BOARD)}"

# --- service account --------------------------------------------------------------------------
if ! id "$APP_USER" >/dev/null 2>&1; then
  log "Creating system user $APP_USER"
  useradd --system --home-dir "$DATA_DIR" --no-create-home --shell /usr/sbin/nologin "$APP_USER"
fi
getent group video >/dev/null && usermod -a -G video "$APP_USER"

# --- code -------------------------------------------------------------------------------------
log "Copying code to $PREFIX"
install -d -m 755 -o root -g root "$PREFIX"
rm -rf "$PREFIX/aci_agent"
cp -r "$SOURCE/aci_agent" "$PREFIX/aci_agent"
install -m 644 "$SOURCE/pyproject.toml" "$SOURCE/uv.lock" "$PREFIX/"
[[ -f "$SOURCE/README.md" ]] && install -m 644 "$SOURCE/README.md" "$PREFIX/"
install -d -m 755 "$PREFIX/models" "$PREFIX/docs"
cp -f "$SOURCE"/models/*.onnx "$PREFIX/models/" 2>/dev/null || true
for doc in EDGE_AGENT_GUIDE.md JETSON.md; do
  [[ -f "$SOURCE/../../docs/$doc" ]] && install -m 644 "$SOURCE/../../docs/$doc" "$PREFIX/docs/"
done
find "$PREFIX/aci_agent" -name '__pycache__' -prune -exec rm -rf {} +
chown -R root:root "$PREFIX"

log "Creating virtualenv (uv sync --frozen)"
EXTRAS=()
[[ $WITH_CLAUDE -eq 1 ]] && EXTRAS+=(--extra claude)
(
  cd "$PREFIX"
  UV_PYTHON_INSTALL_DIR="$PREFIX/python" UV_CACHE_DIR="$PREFIX/.uv-cache" \
    "$UV" sync --frozen --no-dev --python 3.12 "${EXTRAS[@]}"
)
rm -rf "$PREFIX/.uv-cache"
chmod -R a+rX "$PREFIX"
"$PREFIX/.venv/bin/python" -c "import aci_agent, cv2; print('agent', aci_agent.__version__, 'opencv', cv2.__version__)"

# --- configuration ----------------------------------------------------------------------------
install -d -m 755 -o root -g root "$CONF_DIR"
if [[ ! -f "$CONF_DIR/agent.env" ]]; then
  log "Writing default configuration $CONF_DIR/agent.env (edit with: sudo aci-agentctl configure)"
  umask 077
  cat >"$CONF_DIR/agent.env" <<'EOF'
# AI Camera Intelligence — edge agent. Secrets live here: keep this file mode 600, owned by root.
ACI_AGENT_HOST=127.0.0.1
ACI_AGENT_PORT=8100
ACI_AGENT_LOG_JSON=true
# Shared with the central server's ACI_JWT_SECRET (enables the management API):
# ACI_AGENT_JWT_SECRET=
# Optional central server (HTTPS):
# ACI_AGENT_SERVER_URL=https://server.example.com
# ACI_AGENT_SERVER_ENROLLMENT_TOKEN=
EOF
fi
chown root:root "$CONF_DIR/agent.env"
chmod 600 "$CONF_DIR/agent.env"

# --- data -------------------------------------------------------------------------------------
install -d -m 700 -o "$APP_USER" -g "$APP_USER" "$DATA_DIR"
chown -R "$APP_USER:$APP_USER" "$DATA_DIR"
chmod 700 "$DATA_DIR"

# --- service + control script -----------------------------------------------------------------
install -m 644 "$SCRIPT_DIR/aci-agent.service" "/etc/systemd/system/$SERVICE.service"
install -m 755 "$SCRIPT_DIR/aci-agentctl" /usr/local/bin/aci-agentctl
install -d -m 755 "$PREFIX/deploy"
install -m 755 "$SCRIPT_DIR/install.sh" "$SCRIPT_DIR/aci-agentctl" "$PREFIX/deploy/"
install -m 644 "$SCRIPT_DIR/aci-agent.service" "$PREFIX/deploy/"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null

if [[ $START -eq 1 ]]; then
  log "Starting $SERVICE"
  systemctl restart "$SERVICE"
  for _ in $(seq 1 30); do
    if aci-agentctl health >/dev/null 2>&1; then
      log "Agent is up: $(aci-agentctl health)"
      break
    fi
    sleep 1
  done
  aci-agentctl health >/dev/null 2>&1 || echo "Agent did not answer yet — check: aci-agentctl logs"
fi

cat <<EOF

Installed. Next steps:
  sudo aci-agentctl configure      # JWT secret, device name, central server (optional)
  sudo aci-agentctl restart
  aci-agentctl status
Data: $DATA_DIR   Config: $CONF_DIR/agent.env   Code: $PREFIX
EOF
