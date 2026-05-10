#!/bin/bash
set -euo pipefail

# Hermes Agent host wrapper
# Manages WebUI access from Windows browser via TCP port forwarding.
#
# Configurable via environment variables:
#   HERMES_CONTAINER   — Docker container name (default: hermes)
#   HERMES_DATA_DIR    — Host data directory (default: ~/.hermes)
#   HERMES_WEBUI_HOST_PORT — Host port for browser access (default: 8787)
#
# Data directory: $HERMES_DATA_DIR (host) / /opt/data (container)
# WebUI: http://localhost:$HERMES_WEBUI_HOST_PORT (Windows browser)

DATA_DIR_HOST="${HERMES_DATA_DIR:-$HOME/.hermes}"
CONTAINER_NAME="${HERMES_CONTAINER:-hermes}"
INTERNAL_HOST="0.0.0.0"
INTERNAL_PORT="8787"
HOST_BIND="127.0.0.1"
HOST_PORT="${HERMES_WEBUI_HOST_PORT:-8787}"
FORWARDER="${DATA_DIR_HOST}/scripts/hermes-port-forward.py"
FORWARD_PID_FILE="${DATA_DIR_HOST}/webui.forward.pid"
FORWARD_STATE_FILE="${DATA_DIR_HOST}/webui.forward.json"
FORWARD_LOG="${DATA_DIR_HOST}/webui.forward.log"
CONTAINER_WEBUI_CMD="/opt/data/scripts/hermes-webui"

# ── helpers ──

container_running() {
  docker inspect -f '{{.State.Running}}' "${CONTAINER_NAME}" 2>/dev/null | grep -qx true
}

require_container() {
  if ! container_running; then
    echo "Container '${CONTAINER_NAME}' is not running. Start it first." >&2
    exit 1
  fi
}

container_ip() {
  docker inspect -f '{{range .NetworkSettings.Networks}}{{if .IPAddress}}{{.IPAddress}}{{end}}{{end}}' "${CONTAINER_NAME}" 2>/dev/null | awk 'NF{print $1; exit}'
}

container_webui() {
  docker exec "${CONTAINER_NAME}" "${CONTAINER_WEBUI_CMD}" "$@"
}

fwd_pid() {
  [[ -f "${FORWARD_PID_FILE}" ]] || return 1
  tr -d '[:space:]' < "${FORWARD_PID_FILE}"
}

