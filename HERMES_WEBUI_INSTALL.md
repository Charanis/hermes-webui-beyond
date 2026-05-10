# Hermes WebUI Install Notes

Installed from: https://github.com/nesquena/hermes-webui
Local checkout: `/opt/data/hermes-webui`
Runtime Hermes source: `/opt/hermes`
Hermes home/config: `/opt/data` / `/opt/data/config.yaml`

## Runtime

The WebUI is configured by `/opt/data/hermes-webui/.env` and managed through:

```bash
/opt/data/scripts/hermes-webui start
/opt/data/scripts/hermes-webui status
/opt/data/scripts/hermes-webui logs --lines 100 --no-follow
/opt/data/scripts/hermes-webui restart
/opt/data/scripts/hermes-webui stop
```

There is also a symlink at `/opt/data/bin/hermes-webui`.

Default binding inside the container:

```text
HERMES_WEBUI_HOST=0.0.0.0
HERMES_WEBUI_PORT=8787
```

The WebUI uses password auth because it binds to `0.0.0.0`. The password is stored in `/opt/data/hermes-webui/.env` as `HERMES_WEBUI_PASSWORD`.

## Workspace wrapper and Windows browser access

The host wrapper provides a workspace management interface:

```bash
hermes workspace start
hermes workspace status
hermes workspace logs --lines 100 --no-follow
hermes workspace restart
hermes workspace stop
```

When run **inside the container**, this wrapper manages the WebUI daemon directly. When the same wrapper is run from the **WSL/Docker host**, it:

1. starts the WebUI inside the container via `docker exec`,
2. checks whether Docker already publishes container port `8787`, and
3. if not, starts a small local TCP forwarder so Windows can open the WebUI at:

```text
http://localhost:8787
```

Override the host/browser port with:

```bash
HERMES_WEBUI_HOST_PORT=8788 hermes workspace start
```

Inside the container the service listens on all container interfaces and is healthy at:

```bash
curl http://127.0.0.1:8787/health
curl http://$(hostname -I | awk '{print $1}'):8787/health
```

If you prefer native Docker port publishing instead of the wrapper forwarder, start/recreate the container with one of these mappings:

```bash
-p 8787:8787
# or, if 8787 is taken by another service:
-p 8788:8787
```

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `HERMES_CONTAINER` | `hermes` | Docker container name |
| `HERMES_DATA_DIR` | `~/.hermes` | Host-side data directory |
| `HERMES_WEBUI_HOST_PORT` | `8787` | Host port for browser access |

## Verification checklist

- WebUI checkout cloned from upstream.
- Wrapper and `.env` created.
- Host wrapper and TCP forwarder installed for WSL/host-side browser access.
- WebUI started and verified via workspace status.
- `/health` returns HTTP 200.
- Password login succeeds.
- `/api/models` loads the active model/provider.
- WebUI chat call succeeds, confirming the WebUI can run Hermes Agent.
