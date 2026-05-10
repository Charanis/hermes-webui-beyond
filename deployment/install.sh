#!/usr/bin/env bash
set -euo pipefail

# Install Hermes WebUI with browser-accessible workspace support.
# Idempotent — safe to run multiple times.
#
# Usage:
#   ./install.sh                              # defaults: container=hermes, data=~/.hermes, port=8787
#   HERMES_CONTAINER=hermes2 HERMES_DATA_DIR=~/.hermes2 HERMES_WEBUI_HOST_PORT=8788 ./install.sh

CONTAINER_NAME="${HERMES_CONTAINER:-hermes}"
DATA_DIR="${HERMES_DATA_DIR:-$HOME/.hermes}"
HOST_PORT="${HERMES_WEBUI_HOST_PORT:-8787}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Hermes WebUI Installer"
echo "  Container: ${CONTAINER_NAME}"
echo "  Data dir:  ${DATA_DIR}"
echo "  Host port: ${HOST_PORT}"
echo ""

# ── 1. Check prerequisites ──
command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not found"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found (needed for port forwarder)"; exit 1; }
docker inspect -f '{{.State.Running}}' "${CONTAINER_NAME}" >/dev/null 2>&1 \
  || { echo "ERROR: Container '${CONTAINER_NAME}' is not running. Start it first."; exit 1; }

echo "Prerequisites OK."

# ── 2. Copy container-side WebUI ctl ──
echo "Installing container-side WebUI wrapper..."
docker cp "${SCRIPT_DIR}/wrappers/hermes-webui" "${CONTAINER_NAME}:/opt/data/scripts/hermes-webui"
docker exec "${CONTAINER_NAME}" chmod +x /opt/data/scripts/hermes-webui

# ── 3. Copy port forwarder to host ──
echo "Installing host-side port forwarder..."
mkdir -p "${DATA_DIR}/scripts"
cp "${SCRIPT_DIR}/wrappers/hermes-port-forward.py" "${DATA_DIR}/scripts/hermes-port-forward.py"
chmod +x "${DATA_DIR}/scripts/hermes-port-forward.py"

# ── 4. Install host wrapper to /usr/local/bin ──
WRAPPER_NAME="$(basename "${CONTAINER_NAME}")"
WRAPPER_SRC="${SCRIPT_DIR}/wrappers/hermes-wrapper.sh"
WRAPPER_DEST="/usr/local/bin/${WRAPPER_NAME}"

echo "Installing host wrapper to ${WRAPPER_DEST}..."
if [[ -w /usr/local/bin ]]; then
  cp "${WRAPPER_SRC}" "${WRAPPER_DEST}"
else
  echo "  (requires sudo)"
  sudo cp "${WRAPPER_SRC}" "${WRAPPER_DEST}"
fi
chmod +x "${WRAPPER_DEST}" 2>/dev/null || sudo chmod +x "${WRAPPER_DEST}"

# ── 5. Create .env if missing ──
ENV_FILE="${DATA_DIR}/hermes-webui/.env"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Creating .env from example..."
  mkdir -p "$(dirname "${ENV_FILE}")"
  PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
  cat > "${ENV_FILE}" << ENVEOF
# Hermes WebUI configuration
HERMES_HOME=/opt/data
HERMES_WEBUI_HOST=0.0.0.0
HERMES_WEBUI_PORT=8787
HERMES_WEBUI_PASSWORD=${PASSWORD}
ENVEOF
  echo "  Generated password saved to ${ENV_FILE}"
  echo "  IMPORTANT: Save this password — you'll need it to log in."
else
  echo "  .env already exists, skipping."
fi

# ── 6. Done ──
echo ""
echo "Installation complete!"
echo ""
echo "Start the WebUI:"
echo "  ${WRAPPER_NAME} workspace start"
echo ""
echo "Then open in your Windows browser:"
echo "  http://127.0.0.1:${HOST_PORT}"