forward_alive() {
  pid="$(fwd_pid 2>/dev/null || true)"
  [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1
}

stop_forwarder() {
  pid="$(fwd_pid 2>/dev/null || true)"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
    echo "[hermes] Stopping host WebUI forwarder (PID ${pid})"
    kill "${pid}" >/dev/null 2>&1 || true
    for _ in {1..40}; do
      kill -0 "${pid}" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "${pid}" 2>/dev/null; then
      kill -KILL "${pid}" >/dev/null 2>&1 || true
    fi
  fi
  rm -f "${FORWARD_PID_FILE}" "${FORWARD_STATE_FILE}"
}

start_forwarder() {
  target_host="$1"
  if forward_alive; then
    echo "[hermes] Host WebUI forwarder already running (PID $(fwd_pid))"
    return 0
  fi
  if [[ ! -f "${FORWARDER}" ]]; then
    echo "Forwarder script missing: ${FORWARDER}" >&2
    exit 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required on WSL host for the WebUI forwarder" >&2
    exit 1
  fi
  mkdir -p "$(dirname "${FORWARD_PID_FILE}")"
  : > "${FORWARD_LOG}"
  nohup python3 "${FORWARDER}" \
    --bind-host "${HOST_BIND}" \
    --bind-port "${HOST_PORT}" \
    --target-host "${target_host}" \
    --target-port "${INTERNAL_PORT}" \
    --pid-file "${FORWARD_PID_FILE}" \
    --state-file "${FORWARD_STATE_FILE}" \
    >> "${FORWARD_LOG}" 2>&1 &
  sleep 0.4
  if ! forward_alive; then
    echo "[hermes] Failed to start forwarder. Log: ${FORWARD_LOG}" >&2
    sed -n '1,40p' "${FORWARD_LOG}" >&2 2>/dev/null || true
    exit 1
  fi
  echo "[hermes] Forwarder: ${HOST_BIND}:${HOST_PORT} -> ${target_host}:${INTERNAL_PORT}"
}

# ── update command ──

if [[ "${1:-}" == "update" ]]; then
    echo "Updating hermes agent..."
    rm -rf /tmp/hermes-agent-build
    git clone https://github.com/NousResearch/hermes-agent.git /tmp/hermes-agent-build
    sed -i '/\.git/d' /tmp/hermes-agent-build/.dockerignore
    docker build -t nousresearch/hermes-agent:latest /tmp/hermes-agent-build
    rm -rf /tmp/hermes-agent-build
    echo "Update complete!"
    exit 0
fi

# ── workspace command ──

if [[ "${1:-}" == "workspace" ]]; then
  case "${2:-status}" in
    start)
      require_container
      echo "[hermes] Starting WebUI inside container '${CONTAINER_NAME}'"
      container_webui start --host "${INTERNAL_HOST}" "${INTERNAL_PORT}"

      target="$(container_ip)"
      if [[ -z "${target}" ]]; then
        target="127.0.0.1"
      fi
      start_forwarder "${target}"
      echo ""
      echo "Hermes WebUI is ready! Open in your Windows browser:"
      echo "  http://127.0.0.1:${HOST_PORT}"
      exit 0
      ;;
    stop)
      require_container
      stop_forwarder
      echo "[hermes] Stopping WebUI inside container '${CONTAINER_NAME}'"
      container_webui stop
      echo "Workspace stopped."
      exit 0
      ;;
    restart)
      require_container
      stop_forwarder 2>/dev/null || true
      container_webui stop 2>/dev/null || true
      echo "[hermes] Restarting WebUI inside container '${CONTAINER_NAME}'"
      container_webui start --host "${INTERNAL_HOST}" "${INTERNAL_PORT}"
      target="$(container_ip)"
      if [[ -z "${target}" ]]; then
        target="127.0.0.1"
      fi
      start_forwarder "${target}"
      echo ""
      echo "Hermes WebUI is ready! Open in your Windows browser:"
      echo "  http://127.0.0.1:${HOST_PORT}"
      exit 0
      ;;
    status)
      require_container
      echo "Hermes Workspace Status:"
      container_webui status | sed 's/^/  /'
      if forward_alive; then
        echo "  Forwarder: running (PID $(fwd_pid)), ${HOST_BIND}:${HOST_PORT} -> container:${INTERNAL_PORT}"
        echo "  Windows browser: http://127.0.0.1:${HOST_PORT}"
      else
        echo "  Forwarder: stopped"
        echo "  Start with: ${CONTAINER_NAME} workspace start"
      fi
      if command -v curl >/dev/null 2>&1; then
        if curl -fsS --max-time 2 "http://${HOST_BIND}:${HOST_PORT}/health" >/dev/null 2>&1; then
          echo "  Host health: ok"
        else
          echo "  Host health: not reachable"
        fi
      fi
      exit 0
      ;;
    logs)
      require_container
      shift 2
      container_webui logs "${@:---lines 100 --no-follow}"
      exit 0
      ;;
    url)
      echo "http://127.0.0.1:${HOST_PORT}"
      exit 0
      ;;
    *)
      echo "Usage: ${CONTAINER_NAME} workspace {start|stop|restart|status|logs|url}"
      exit 1
      ;;
  esac
fi

# ── dashboard command ──

if [[ "${1:-}" == "dashboard" ]]; then
    PORT="-p 9120:9120"
    EXTRA_ARGS="--host 0.0.0.0 --port 9120 --insecure --no-open"
else
    PORT=""
    EXTRA_ARGS=""
fi

# ── default: docker run ──

docker run -it --rm \
  $PORT \
  -v "${DATA_DIR_HOST}:/opt/data" \
  -e HERMES_UID=1000 \
  -e HERMES_GID=1000 \
  -e HERMES_TUI_DIR=/opt/data/ui-tui \
  -e PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  nousresearch/hermes-agent "$@" $EXTRA_ARGS
