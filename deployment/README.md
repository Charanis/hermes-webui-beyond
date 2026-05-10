# Hermes WebUI — Deployment Guide

> This fork adds a profile file editor and a deployment layer on top of [nesquena/hermes-webui](https://github.com/nesquena/hermes-webui).

## What this fork adds

- **Profile file editor** — Edit SOUL.md, config.yaml, .env, and memory files directly from the WebUI
- **Deployment wrappers** — Generic, configurable scripts for running the WebUI behind Docker with Windows browser access
- **Idempotent install script** — One command to set up everything for any hermes-agent instance

## Prerequisites

- Docker (running in WSL2 or Linux)
- Python 3 on the host (stdlib only — no extra packages)
- A running hermes-agent container (e.g. `hermes`, `hermes2`)

## Quick Start

```bash
# Clone this fork
git clone -b custom https://github.com/Charanis/hermes-webui-beyond.git
cd hermes-webui-beyond

# Run the installer (defaults: container=hermes, data=~/.hermes, port=8787)
cd deployment
./install.sh

# For a second agent instance:
HERMES_CONTAINER=hermes2 HERMES_DATA_DIR=~/.hermes2 HERMES_WEBUI_HOST_PORT=8788 ./install.sh
```

## Install Script Details

The install script (`deployment/install.sh`) is idempotent and safe to run multiple times. It:

1. **Checks prerequisites** — Docker, Python3, and that the target container is running
2. **Copies container-side wrapper** — `hermes-webui` to `/opt/data/scripts/` inside the container
3. **Copies port forwarder** — `hermes-port-forward.py` to `$HERMES_DATA_DIR/scripts/` on the host
4. **Installs host wrapper** — `hermes-wrapper.sh` to `/usr/local/bin/<container-name>` (e.g. `/usr/local/bin/hermes`)
5. **Creates .env** — Generates a random password if `.env` doesn't exist

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HERMES_CONTAINER` | `hermes` | Docker container name |
| `HERMES_DATA_DIR` | `~/.hermes` | Host-side data directory |
| `HERMES_WEBUI_HOST_PORT` | `8787` | Host port for Windows browser access |

## Usage

After installation, manage the WebUI with:

```bash
# Start the WebUI (accessible from Windows browser)
hermes workspace start

# Check status
hermes workspace status

# View logs
hermes workspace logs

# Restart
hermes workspace restart

# Stop
hermes workspace stop

# Print the URL
hermes workspace url
```

For a second instance (installed with different env vars):

```bash
HERMES_CONTAINER=hermes2 hermes2 workspace start
```

## File Reference

```
deployment/
├── README.md                    # This file
├── install.sh                   # Idempotent installer
└── wrappers/
    ├── hermes-wrapper.sh        # Host-side wrapper (installed to /usr/local/bin)
    ├── hermes-webui             # Container-side WebUI ctl
    └── hermes-port-forward.py   # TCP port forwarder (stdlib Python)
```

## Customizing for Another Agent

To deploy for a different hermes agent instance:

1. Set `HERMES_CONTAINER` to the container name
2. Set `HERMES_DATA_DIR` to the host data directory
3. Set `HERMES_WEBUI_HOST_PORT` to an available port
4. Run `./install.sh`

Example for a second agent on the same machine:

```bash
HERMES_CONTAINER=hermes2 \
HERMES_DATA_DIR=~/.hermes2 \
HERMES_WEBUI_HOST_PORT=8788 \
./install.sh
```

## Pulling Upstream Updates

```bash
git fetch upstream
git merge upstream/master
# Resolve any conflicts, then:
git push origin custom
```

## Changes from Upstream

### api/routes.py (+61 lines)

Added profile file read/write API endpoints:
- `GET /api/profile/files` — List editable files for a profile
- `POST /api/profile/files` — Read or write a profile file

Whitelist approach for security — only these files are accessible:
- `SOUL.md`, `config.yaml`, `.env`
- `memories/MEMORY.md`, `memories/USER.md`

Path traversal prevention via `.resolve()` + `.relative_to()` checks.

### static/panels.js (+79 lines)

Added profile file editor UI:
- File list card in profile detail view
- Edit button opens inline editor
- Save button writes changes back via API
- SVG buttons with CSS tooltips
