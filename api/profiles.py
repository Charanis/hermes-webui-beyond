"""
Hermes Web UI -- Profile state management.
Wraps hermes_cli.profiles to provide profile switching for the web UI.

The web UI maintains a process-level "active profile" that determines which
HERMES_HOME directory is used for config, skills, memory, cron, and API keys.
Profile switches update os.environ['HERMES_HOME'] and monkey-patch module-level
cached paths in hermes-agent modules (skills_tool, skill_manager_tool,
cron/jobs) that snapshot HERMES_HOME at import time.
"""
import json
import logging
import os
import re
import shutil
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Constants (match hermes_cli.profiles upstream) ─────────────────────────
_PROFILE_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')
_PROFILE_DIRS = [
    'memories', 'sessions', 'skills', 'skins',
    'logs', 'plans', 'workspace', 'cron',
]
_CLONE_CONFIG_FILES = ['config.yaml', '.env', 'SOUL.md']

# ── Module state ────────────────────────────────────────────────────────────
_active_profile = 'default'
_profile_lock = threading.Lock()
_loaded_profile_env_keys: set[str] = set()

# Thread-local profile context: set per-request by server.py, cleared after.
# Enables per-client profile isolation (issue #798) — each HTTP request thread
# reads its own profile from the hermes_profile cookie instead of the
# process-global _active_profile.
_tls = threading.local()

_SKILL_HOME_MODULES = ("tools.skills_tool", "tools.skill_manager_tool")


def patch_skill_home_modules(home: Path) -> None:
    """Patch imported skill modules that cache HERMES_HOME at import time."""
    for module_name in _SKILL_HOME_MODULES:
        module = sys.modules.get(module_name)
        if module is None:
            continue
        try:
            module.HERMES_HOME = home
            module.SKILLS_DIR = home / "skills"
        except AttributeError:
            logger.debug("Failed to patch %s module", module_name)


def _unwrap_profile_home_to_base(home: Path) -> Path:
    """Return the base Hermes home when *home* is already a named profile dir."""
    if home.parent.name == 'profiles':
        return home.parent.parent
    return home


def _resolve_base_hermes_home() -> Path:
    """Return the BASE ~/.hermes directory — the root that contains profiles/.

    This is intentionally distinct from HERMES_HOME, which tracks the *active
    profile's* home and changes on every profile switch.  The base dir must
    always point to the top-level .hermes regardless of which profile is active.

    Resolution order:
      1. HERMES_BASE_HOME env var (set explicitly, highest priority)
      2. HERMES_HOME env var — but only if it does NOT look like a profile subdir
         (i.e. its parent is not named 'profiles').  This handles test isolation
         where HERMES_HOME is set to an isolated test state dir.
      3. ~/.hermes (always-correct default)

    The bug this prevents: if HERMES_HOME has already been mutated to
    /home/user/.hermes/profiles/webui (by init_profile_state at startup),
    reading it here would make _DEFAULT_HERMES_HOME point to that subdir,
    causing switch_profile('webui') to look for
    /home/user/.hermes/profiles/webui/profiles/webui — which doesn't exist.

    HERMES_BASE_HOME normally points at the base home already, but isolated
    single-profile WebUI deployments can provide /base/profiles/<name> there as
    well.  Normalize both env vars through the same helper so active-profile
    and per-request resolution share one base-root contract (#749).
    """
    # Explicit override for tests or unusual setups
    base_override = os.getenv('HERMES_BASE_HOME', '').strip()
    if base_override:
        return _unwrap_profile_home_to_base(Path(base_override).expanduser())

    hermes_home = os.getenv('HERMES_HOME', '').strip()
    if hermes_home:
        p = Path(hermes_home).expanduser()
        # If HERMES_HOME points to a profiles/ subdir, walk up two levels to the base
        return _unwrap_profile_home_to_base(p)

    return Path.home() / '.hermes'

_DEFAULT_HERMES_HOME = _resolve_base_hermes_home()


def _read_active_profile_file() -> str:
    """Read the sticky active profile from ~/.hermes/active_profile."""
    ap_file = _DEFAULT_HERMES_HOME / 'active_profile'
    if ap_file.exists():
        try:
            name = ap_file.read_text(encoding="utf-8").strip()
            if name:
                return name
        except Exception:
            logger.debug("Failed to read active profile file")
    return 'default'


# ── Public API ──────────────────────────────────────────────────────────────

# ── Root-profile resolution (#1612) ────────────────────────────────────────
#
# Hermes Agent allows the root/default profile (~/.hermes itself) to have a
# display name other than the legacy literal 'default'.  When that happens,
# WebUI must NOT resolve the display name as ~/.hermes/profiles/<name> — that
# directory doesn't exist, and every site that does `if name == 'default':`
# will fall through to the wrong filesystem path.
#
# `_is_root_profile(name)` answers "does this name resolve to ~/.hermes?" and
# is the canonical replacement for scattered `if name == 'default':` checks
# in switch_profile, get_active_hermes_home, _validate_profile_name, etc.
#
# Cost note: list_profiles_api() shells out via hermes_cli (non-trivial), so
# we memoize the lookup. The cache is invalidated whenever profiles are
# created, deleted, renamed, or cloned — i.e. on every mutation site we
# control.
_root_profile_name_cache: set[str] = {'default'}
_root_profile_name_cache_lock = threading.Lock()
_root_profile_name_cache_loaded = False


def _invalidate_root_profile_cache() -> None:
    """Drop the memoized root-profile-name set.

    Called whenever profile metadata might have changed: create, clone,
    delete, rename. The next _is_root_profile() call repopulates from
    list_profiles_api().
    """
    global _root_profile_name_cache_loaded
    with _root_profile_name_cache_lock:
        _root_profile_name_cache.clear()
        _root_profile_name_cache.add('default')
        _root_profile_name_cache_loaded = False


def _is_root_profile(name: str) -> bool:
    """True if *name* resolves to the Hermes Agent root profile (~/.hermes).

    Matches the legacy 'default' alias plus any name where list_profiles_api()
    reports is_default=True. Memoized; call _invalidate_root_profile_cache()
    after mutating profile metadata.
    """
    global _root_profile_name_cache_loaded
    if not name:
        return False
    if name == 'default':
        return True
    with _root_profile_name_cache_lock:
        if _root_profile_name_cache_loaded:
            return name in _root_profile_name_cache
    # Cache miss — populate from list_profiles_api(). Done outside the lock to
    # avoid holding it across a hermes_cli subprocess call.
    try:
        infos = list_profiles_api()
    except Exception:
        logger.debug("Failed to list profiles for root-profile lookup", exc_info=True)
        return False
    with _root_profile_name_cache_lock:
        _root_profile_name_cache.clear()
        _root_profile_name_cache.add('default')
        for p in infos:
            try:
                if p.get('is_default') and p.get('name'):
                    _root_profile_name_cache.add(p['name'])
            except (AttributeError, TypeError):
                continue
        _root_profile_name_cache_loaded = True
        return name in _root_profile_name_cache


def _profiles_match(row_profile, active_profile) -> bool:
    """Return True if a session/project row's profile matches the active profile.

    Treats both the literal alias 'default' and any renamed-root display name
    (per _is_root_profile) as equivalent, so legacy rows tagged 'default'
    still surface when the user has renamed the root profile to e.g. 'kinni',
    and vice versa.

    A row with no profile (`None` or empty string) is treated as belonging to
    the root profile — that's the convention used by the legacy backfill at
    api/models.py::all_sessions, and matches the default seen in
    `static/sessions.js` (`S.activeProfile||'default'`).

    Originally lived in api/routes.py; relocated here so both routes.py and
    out-of-process consumers (mcp_server.py) can import the canonical helper
    instead of duplicating the body. See #1614 for the visibility model.
    """
    row = row_profile or 'default'
    active = active_profile or 'default'
    if row == active:
        return True
    # Cross-alias the renamed root.
    if _is_root_profile(row) and _is_root_profile(active):
        return True
    return False


def get_active_profile_name() -> str:
    """Return the currently active profile name.

    Priority:
      1. Thread-local (set per-request from hermes_profile cookie) — issue #798
      2. Process-level default (_active_profile)
    """
    tls_name = getattr(_tls, 'profile', None)
    if tls_name is not None:
        return tls_name
    return _active_profile


def set_request_profile(name: str) -> None:
    """Set the per-request profile context for this thread.

    Called by server.py at the start of each request when a hermes_profile
    cookie is present.  Always paired with clear_request_profile() in a
    finally block so the thread-local is released after the request.
    """
    _tls.profile = name


def clear_request_profile() -> None:
    """Clear the per-request profile context for this thread.

    Called by server.py in the finally block of do_GET / do_POST.
    Safe to call even if set_request_profile() was never called.
    """
    _tls.profile = None


def _resolve_profile_home_for_name(name: str) -> Path:
    """Resolve a logical profile name to its Hermes home path.

    Root/default aliases resolve to _DEFAULT_HERMES_HOME.  Valid named profiles
    resolve to _DEFAULT_HERMES_HOME/profiles/<name> even when the directory has
    not been created yet; the agent layer may create it on first use.  Invalid
    names fall back to the base home so traversal-shaped cookie values cannot
    influence filesystem paths.
    """
    if not name or _is_root_profile(name):
        return _DEFAULT_HERMES_HOME
    if not _PROFILE_ID_RE.fullmatch(name):
        return _DEFAULT_HERMES_HOME
    return _resolve_named_profile_home(name)


def get_active_hermes_home() -> Path:
    """Return the HERMES_HOME path for the currently active profile.

    Uses get_active_profile_name() so per-request TLS context (issue #798)
    is respected, not just the process-level global.
    """
    return _resolve_profile_home_for_name(get_active_profile_name())



# ── Cron-call profile isolation (issue: Scheduled jobs ignored active profile) ─
# `cron.jobs` reads HERMES_HOME from os.environ (process-global) at function-
# call time. That bypasses our per-request thread-local profile, so the
# `/api/crons*` endpoints always returned the process-default profile's jobs.
# This context manager swaps HERMES_HOME (and the cached module-level constants
# in cron.jobs) for the duration of a cron call, serialized by a lock so
# concurrent requests from different profiles don't race on the global env var.
#
# Thread-safety note on os.environ mutation:
# CPython's os.environ assignment is GIL-protected at the bytecode level, but
# multi-step read-modify-write sequences (snapshot prev → assign new → restore
# on exit) are NOT atomic without explicit serialization. The _cron_env_lock
# below makes the entire context-manager body run-to-completion serially, so
# all webui access to HERMES_HOME goes through one thread at a time. Any
# subprocess.Popen() call inside `run_job` inherits the env at fork time,
# which is also under the lock — so child processes always see a consistent
# (own-profile) HERMES_HOME, never a half-swapped state.
_cron_env_lock = threading.Lock()


def _cron_profile_context_depth() -> int:
    return int(getattr(_tls, 'cron_profile_depth', 0) or 0)


def _push_cron_profile_context_depth() -> None:
    _tls.cron_profile_depth = _cron_profile_context_depth() + 1


def _pop_cron_profile_context_depth() -> None:
    depth = _cron_profile_context_depth()
    _tls.cron_profile_depth = max(0, depth - 1)


def _home_for_scheduled_cron_job(job: dict) -> Path:
    """Resolve the profile home an auto-fired scheduler job should execute in.

    Legacy jobs with no profile keep the scheduler's server-default profile.
    Jobs pinned to a named profile execute under that profile's HERMES_HOME, so
    an in-process WebUI scheduler thread does not leak process-global config or
    .env into the agent run. If a profile was deleted after the job was saved,
    fall back to the server default rather than crashing every scheduler tick.
    """
    raw = str((job or {}).get('profile') or '').strip()
    if not raw:
        return get_active_hermes_home()
    if _is_root_profile(raw):
        return _DEFAULT_HERMES_HOME
    if not _PROFILE_ID_RE.fullmatch(raw):
        logger.warning(
            "Cron job %s has invalid profile %r; falling back to server default",
            (job or {}).get('id', '?'), raw,
        )
        return get_active_hermes_home()
    home = _resolve_named_profile_home(raw)
    if not home.is_dir():
        logger.warning(
            "Cron job %s references missing profile %r; falling back to server default",
            (job or {}).get('id', '?'), raw,
        )
        return get_active_hermes_home()
    return home


def install_cron_scheduler_profile_isolation() -> None:
    """Patch cron.scheduler.run_job for WebUI in-process scheduler safety.

    Standard WebUI deployments do not start the scheduler thread in-process, but
    if a future/single-process deployment calls cron.scheduler.tick() from the
    WebUI worker, tick's background job path has no request TLS context. Wrap
    run_job so each auto-fired job's persisted ``profile`` field gets the same
    HERMES_HOME isolation as the manual /api/crons/run path.
    """
    try:
        import cron.scheduler as _cs
    except ImportError:
        logger.debug("install_cron_scheduler_profile_isolation: cron.scheduler unavailable")
        return

    original = getattr(_cs, 'run_job', None)
    if original is None or getattr(original, '_webui_profile_isolated', False):
        return

    def _webui_profile_isolated_run_job(job, *args, **kwargs):
        # Manual WebUI runs already enter cron_profile_context_for_home before
        # calling run_job. Avoid nesting the non-reentrant env lock or changing
        # the explicitly selected manual execution profile.
        if _cron_profile_context_depth() > 0:
            return original(job, *args, **kwargs)
        with cron_profile_context_for_home(_home_for_scheduled_cron_job(job)):
            return original(job, *args, **kwargs)

    _webui_profile_isolated_run_job._webui_profile_isolated = True
    _webui_profile_isolated_run_job._webui_original_run_job = original
    _cs.run_job = _webui_profile_isolated_run_job


class cron_profile_context_for_home:
    """Context manager that pins HERMES_HOME to an explicit profile home path.

    Use this variant from worker threads that don't have TLS context (e.g. the
    background thread started by /api/crons/run). The HTTP-side variant below
    resolves the home via TLS.
    """

    def __init__(self, home: Path):
        self._home = Path(home)

    def __enter__(self):
        _cron_env_lock.acquire()
        _push_cron_profile_context_depth()
        try:
            self._prev_env = os.environ.get('HERMES_HOME')
            os.environ['HERMES_HOME'] = str(self._home)

            # Re-patch cron.jobs module-level constants (see main context manager
            # below for the rationale).
            self._prev_cj = None
            try:
                import cron.jobs as _cj
                self._prev_cj = (_cj.HERMES_DIR, _cj.CRON_DIR, _cj.JOBS_FILE, _cj.OUTPUT_DIR)
                _cj.HERMES_DIR = self._home
                _cj.CRON_DIR = self._home / 'cron'
                _cj.JOBS_FILE = _cj.CRON_DIR / 'jobs.json'
                _cj.OUTPUT_DIR = _cj.CRON_DIR / 'output'
            except (ImportError, AttributeError):
                logger.debug("cron_profile_context_for_home: cron.jobs unavailable")

            # cron.scheduler snapshots _hermes_home at import time and run_job()
            # reads config/.env from that module global. Patch it alongside
            # cron.jobs so manual WebUI runs actually execute under the selected
            # profile, not merely write output metadata there (#617).
            self._prev_cs = None
            try:
                import cron.scheduler as _cs
                self._prev_cs = (
                    getattr(_cs, '_hermes_home', None),
                    getattr(_cs, '_LOCK_DIR', None),
                    getattr(_cs, '_LOCK_FILE', None),
                )
                _cs._hermes_home = self._home
                _cs._LOCK_DIR = self._home / 'cron'
                _cs._LOCK_FILE = _cs._LOCK_DIR / '.tick.lock'
            except (ImportError, AttributeError):
                logger.debug("cron_profile_context_for_home: cron.scheduler unavailable")
        except Exception:
            _pop_cron_profile_context_depth()
            _cron_env_lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._prev_env is None:
                os.environ.pop('HERMES_HOME', None)
            else:
                os.environ['HERMES_HOME'] = self._prev_env
            if self._prev_cj is not None:
                try:
                    import cron.jobs as _cj
                    _cj.HERMES_DIR, _cj.CRON_DIR, _cj.JOBS_FILE, _cj.OUTPUT_DIR = self._prev_cj
                except (ImportError, AttributeError):
                    pass
            if getattr(self, '_prev_cs', None) is not None:
                try:
                    import cron.scheduler as _cs
                    _cs._hermes_home, _cs._LOCK_DIR, _cs._LOCK_FILE = self._prev_cs
                except (ImportError, AttributeError):
                    pass
        finally:
            _pop_cron_profile_context_depth()
            _cron_env_lock.release()
        return False


class cron_profile_context:
    """Context manager that pins HERMES_HOME to the TLS-active profile.

    Usage:
        with cron_profile_context():
            from cron.jobs import list_jobs
            jobs = list_jobs(include_disabled=True)

    Serializes cron API calls across profiles (cron API is low-frequency;
    serialization cost is negligible compared to correctness).
    """

    def __enter__(self):
        _cron_env_lock.acquire()
        _push_cron_profile_context_depth()
        try:
            self._prev_env = os.environ.get('HERMES_HOME')
            home = get_active_hermes_home()
            os.environ['HERMES_HOME'] = str(home)

            # Re-patch cron.jobs module-level constants. They are snapshot at
            # import time (line 68-71 of cron/jobs.py) and don't participate in
            # the module's __getattr__ lazy path, so env-var alone is not enough
            # for callers that reference the module constants directly.
            self._prev_cj = None
            try:
                import cron.jobs as _cj
                self._prev_cj = (_cj.HERMES_DIR, _cj.CRON_DIR, _cj.JOBS_FILE, _cj.OUTPUT_DIR)
                _cj.HERMES_DIR = home
                _cj.CRON_DIR = home / 'cron'
                _cj.JOBS_FILE = _cj.CRON_DIR / 'jobs.json'
                _cj.OUTPUT_DIR = _cj.CRON_DIR / 'output'
            except (ImportError, AttributeError):
                logger.debug("cron_profile_context: cron.jobs unavailable; env-var only")

            self._prev_cs = None
            try:
                import cron.scheduler as _cs
                self._prev_cs = (
                    getattr(_cs, '_hermes_home', None),
                    getattr(_cs, '_LOCK_DIR', None),
                    getattr(_cs, '_LOCK_FILE', None),
                )
                _cs._hermes_home = home
                _cs._LOCK_DIR = home / 'cron'
                _cs._LOCK_FILE = _cs._LOCK_DIR / '.tick.lock'
            except (ImportError, AttributeError):
                logger.debug("cron_profile_context: cron.scheduler unavailable; env-var only")
        except Exception:
            _pop_cron_profile_context_depth()
            _cron_env_lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            # Restore env var
            if self._prev_env is None:
                os.environ.pop('HERMES_HOME', None)
            else:
                os.environ['HERMES_HOME'] = self._prev_env

            # Restore cron.jobs module constants
            if self._prev_cj is not None:
                try:
                    import cron.jobs as _cj
                    _cj.HERMES_DIR, _cj.CRON_DIR, _cj.JOBS_FILE, _cj.OUTPUT_DIR = self._prev_cj
                except (ImportError, AttributeError):
                    pass
            if getattr(self, '_prev_cs', None) is not None:
                try:
                    import cron.scheduler as _cs
                    _cs._hermes_home, _cs._LOCK_DIR, _cs._LOCK_FILE = self._prev_cs
                except (ImportError, AttributeError):
                    pass
        finally:
            _pop_cron_profile_context_depth()
            _cron_env_lock.release()
        return False


def get_hermes_home_for_profile(name: str) -> Path:
    """Return the HERMES_HOME Path for *name* without mutating any process state.

    Safe to call from per-request context (streaming, session creation) because
    it reads only the filesystem — it never touches os.environ, module-level
    cached paths, or the process-level _active_profile global.

    Falls back to _DEFAULT_HERMES_HOME (same as 'default') when *name* is None,
    empty, 'default', or does not match the profile-name format (rejects path
    traversal such as '../../etc').
    """
    return _resolve_profile_home_for_name(name)


_TERMINAL_ENV_MAPPINGS = {
    'backend': 'TERMINAL_ENV',
    'env_type': 'TERMINAL_ENV',
    'cwd': 'TERMINAL_CWD',
    'timeout': 'TERMINAL_TIMEOUT',
    'lifetime_seconds': 'TERMINAL_LIFETIME_SECONDS',
    'modal_mode': 'TERMINAL_MODAL_MODE',
    'docker_image': 'TERMINAL_DOCKER_IMAGE',
    'docker_forward_env': 'TERMINAL_DOCKER_FORWARD_ENV',
    'docker_env': 'TERMINAL_DOCKER_ENV',
    'docker_mount_cwd_to_workspace': 'TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE',
    'singularity_image': 'TERMINAL_SINGULARITY_IMAGE',
    'modal_image': 'TERMINAL_MODAL_IMAGE',
    'daytona_image': 'TERMINAL_DAYTONA_IMAGE',
    'container_cpu': 'TERMINAL_CONTAINER_CPU',
    'container_memory': 'TERMINAL_CONTAINER_MEMORY',
    'container_disk': 'TERMINAL_CONTAINER_DISK',
    'container_persistent': 'TERMINAL_CONTAINER_PERSISTENT',
    'docker_volumes': 'TERMINAL_DOCKER_VOLUMES',
    'persistent_shell': 'TERMINAL_PERSISTENT_SHELL',
    'ssh_host': 'TERMINAL_SSH_HOST',
    'ssh_user': 'TERMINAL_SSH_USER',
    'ssh_port': 'TERMINAL_SSH_PORT',
    'ssh_key': 'TERMINAL_SSH_KEY',
    'ssh_persistent': 'TERMINAL_SSH_PERSISTENT',
    'local_persistent': 'TERMINAL_LOCAL_PERSISTENT',
}


def _stringify_env_value(value) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


def get_profile_runtime_env(home: Path) -> dict[str, str]:
    """Return env vars needed to run an agent turn for a profile home.

    WebUI profile switching is per-client/cookie scoped, so it intentionally
    does not call ``switch_profile(..., process_wide=True)`` for every browser.
    Agent/tool code still consumes terminal backend settings through
    environment variables (matching ``hermes -p <profile>``), so streaming must
    apply the selected profile's terminal config and ``.env`` for the duration
    of that run.
    """
    home = Path(home).expanduser()
    env: dict[str, str] = {}

    try:
        import yaml as _yaml

        cfg_path = home / 'config.yaml'
        cfg = _yaml.safe_load(cfg_path.read_text(encoding='utf-8')) if cfg_path.exists() else {}
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}

    terminal_cfg = cfg.get('terminal', {}) if isinstance(cfg, dict) else {}
    if isinstance(terminal_cfg, dict):
        for key, env_key in _TERMINAL_ENV_MAPPINGS.items():
            if key in terminal_cfg and terminal_cfg[key] is not None:
                env[env_key] = _stringify_env_value(terminal_cfg[key])

    env_path = home / '.env'
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and v:
                        env[k] = v
        except Exception:
            logger.debug("Failed to read runtime env from %s", env_path)

    return env


def _set_hermes_home(home: Path):
    """Set HERMES_HOME env var and monkey-patch cached module-level paths."""
    os.environ['HERMES_HOME'] = str(home)

    patch_skill_home_modules(home)

    # Patch cron/jobs module-level cache
    try:
        import cron.jobs as _cj
        _cj.HERMES_DIR = home
        _cj.CRON_DIR = home / 'cron'
        _cj.JOBS_FILE = _cj.CRON_DIR / 'jobs.json'
        _cj.OUTPUT_DIR = _cj.CRON_DIR / 'output'
    except (ImportError, AttributeError):
        logger.debug("Failed to patch cron.jobs module")

    try:
        import cron.scheduler as _cs
        _cs._hermes_home = home
        _cs._LOCK_DIR = home / 'cron'
        _cs._LOCK_FILE = _cs._LOCK_DIR / '.tick.lock'
    except (ImportError, AttributeError):
        logger.debug("Failed to patch cron.scheduler module")


def _reload_dotenv(home: Path):
    """Load .env from the profile dir into os.environ with profile isolation.

    Clears env vars that were loaded from the previously active profile before
    applying the current profile's .env. This prevents API keys and other
    profile-scoped secrets from leaking across profile switches.
    """
    global _loaded_profile_env_keys

    # Remove keys loaded from the previous profile first.
    for key in list(_loaded_profile_env_keys):
        os.environ.pop(key, None)
    _loaded_profile_env_keys = set()

    env_path = home / '.env'
    if not env_path.exists():
        return
    try:
        loaded_keys: set[str] = set()
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v:
                    os.environ[k] = v
                    loaded_keys.add(k)
        _loaded_profile_env_keys = loaded_keys
    except Exception:
        _loaded_profile_env_keys = set()
        logger.debug("Failed to reload dotenv from %s", env_path)


def init_profile_state() -> None:
    """Initialize profile state at server startup.

    Reads ~/.hermes/active_profile, sets HERMES_HOME env var, patches
    module-level cached paths.  Called once from config.py after imports.
    """
    global _active_profile
    _active_profile = _read_active_profile_file()
    home = get_active_hermes_home()
    _set_hermes_home(home)
    install_cron_scheduler_profile_isolation()
    _reload_dotenv(home)


def switch_profile(name: str, *, process_wide: bool = True) -> dict:
    """Switch the active profile.

    Validates the profile exists, updates process state, patches module caches,
    reloads .env, and reloads config.yaml.

    Args:
        name: Profile name to switch to.
        process_wide: If True (default), updates the process-global
            _active_profile.  Set to False for per-client switches from the
            WebUI where the profile is managed via cookie + thread-local (#798).

    Returns: {'profiles': [...], 'active': name}
    Raises ValueError if profile doesn't exist or agent is busy.
    """
    global _active_profile

    # Import here to avoid circular import at module load
    from api.config import STREAMS, STREAMS_LOCK, reload_config

    # Process-wide profile switches mutate HERMES_HOME, module-level path caches,
    # os.environ-backed .env keys, and the global config cache. Keep those blocked
    # while any agent stream is active. Per-client WebUI switches are cookie/TLS
    # scoped (process_wide=False) and do not mutate those globals, so users can
    # leave a running session in one profile and start work in another (#1700).
    if process_wide:
        with STREAMS_LOCK:
            if len(STREAMS) > 0:
                raise RuntimeError(
                    'Cannot switch profiles while an agent is running. '
                    'Cancel or wait for it to finish.'
                )

    # Resolve profile directory
    if _is_root_profile(name):
        home = _DEFAULT_HERMES_HOME
    else:
        home = _resolve_named_profile_home(name)
        if not home.is_dir():
            raise ValueError(f"Profile '{name}' does not exist.")

    with _profile_lock:
        if process_wide:
            global _active_profile
            _active_profile = name
            _set_hermes_home(home)
            _reload_dotenv(home)

    if process_wide:
        # Write sticky default for CLI consistency
        try:
            ap_file = _DEFAULT_HERMES_HOME / 'active_profile'
            ap_file.write_text('' if _is_root_profile(name) else name, encoding='utf-8')
        except Exception:
            logger.debug("Failed to write active profile file")

        # Reload config.yaml from the new profile
        reload_config()

    # Return profile-specific defaults so frontend can apply them.
    # For process_wide=False (per-client switch), read the target profile's
    # config.yaml directly from disk rather than from _cfg_cache (process-global),
    # since reload_config() was intentionally skipped.
    if process_wide:
        from api.config import get_config
        cfg = get_config()
    else:
        # Direct disk read — does not touch _cfg_cache
        try:
            import yaml as _yaml
            cfg_path = home / 'config.yaml'
            cfg = _yaml.safe_load(cfg_path.read_text(encoding='utf-8')) if cfg_path.exists() else {}
            if not isinstance(cfg, dict):
                cfg = {}
        except Exception:
            cfg = {}
    model_cfg = cfg.get('model', {})
    default_model = None
    if isinstance(model_cfg, str):
        default_model = model_cfg
    elif isinstance(model_cfg, dict):
        default_model = model_cfg.get('default')

    # Read the target profile's workspace directly from *home* rather than via
    # get_last_workspace() which routes through the thread-local/process-global active
    # profile — both of which still point to the OLD profile during process_wide=False
    # switches (the Set-Cookie has been sent but hasn't been processed by a new request
    # yet).  We derive workspace in priority order:
    #   1. {home}/webui_state/last_workspace.txt  (previously chosen workspace for this profile)
    #   2. cfg terminal.cwd / workspace / default_workspace keys
    #   3. Boot-time DEFAULT_WORKSPACE constant
    # Use the module-level ``Path`` (imported at line 17) rather than re-importing
    # it locally — keeps the exception fallback simple and avoids a latent NameError
    # if a future refactor moves the inner imports.
    default_workspace = None
    try:
        from api.config import DEFAULT_WORKSPACE as _DW
        lw_file = home / 'webui_state' / 'last_workspace.txt'
        if lw_file.exists():
            _p = lw_file.read_text(encoding='utf-8').strip()
            if _p:
                _pp = Path(_p).expanduser()
                if _pp.is_dir():
                    default_workspace = str(_pp.resolve())
        if default_workspace is None:
            for _key in ('workspace', 'default_workspace'):
                _v = cfg.get(_key)
                if _v:
                    _pp = Path(str(_v)).expanduser().resolve()
                    if _pp.is_dir():
                        default_workspace = str(_pp)
                        break
        if default_workspace is None:
            _tc = cfg.get('terminal', {})
            if isinstance(_tc, dict):
                _cwd = _tc.get('cwd', '')
                if _cwd and str(_cwd) not in ('.', ''):
                    _pp = Path(str(_cwd)).expanduser().resolve()
                    if _pp.is_dir():
                        default_workspace = str(_pp)
        if default_workspace is None:
            default_workspace = str(_DW)
    except Exception:
        try:
            from api.config import DEFAULT_WORKSPACE as _DW2
            default_workspace = str(_DW2)
        except Exception:
            default_workspace = str(Path.home())

    return {
        'profiles': list_profiles_api(),
        'active': name,
        'default_model': default_model,
        'default_workspace': default_workspace,
    }


_MISSING = object()
_PROFILE_SETTINGS_FILE = 'profile_settings.json'
_AVATAR_TYPES = {'emoji', 'url', 'asset', 'image'}
_MAX_AVATAR_VALUE_LEN = 4 * 1024 * 1024
_MAX_EMOJI_AVATAR_LEN = 64
_IMAGE_AVATAR_RE = re.compile(r'^data:image/(png|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=\s]+$')


def _validate_profile_settings_name(name: str) -> str:
    """Validate a profile name for settings reads/writes."""
    if not isinstance(name, str):
        name = str(name or '')
    name = name.strip()
    if not name:
        raise ValueError('name is required')
    if name == 'default':
        return name
    if not _PROFILE_ID_RE.fullmatch(name):
        raise ValueError(
            f"Invalid profile name {name!r}. "
            "Must match [a-z0-9][a-z0-9_-]{0,63}"
        )
    return name


def _require_profile_home_for_settings(name: str) -> tuple[str, Path]:
    """Return a validated profile name and existing profile home path."""
    name = _validate_profile_settings_name(name)
    if _is_root_profile(name):
        home = _DEFAULT_HERMES_HOME
    else:
        home = _resolve_named_profile_home(name)
    if not home.is_dir():
        raise FileNotFoundError(f"Profile '{name}' not found.")
    return name, home


def _load_profile_config_for_settings(profile_home: Path) -> dict:
    config_path = profile_home / 'config.yaml'
    if not config_path.exists():
        return {}
    try:
        import yaml as _yaml
        loaded = _yaml.safe_load(config_path.read_text(encoding='utf-8'))
    except Exception:
        logger.debug("Failed to load profile settings config from %s", config_path, exc_info=True)
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _save_profile_config_for_settings(profile_home: Path, config_data: dict) -> None:
    try:
        import yaml as _yaml
    except ImportError as exc:
        raise RuntimeError('PyYAML is required to update profile settings') from exc
    config_path = profile_home / 'config.yaml'
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        _yaml.safe_dump(config_data, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding='utf-8',
    )


# Mirrors api.config.VALID_REASONING_EFFORTS without importing — keeps profile
# settings independent of the active-profile reasoning helpers in api.config.
_VALID_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")


def _extract_profile_reasoning_effort(config_data: dict) -> str:
    agent_cfg = config_data.get('agent') if isinstance(config_data, dict) else None
    if not isinstance(agent_cfg, dict):
        return ''
    raw = agent_cfg.get('reasoning_effort')
    if raw is None:
        return ''
    return str(raw).strip().lower()


def _merge_profile_reasoning_effort(config_data: dict, effort) -> bool:
    """Apply *effort* into ``agent.reasoning_effort`` on *config_data*.

    Returns True when the config changed. Accepts ``''`` (unset),
    ``'none'`` (explicitly disabled), or any value in
    ``_VALID_REASONING_EFFORTS``. Raises ValueError on unknown values.
    """
    if not isinstance(effort, str):
        raise ValueError('reasoning_effort must be a string')
    raw = effort.strip().lower()
    if raw and raw != 'none' and raw not in _VALID_REASONING_EFFORTS:
        raise ValueError(
            f"Unknown reasoning effort '{effort}'. "
            f"Valid: none, {', '.join(_VALID_REASONING_EFFORTS)}."
        )
    agent_cfg = config_data.get('agent')
    if not isinstance(agent_cfg, dict):
        agent_cfg = {}
        had_agent = False
    else:
        agent_cfg = dict(agent_cfg)
        had_agent = True
    before_effort = agent_cfg.get('reasoning_effort')
    if not raw:
        # Empty string means "remove the override" — use profile default.
        if 'reasoning_effort' in agent_cfg:
            agent_cfg.pop('reasoning_effort', None)
            changed = True
        else:
            changed = False
    else:
        agent_cfg['reasoning_effort'] = raw
        changed = before_effort != raw
    if not changed and had_agent:
        return False
    if agent_cfg:
        config_data['agent'] = agent_cfg
    elif had_agent:
        # Removed the last key from agent; collapse the empty section.
        config_data.pop('agent', None)
    return changed


def _extract_profile_model_settings(config_data: dict) -> tuple[str | None, str | None]:
    model_cfg = config_data.get('model') if isinstance(config_data, dict) else None
    if isinstance(model_cfg, dict):
        model = model_cfg.get('default') or model_cfg.get('model') or model_cfg.get('name')
        provider = model_cfg.get('provider')
    elif isinstance(model_cfg, str):
        model = model_cfg
        provider = None
    else:
        model = None
        provider = None
    model_s = str(model).strip() if model is not None else None
    provider_s = str(provider).strip() if provider is not None else None
    return (model_s or None, provider_s or None)


def _normalize_model_provider_inputs(provider, model) -> tuple[str | object, str | object]:
    normalized_provider = provider
    normalized_model = model
    if model is not _MISSING:
        if not isinstance(model, str):
            raise ValueError('model must be a string')
        selected = model.strip()
        if not selected:
            raise ValueError('model is required')
        if selected.startswith('@') and ':' in selected:
            provider_hint, bare_model = selected[1:].split(':', 1)
            provider_hint = provider_hint.strip()
            bare_model = bare_model.strip()
            if not bare_model:
                raise ValueError('model is required')
            selected = bare_model
            if provider is _MISSING and provider_hint:
                normalized_provider = provider_hint
        normalized_model = selected
    if normalized_provider is not _MISSING:
        if normalized_provider is None:
            pass
        elif not isinstance(normalized_provider, str):
            raise ValueError('provider must be a string')
        else:
            normalized_provider = normalized_provider.strip()
            if not normalized_provider:
                raise ValueError('provider is required')
    return normalized_provider, normalized_model


def _merge_profile_model_settings(config_data: dict, provider, model) -> bool:
    provider, model = _normalize_model_provider_inputs(provider, model)
    current = config_data.get('model')
    if isinstance(current, dict):
        model_cfg = dict(current)
    elif isinstance(current, str) and current.strip():
        model_cfg = {'default': current.strip()}
    else:
        model_cfg = {}

    before = dict(model_cfg)
    if model is not _MISSING:
        model_cfg['default'] = model
    if provider is not _MISSING:
        if provider is None:
            model_cfg.pop('provider', None)
        else:
            model_cfg['provider'] = provider
    config_data['model'] = model_cfg
    return before != model_cfg


def _profile_settings_state_path(profile_home: Path) -> Path:
    return profile_home / 'webui_state' / _PROFILE_SETTINGS_FILE


def _read_profile_settings_state(profile_home: Path) -> dict:
    path = _profile_settings_state_path(profile_home)
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        logger.debug("Failed to load WebUI profile settings from %s", path, exc_info=True)
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_profile_settings_state(profile_home: Path, state: dict) -> None:
    path = _profile_settings_state_path(profile_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    tmp.replace(path)


def _read_profile_avatar_for_home(profile_home: Path):
    state = _read_profile_settings_state(profile_home)
    avatar = state.get('avatar')
    return avatar if isinstance(avatar, dict) else None


def _normalize_avatar_payload(avatar):
    if avatar is None:
        return None
    if not isinstance(avatar, dict):
        raise ValueError('avatar must be an object')
    avatar_type = str(avatar.get('type') or '').strip().lower()
    value = avatar.get('value')
    if avatar_type not in _AVATAR_TYPES:
        raise ValueError('avatar type must be emoji, url, asset, or image')
    if not isinstance(value, str):
        raise ValueError('avatar value must be a string')
    value = value.strip()
    if not value:
        raise ValueError('avatar value is required')
    if len(value) > _MAX_AVATAR_VALUE_LEN:
        raise ValueError('avatar value is too large')
    if avatar_type == 'emoji' and len(value) > _MAX_EMOJI_AVATAR_LEN:
        raise ValueError('emoji avatar value is too large')
    if avatar_type == 'url':
        from urllib.parse import urlparse
        parsed = urlparse(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
            raise ValueError('avatar URL must start with http:// or https://')
    if avatar_type == 'image':
        compact_value = ''.join(value.split())
        if not _IMAGE_AVATAR_RE.fullmatch(compact_value):
            raise ValueError('uploaded avatar must be a PNG, JPEG, GIF, or WebP data image')
        value = compact_value
    if avatar_type == 'asset':
        if value.startswith(('/', '\\')) or '..' in value.split('/') or '\\' in value or ':' in value:
            raise ValueError('avatar asset must be a safe relative asset path')
    return {'type': avatar_type, 'value': value}


def get_profile_settings_api(name: str) -> dict:
    """Return structured WebUI settings for a profile."""
    name, profile_home = _require_profile_home_for_settings(name)
    config_data = _load_profile_config_for_settings(profile_home)
    model, provider = _extract_profile_model_settings(config_data)
    return {
        'name': name,
        'provider': provider,
        'model': model,
        'avatar': _read_profile_avatar_for_home(profile_home),
        'reasoning_effort': _extract_profile_reasoning_effort(config_data),
        'description': _extract_profile_description(config_data),
    }


# ── Profile description (profile screen rework v3.1 — 2026-05-15) ─────────
#
# The hero dossier on the profile detail screen shows a short user-authored
# description distinct from SOUL.md (which carries the agent's persona /
# voice for the model). Persisted at `webui.description` inside the profile's
# config.yaml, capped at _PROFILE_DESCRIPTION_MAX chars to keep the dossier
# from turning into an essay. The persona endpoint returns it; the existing
# /api/profile/settings POST writes it.

_PROFILE_DESCRIPTION_MAX = 280


def _extract_profile_description(config_data: dict) -> str:
    """Return the user-set description string from a loaded config blob.

    Reads ``webui.description``. Missing/non-string values become ``''``.
    """
    if not isinstance(config_data, dict):
        return ''
    webui_cfg = config_data.get('webui')
    if not isinstance(webui_cfg, dict):
        return ''
    raw = webui_cfg.get('description')
    return str(raw).strip() if isinstance(raw, str) else ''


def _merge_profile_description(config_data: dict, description) -> bool:
    """Apply *description* into ``webui.description`` on *config_data*.

    Empty / None removes the override and collapses the ``webui`` section if
    it becomes empty. Returns True when the config changed. Raises
    ValueError on non-string inputs or strings longer than the hard cap.
    """
    if description is None:
        description = ''
    if not isinstance(description, str):
        raise ValueError('description must be a string')
    new_value = description.strip()
    if len(new_value) > _PROFILE_DESCRIPTION_MAX:
        raise ValueError(
            f"description must be <= {_PROFILE_DESCRIPTION_MAX} characters"
        )
    webui_cfg = config_data.get('webui')
    if not isinstance(webui_cfg, dict):
        webui_cfg = {}
        had_webui = False
    else:
        webui_cfg = dict(webui_cfg)
        had_webui = True
    before = webui_cfg.get('description')
    if not new_value:
        if 'description' in webui_cfg:
            webui_cfg.pop('description', None)
            changed = True
        else:
            changed = False
    else:
        webui_cfg['description'] = new_value
        changed = before != new_value
    if not changed and had_webui:
        return False
    if webui_cfg:
        config_data['webui'] = webui_cfg
    elif had_webui:
        config_data.pop('webui', None)
    return changed


# Legacy helper retained for the file-read flow / tests that exercise SOUL
# parsing; the persona endpoint no longer routes through it.
def _first_non_blank_paragraph(text: str) -> str:
    """Return the first non-blank, non-heading-only paragraph from a markdown blob.

    Heading marks ('#') are stripped, but a paragraph that contains *only*
    headings is skipped in favour of a paragraph that has at least one body
    line. Joined lines are space-separated so a wrapped voice quote survives.
    """
    for raw in text.split('\n\n'):
        para = raw.strip()
        if not para:
            continue
        kept = []
        for line in para.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            is_heading = stripped.startswith('#')
            text_line = stripped.lstrip('#').strip() if is_heading else stripped
            kept.append((text_line, is_heading))
        if not kept:
            continue
        if all(h for _, h in kept):
            # Whole paragraph is headings — look at the next paragraph instead.
            continue
        body = ' '.join(t for t, h in kept if t and not h) or ' '.join(t for t, _ in kept if t)
        body = body.strip()
        if body:
            return body
    return ''


# ── Activity aggregator (profile screen rework 2026-05-14) ─────────────────
#
# The activity line on the reworked profile screen reports last-used,
# sessions-this-week, optional spend, and the gateway's last-run timestamp.
# Sessions live in a single global WebUI index keyed by profile name, so the
# aggregation is a pure data filter; the gateway timestamp lives inside the
# profile's HOME at .gateway-state.json, written by the gateway control path
# on successful start (Task 6 below).

_ACTIVITY_WINDOW_DAYS = 7


def _compute_profile_activity(rows, name: str, *, now: float) -> dict:
    """Aggregate session-index *rows* for profile *name*.

    ``sessions_week`` is the count of profile-tagged sessions within the
    last 7 days. ``last_used_at`` is the most-recent ``updated_at`` for
    this profile across ALL time — a profile last touched 30 days ago
    should still report a non-null last-used (just outside the weekly
    window). Validator F#15 caught the prior version scoping both signals
    to the same cutoff.

    A row whose ``profile`` field is missing is treated as belonging to
    the default profile (matches the index's pre-multi-profile shape).
    Spend is intentionally ``None`` in v1 — the UI hides the segment
    until cost tracking lands.
    """
    import datetime as _dt

    cutoff = now - _ACTIVITY_WINDOW_DAYS * 86400.0
    all_timestamps = []     # unbounded — for last_used_at
    window_timestamps = []  # within cutoff — for sessions_week
    for r in rows or ():
        if not isinstance(r, dict):
            continue
        row_profile = r.get('profile') or 'default'
        if row_profile != name:
            continue
        ts = r.get('updated_at')
        if not isinstance(ts, (int, float)):
            continue
        all_timestamps.append(ts)
        if ts >= cutoff:
            window_timestamps.append(ts)

    last_used_at = None
    if all_timestamps:
        most_recent = max(all_timestamps)
        last_used_at = _dt.datetime.fromtimestamp(
            most_recent, tz=_dt.timezone.utc
        ).isoformat().replace('+00:00', 'Z')

    return {
        'sessions_week': len(window_timestamps),
        'last_used_at': last_used_at,
        'spend_week_usd': None,
    }


def _read_gateway_state(profile_home: Path) -> dict:
    """Read .gateway-state.json — return {} on missing or malformed file."""
    state_path = profile_home / '.gateway-state.json'
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


# Grace window for gateway start before a missing/dead PID is considered
# a failure. Tuned for Telegram + Slack adapter cold-start latency.
GATEWAY_START_GRACE_SECONDS = 8


def _write_gateway_phase(
    profile_home: Path,
    phase: str,
    *,
    last_error: str | None = None,
    started_at: str | None = None,
) -> None:
    """Stamp the gateway phase in .gateway-state.json without clobbering
    sibling fields (e.g. last_run_at).

    phase values:
      'starting'  — set phase + phase_started_at + clear last_error
      'stopping'  — set phase + phase_started_at + clear last_error
      'running'   — set phase + phase_started_at + clear last_error
      'failed'    — set phase + phase_started_at + record last_error
      'stopped'   — clear phase, phase_started_at, last_error

    If ``started_at`` is supplied, it is used verbatim for
    ``phase_started_at`` on the non-stopped phases (preserves the original
    transition timestamp during promotion). Otherwise a fresh "now" is
    stamped. The 'stopped' phase always clears ``phase_started_at``
    regardless of ``started_at``.
    """
    import datetime as _dt
    state_path = profile_home / '.gateway-state.json'
    payload: dict = {}
    if state_path.exists():
        try:
            existing = json.loads(state_path.read_text(encoding='utf-8'))
            if isinstance(existing, dict):
                payload = existing
        except (ValueError, OSError):
            payload = {}

    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat().replace('+00:00', 'Z')
    timestamp = started_at if started_at else now_iso

    if phase == 'stopped':
        payload['phase'] = None
        payload['phase_started_at'] = None
        payload['last_error'] = None
    elif phase == 'failed':
        payload['phase'] = 'failed'
        payload['phase_started_at'] = timestamp
        payload['last_error'] = last_error
    elif phase in ('starting', 'stopping', 'running'):
        payload['phase'] = phase
        payload['phase_started_at'] = timestamp
        payload['last_error'] = None
    else:
        raise ValueError(f"unknown gateway phase: {phase!r}")

    try:
        state_path.write_text(json.dumps(payload), encoding='utf-8')
    except OSError:
        logger.debug("Failed to write gateway phase state", exc_info=True)


def _load_session_index_rows() -> list:
    """Best-effort read of the WebUI session index. Returns [] on any failure.

    The index is global to the WebUI installation (not per-profile), so we
    can compute activity for any profile from a single read.
    """
    try:
        from api.config import SESSION_INDEX_FILE
    except ImportError:
        return []
    if not SESSION_INDEX_FILE.exists():
        return []
    try:
        data = json.loads(SESSION_INDEX_FILE.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return []
    return data if isinstance(data, list) else []


def read_profile_activity_api(name: str) -> dict:
    """Return aggregated activity signals for the profile detail screen.

    Raises:
        ValueError: invalid profile name.
        FileNotFoundError: profile directory does not exist.
    """
    import time as _time
    name, profile_home = _require_profile_home_for_settings(name)
    rows = _load_session_index_rows()
    agg = _compute_profile_activity(rows, name, now=_time.time())
    state = _read_gateway_state(profile_home)
    gateway_last = state.get('last_run_at') if isinstance(state.get('last_run_at'), str) else None
    return {
        'name': name,
        'sessions_week': agg['sessions_week'],
        'last_used_at': agg['last_used_at'],
        'ever_started_gateway': gateway_last is not None,
        'gateway_last_run_at': gateway_last,
        'spend_week_usd': agg['spend_week_usd'],
    }


def read_profile_persona_api(name: str) -> dict:
    """Return the user-authored description for *name*.

    The hero dossier renders this line. It is stored at
    ``webui.description`` in the profile's config.yaml and is intentionally
    separate from SOUL.md, which carries the agent's persona / voice for
    the model itself. Empty when no description has been set yet — the UI
    surfaces an "Add a description" placeholder in that case.

    Raises:
        ValueError: invalid profile name.
        FileNotFoundError: profile directory does not exist.
    """
    name, profile_home = _require_profile_home_for_settings(name)
    config_data = _load_profile_config_for_settings(profile_home)
    description = _extract_profile_description(config_data)
    return {
        'name': name,
        'description': description,
    }


def update_profile_settings_api(name: str, *, provider=_MISSING, model=_MISSING,
                                avatar=_MISSING, reasoning_effort=_MISSING,
                                description=_MISSING) -> dict:
    """Update model/provider, reasoning effort, description and/or WebUI avatar metadata."""
    if (provider is _MISSING and model is _MISSING
            and avatar is _MISSING and reasoning_effort is _MISSING
            and description is _MISSING):
        raise ValueError(
            'At least one of provider, model, avatar, reasoning_effort, or description is required'
        )
    name, profile_home = _require_profile_home_for_settings(name)

    needs_yaml_write = (
        provider is not _MISSING or model is not _MISSING
        or reasoning_effort is not _MISSING or description is not _MISSING
    )
    invalidate_models = False
    if needs_yaml_write:
        config_data = _load_profile_config_for_settings(profile_home)
        config_changed = False
        if provider is not _MISSING or model is not _MISSING:
            if _merge_profile_model_settings(config_data, provider, model):
                config_changed = True
                invalidate_models = True
        if reasoning_effort is not _MISSING:
            if _merge_profile_reasoning_effort(config_data, reasoning_effort):
                config_changed = True
        if description is not _MISSING:
            if _merge_profile_description(config_data, description):
                config_changed = True
        if config_changed:
            _save_profile_config_for_settings(profile_home, config_data)
        if invalidate_models:
            from api.config import invalidate_models_cache
            invalidate_models_cache()

    if avatar is not _MISSING:
        normalized_avatar = _normalize_avatar_payload(avatar)
        state = _read_profile_settings_state(profile_home)
        if normalized_avatar is None:
            state.pop('avatar', None)
        else:
            state['avatar'] = normalized_avatar
        _write_profile_settings_state(profile_home, state)

    return get_profile_settings_api(name)


def list_profiles_api() -> list:
    """List all profiles with metadata, serialized for JSON response."""
    try:
        from hermes_cli.profiles import list_profiles
        infos = list_profiles()
    except ImportError:
        # hermes_cli not available -- return just the default
        return [_default_profile_dict()]

    active = get_active_profile_name()
    result = []
    for p in infos:
        result.append({
            'name': p.name,
            'path': str(p.path),
            'is_default': p.is_default,
            'is_active': p.name == active,
            'gateway_running': p.gateway_running,
            'model': p.model,
            'provider': p.provider,
            'avatar': _read_profile_avatar_for_home(Path(p.path)),
            'has_env': p.has_env,
            'skill_count': p.skill_count,
        })
    return result


def _default_profile_dict() -> dict:
    """Fallback profile dict when hermes_cli is not importable."""
    return {
        'name': 'default',
        'path': str(_DEFAULT_HERMES_HOME),
        'is_default': True,
        'is_active': True,
        'gateway_running': False,
        'model': None,
        'provider': None,
        'avatar': _read_profile_avatar_for_home(_DEFAULT_HERMES_HOME),
        'has_env': (_DEFAULT_HERMES_HOME / '.env').exists(),
        'skill_count': 0,
    }


def _validate_profile_name(name: str):
    """Validate profile name format (matches hermes_cli.profiles upstream)."""
    if name == 'default':
        raise ValueError("Cannot create a profile named 'default' -- it is the built-in profile.")
    # Use fullmatch (not match) so a trailing newline can't sneak past the $ anchor
    if not _PROFILE_ID_RE.fullmatch(name):
        raise ValueError(
            f"Invalid profile name {name!r}. "
            "Must match [a-z0-9][a-z0-9_-]{0,63}"
        )


# Newly-created or renamed profiles are capped at a tighter limit than the
# regex's 64-char back-compat ceiling — long names overflow the hero card
# layout. Keep the regex permissive on read (so existing 33-64 char profiles
# still load) but reject anything longer on write.
_PROFILE_NAME_NEW_MAX_LEN = 32


def _enforce_new_profile_name_cap(name: str) -> None:
    if len(name) > _PROFILE_NAME_NEW_MAX_LEN:
        raise ValueError(
            f"Profile name must be {_PROFILE_NAME_NEW_MAX_LEN} characters or fewer "
            f"(got {len(name)})."
        )


def _profiles_root() -> Path:
    """Return the canonical root that contains named profiles."""
    return (_DEFAULT_HERMES_HOME / 'profiles').resolve()


def _resolve_named_profile_home(name: str) -> Path:
    """Resolve a named profile to a directory under the profiles root.

    Validates *name* as a logical profile identifier first, then resolves the
    final filesystem path and enforces containment under ~/.hermes/profiles.
    """
    _validate_profile_name(name)
    profiles_root = _profiles_root()
    candidate = (profiles_root / name).resolve()
    candidate.relative_to(profiles_root)
    return candidate


def _create_profile_fallback(name: str, clone_from: str = None,
                              clone_config: bool = False) -> Path:
    """Create a profile directory without hermes_cli (Docker/standalone fallback)."""
    profile_dir = _DEFAULT_HERMES_HOME / 'profiles' / name
    if profile_dir.exists():
        raise FileExistsError(f"Profile '{name}' already exists.")

    # Bootstrap directory structure (exist_ok=False so a concurrent create raises)
    profile_dir.mkdir(parents=True, exist_ok=False)
    for subdir in _PROFILE_DIRS:
        (profile_dir / subdir).mkdir(parents=True, exist_ok=True)

    # Clone config files from source profile if requested
    if clone_config and clone_from:
        if _is_root_profile(clone_from):
            source_dir = _DEFAULT_HERMES_HOME
        else:
            source_dir = _DEFAULT_HERMES_HOME / 'profiles' / clone_from
        if source_dir.is_dir():
            for filename in _CLONE_CONFIG_FILES:
                src = source_dir / filename
                if src.exists():
                    shutil.copy2(src, profile_dir / filename)

    return profile_dir


def _write_endpoint_to_config(profile_dir: Path, base_url: str = None, api_key: str = None) -> None:
    """Write custom endpoint fields into config.yaml for a profile."""
    if not base_url and not api_key:
        return
    config_path = profile_dir / 'config.yaml'
    try:
        import yaml as _yaml
    except ImportError:
        return
    cfg = {}
    if config_path.exists():
        try:
            loaded = _yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cfg = loaded
        except Exception:
            logger.debug("Failed to load config from %s", config_path)
    model_section = cfg.get('model', {})
    if not isinstance(model_section, dict):
        model_section = {}
    if base_url:
        model_section['base_url'] = base_url
    if api_key:
        model_section['api_key'] = api_key
    cfg['model'] = model_section
    config_path.write_text(_yaml.dump(cfg, default_flow_style=False, allow_unicode=True), encoding='utf-8')


def create_profile_api(name: str, clone_from: str = None,
                       clone_config: bool = False,
                       base_url: str = None,
                       api_key: str = None) -> dict:
    """Create a new profile. Returns the new profile info dict."""
    _validate_profile_name(name)
    _enforce_new_profile_name_cap(name)
    # Defense-in-depth: validate clone_from here too, even though routes.py
    # also validates it. Any caller that bypasses the HTTP layer gets protection.
    if clone_from is not None and not _is_root_profile(clone_from):
        _validate_profile_name(clone_from)

    try:
        from hermes_cli.profiles import create_profile
        create_profile(
            name,
            clone_from=clone_from,
            clone_config=clone_config,
            clone_all=False,
            no_alias=True,
        )
    except ImportError:
        _create_profile_fallback(name, clone_from, clone_config)

    # Resolve the profile directory from the profile list when possible.
    # hermes_cli and the webui runtime do not always agree on the exact root,
    # so we prefer the path returned by list_profiles_api() and fall back to the
    # standard profile location only if the profile cannot be found there yet.
    profile_path = _DEFAULT_HERMES_HOME / 'profiles' / name
    for p in list_profiles_api():
        if p['name'] == name:
            try:
                profile_path = Path(p.get('path') or profile_path)
            except Exception:
                logger.debug("Failed to parse profile path")
            break

    profile_path.mkdir(parents=True, exist_ok=True)
    _write_endpoint_to_config(profile_path, base_url=base_url, api_key=api_key)

    # Invalidate cached root-profile-name lookup; create_profile may have added
    # a new profile that flips is_default semantics on the agent side (#1612).
    _invalidate_root_profile_cache()

    # Find and return the newly created profile info.
    # When hermes_cli is not importable, list_profiles_api() also falls back
    # to the stub default-only list and won't find the new profile by name.
    # In that case, return a complete profile dict directly.
    for p in list_profiles_api():
        if p['name'] == name:
            return p
    return {
        'name': name,
        'path': str(profile_path),
        'is_default': False,
        'is_active': _active_profile == name,
        'gateway_running': False,
        'model': None,
        'provider': None,
        'has_env': (profile_path / '.env').exists(),
        'skill_count': 0,
    }


def rename_profile_api(name: str, new_name: str) -> dict:
    """Rename a profile. Refuses the default profile.

    Falls back to a filesystem rename when ``hermes_cli.profiles.rename_profile``
    is not importable. Returns ``{'ok': True, 'old_name', 'new_name',
    'was_active'}`` so callers can refresh the active-profile cookie.
    """
    if _is_root_profile(name):
        raise ValueError("Cannot rename the default profile.")
    _validate_profile_name(name)
    if not isinstance(new_name, str):
        raise ValueError("new_name is required")
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("new_name is required")
    if new_name == name:
        raise ValueError("new_name must differ from current name.")
    _validate_profile_name(new_name)
    _enforce_new_profile_name_cap(new_name)

    profiles_root = _profiles_root()
    src_dir = _resolve_named_profile_home(name)
    if not src_dir.is_dir():
        raise FileNotFoundError(f"Profile '{name}' does not exist.")
    dst_dir = (profiles_root / new_name).resolve()
    dst_dir.relative_to(profiles_root)
    if dst_dir.exists():
        raise FileExistsError(f"Profile '{new_name}' already exists.")

    global _active_profile
    was_active = _active_profile == name

    try:
        from hermes_cli.profiles import rename_profile as _cli_rename
    except ImportError:
        _cli_rename = None

    if _cli_rename is not None:
        try:
            _cli_rename(name, new_name)
        except TypeError:
            # Older signature variants might require keyword arguments.
            _cli_rename(old_name=name, new_name=new_name)
    else:
        # Filesystem fallback: rename the directory in place.
        src_dir.rename(dst_dir)

    if was_active:
        # Update the process-global active profile so subsequent requests
        # without a cookie still resolve to the renamed directory.
        with _profile_lock:
            _active_profile = new_name

    _invalidate_root_profile_cache()
    return {'ok': True, 'old_name': name, 'new_name': new_name, 'was_active': was_active}


def duplicate_profile_api(name: str, new_name: str, *, clone_all: bool = False) -> dict:
    """Duplicate a profile. Copies config and (when supported) WebUI state.

    By default ``clone_all=False`` clones only config files. Pass
    ``clone_all=True`` to mirror everything supported by the CLI duplicate
    semantics.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required")
    if not isinstance(new_name, str) or not new_name.strip():
        raise ValueError("new_name is required")
    name = name.strip()
    new_name = new_name.strip()
    if name == new_name:
        raise ValueError("new_name must differ from source name.")
    _validate_profile_name(new_name)
    _enforce_new_profile_name_cap(new_name)

    profiles_root = _profiles_root()
    dst_dir = (profiles_root / new_name).resolve()
    dst_dir.relative_to(profiles_root)
    if dst_dir.exists():
        raise FileExistsError(f"Profile '{new_name}' already exists.")

    # Resolve source directory: root profile maps to ~/.hermes
    if _is_root_profile(name):
        src_dir = _DEFAULT_HERMES_HOME
    else:
        _validate_profile_name(name)
        src_dir = _resolve_named_profile_home(name)
    if not src_dir.is_dir():
        raise FileNotFoundError(f"Profile '{name}' does not exist.")

    try:
        from hermes_cli.profiles import create_profile as _cli_create_profile
    except ImportError:
        _cli_create_profile = None

    if _cli_create_profile is not None:
        try:
            _cli_create_profile(
                new_name,
                clone_from=name,
                clone_config=True,
                clone_all=bool(clone_all),
                no_alias=True,
            )
        except TypeError:
            _cli_create_profile(new_name, clone_from=name, clone_config=True)
    else:
        # Filesystem fallback: copy config files (and optionally additional dirs).
        _create_profile_fallback(new_name, clone_from=name, clone_config=True)
        if clone_all:
            for sub in ('memories', 'skills'):
                src_sub = src_dir / sub
                dst_sub = dst_dir / sub
                if src_sub.is_dir():
                    shutil.copytree(src_sub, dst_sub, dirs_exist_ok=True)

    # Copy WebUI state (avatar etc.) when present — not handled by hermes_cli.
    try:
        src_state = _profile_settings_state_path(src_dir)
        if src_state.exists():
            dst_state = _profile_settings_state_path(dst_dir)
            dst_state.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_state, dst_state)
    except OSError:
        logger.debug("Failed to copy WebUI state during duplicate", exc_info=True)

    _invalidate_root_profile_cache()
    # Return the freshly-listed profile metadata for the duplicate.
    for p in list_profiles_api():
        if p['name'] == new_name:
            return p
    return {
        'name': new_name,
        'path': str(dst_dir),
        'is_default': False,
        'is_active': False,
        'gateway_running': False,
        'model': None,
        'provider': None,
        'avatar': _read_profile_avatar_for_home(dst_dir),
        'has_env': (dst_dir / '.env').exists(),
        'skill_count': 0,
    }


# ── Per-profile skills list API ────────────────────────────────────────────

# Module-level cache: absolute SKILL.md path string → {name, description, category}
# Does NOT store `enabled` or `path` — those are recomputed per call.
_skill_md_cache: dict[str, dict] = {}


def _invalidate_skill_cache_for_path(path) -> None:
    """Remove the cached metadata entry for *path* (if any).

    Accepts a Path object or a string.  Key normalisation uses str(Path(path))
    with no resolve() call so it stays consistent with how list_profile_skills_api
    stores keys (using the raw absolute path string from rglob).
    """
    key = str(path)
    _skill_md_cache.pop(key, None)


def _parse_skill_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from SKILL.md content.

    Returns (frontmatter_dict, body_str).  If no frontmatter block is present
    the returned dict is empty and body_str is the full content.

    Implemented inline so ``api/profiles`` does not depend on the hermes-agent
    ``tools.skills_tool`` package (which may not be installed in the WebUI venv).
    """
    import yaml

    if not content.startswith("---"):
        return {}, content
    # Find the closing ---
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    fm_text = content[3:end].strip()
    body = content[end + 4:].lstrip("\n")
    try:
        fm = yaml.safe_load(fm_text) or {}
        if not isinstance(fm, dict):
            fm = {}
    except Exception:
        fm = {}
    return fm, body


def _get_external_skills_dirs() -> list:
    """Return the list of external skill directories from hermes-agent.

    These are the agent-bundled skill roots (typically
    ``<HERMES_HOME>/hermes-agent/skills``) that are NOT under the per-profile
    ``skills/`` directory.  The global ``/api/skills`` endpoint already walks
    these via ``_active_skill_search_dirs``; this function lets the per-profile
    skills list include them too.

    Best-effort: returns an empty list if hermes-agent is not installed.
    Tests may monkeypatch this function to inject synthetic external dirs.
    """
    try:
        from agent.skill_utils import get_external_skills_dirs as _ext  # type: ignore
        result = _ext()
        return list(result) if result else []
    except (ImportError, Exception):
        return []


def list_profile_skills_api(name: str) -> dict:
    """Return the skills installed for *name*, including external skill dirs.

    Scans both the per-profile ``skills/`` directory AND the external skill
    roots returned by :func:`_get_external_skills_dirs` (typically the
    hermes-agent's bundled skill dir).  Real deployments install most skills
    in the agent's bundled dir, not the per-profile dir, so the old
    profile-only scan returned 0 skills on almost every installation.

    The disabled set is read from ``<profile-home>/config.yaml`` (key
    ``skills.disabled``).  Disabled skills are INCLUDED in the response list
    with ``enabled: False``; enabled skills get ``enabled: True``.

    Results are cached by SKILL.md absolute path.  Call
    ``_invalidate_skill_cache_for_path(path)`` after writing a SKILL.md to
    force a re-parse on the next list call.

    Raises:
        FileNotFoundError: profile home directory does not exist.
        ValueError: *name* fails basic validation.
    """
    import yaml as _yaml

    _validate_profile_settings_name(name)
    if _is_root_profile(name):
        profile_home = _DEFAULT_HERMES_HOME
    else:
        profile_home = _resolve_named_profile_home(name)

    if not profile_home.is_dir():
        raise FileNotFoundError(f"Profile '{name}' not found.")

    # --- Read disabled set from config.yaml ---
    disabled_set: set[str] = set()
    config_path = profile_home / "config.yaml"
    if config_path.is_file():
        try:
            cfg = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            if isinstance(cfg, dict):
                disabled_list = cfg.get("skills", {}).get("disabled", [])
                if isinstance(disabled_list, list):
                    disabled_set = {str(x) for x in disabled_list if isinstance(x, str)}
        except Exception:
            disabled_set = set()

    # --- Build ordered list of skill search roots ---
    # Profile-local dir first (takes precedence for deduplication), then
    # external dirs from the agent's bundled location.
    search_dirs: list[Path] = []
    profile_skills_dir = profile_home / "skills"
    if profile_skills_dir.is_dir():
        search_dirs.append(profile_skills_dir)
    for ext in _get_external_skills_dirs():
        try:
            ext_path = Path(ext)
        except Exception:
            continue
        if ext_path.is_dir() and ext_path not in search_dirs:
            search_dirs.append(ext_path)

    if not search_dirs:
        return {
            "ok": True,
            "profile": name,
            "skills": [],
            "total_count": 0,
            "enabled_count": 0,
            "categories": [],
        }

    # --- Iterate SKILL.md files across all search roots ---
    skills: list[dict] = []
    seen_names: set[str] = set()

    for skills_root in search_dirs:
        for skill_md in sorted(skills_root.rglob("SKILL.md")):
            if not skill_md.is_file():
                continue
            try:
                rel = skill_md.relative_to(skills_root)
            except ValueError:
                continue
            parts = rel.parts  # e.g. ("research", "deep-dive", "SKILL.md") or ("name", "SKILL.md")
            if len(parts) < 2:
                # SKILL.md must live inside at least one directory (the skill dir).
                continue
            # Skill dir name is the immediate parent of SKILL.md.
            skill_dir_name = parts[-2]
            # Category is the path segment above the skill dir (if present).
            category: str | None = parts[-3] if len(parts) >= 3 else None

            # Check cache by absolute path string (no resolve — consistent with invalidation)
            path_str = str(skill_md)
            cached = _skill_md_cache.get(path_str)
            if cached is None:
                try:
                    content = skill_md.read_text(encoding="utf-8")[:4000]
                except (OSError, UnicodeDecodeError):
                    continue
                fm, body = _parse_skill_frontmatter(content)
                skill_name = str(fm.get("name", skill_dir_name))[:64]
                description = fm.get("description", "")
                if not description:
                    for line in body.strip().split("\n"):
                        line = line.strip()
                        if line and not line.startswith("#"):
                            description = line
                            break
                cached = {
                    "name": skill_name,
                    "description": description,
                    "category": category,
                }
                _skill_md_cache[path_str] = cached

            # Deduplicate by name: profile-local wins (added first).
            if cached["name"] in seen_names:
                continue
            seen_names.add(cached["name"])

            skills.append({
                "name": cached["name"],
                "category": cached["category"],
                "description": cached["description"],
                "enabled": cached["name"] not in disabled_set,
                "path": path_str,
                "source": str(skills_root),
            })

    # Sort alphabetically by name (case-insensitive)
    skills.sort(key=lambda s: s["name"].lower())

    total_count = len(skills)
    enabled_count = sum(1 for s in skills if s["enabled"])
    categories = sorted({s["category"] for s in skills if s.get("category")})
    return {
        "ok": True,
        "profile": name,
        "skills": skills,
        "total_count": total_count,
        "enabled_count": enabled_count,
        "categories": categories,
    }


# ---------------------------------------------------------------------------
# Skill name validation helper
# ---------------------------------------------------------------------------

def _validate_skill_name(skill: str) -> None:
    """Raise ``ValueError`` for empty or path-traversal skill names."""
    if not isinstance(skill, str) or not skill.strip():
        raise ValueError("skill name must be a non-empty string")
    if ".." in skill or "/" in skill or "\\" in skill:
        raise ValueError(f"skill name contains invalid characters: {skill!r}")


# ---------------------------------------------------------------------------
# toggle_profile_skill_api
# ---------------------------------------------------------------------------

def toggle_profile_skill_api(name: str, skill: str, enabled: bool) -> dict:
    """Enable or disable a single skill for the given profile.

    Reads ``<profile-home>/config.yaml``, updates the ``skills.disabled`` list,
    and writes back only when the state actually changes.  The response mirrors
    ``list_profile_skills_api`` plus a ``changed`` boolean so callers can tell
    whether a write occurred.

    Args:
        name:    Profile name (validated via ``_validate_profile_settings_name``).
        skill:   Skill name to toggle (must be non-empty, no path traversal).
        enabled: ``True`` → remove from disabled list; ``False`` → add to it.

    Returns:
        ``{ok, changed, profile, skills, total_count, enabled_count}``

    Raises:
        FileNotFoundError: profile home directory does not exist.
        ValueError: *name* or *skill* fails validation.
    """
    import yaml as _yaml

    _validate_profile_settings_name(name)
    _validate_skill_name(skill)

    if _is_root_profile(name):
        profile_home = _DEFAULT_HERMES_HOME
    else:
        profile_home = _resolve_named_profile_home(name)

    if not profile_home.is_dir():
        raise FileNotFoundError(f"Profile '{name}' not found.")

    config_path = profile_home / "config.yaml"

    # --- Read existing config ---
    cfg: dict = {}
    if config_path.is_file():
        try:
            loaded = _yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cfg = loaded
        except Exception:
            cfg = {}

    # --- Derive existing disabled set ---
    raw_disabled = cfg.get("skills", {}).get("disabled", [])
    if not isinstance(raw_disabled, list):
        raw_disabled = []
    old_set: set[str] = {str(x) for x in raw_disabled if isinstance(x, str)}

    # --- Compute new disabled set ---
    new_set: set[str] = set(old_set)
    if enabled:
        new_set.discard(skill)
    else:
        new_set.add(skill)

    changed = new_set != old_set

    if changed:
        if new_set:
            cfg.setdefault("skills", {})["disabled"] = sorted(new_set)
        else:
            # Empty list — remove the key entirely to keep config tidy.
            if "skills" in cfg:
                cfg["skills"].pop("disabled", None)
                if not cfg["skills"]:
                    del cfg["skills"]
        config_path.write_text(
            _yaml.safe_dump(cfg, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    result = list_profile_skills_api(name)
    result["changed"] = changed
    return result


# ---------------------------------------------------------------------------
# set_profile_disabled_skills_api
# ---------------------------------------------------------------------------

def set_profile_disabled_skills_api(name: str, disabled_list: list) -> dict:
    """Replace the full disabled-skills list for the given profile in one write.

    Unlike ``toggle_profile_skill_api`` this overwrites the entire set, which
    is what the bulk "Save" action in the Skills manager modal needs.

    Args:
        name:          Profile name.
        disabled_list: A ``list`` of skill-name strings (may be empty to
                       clear all disabled skills).  Passing a non-list (e.g.
                       a bare string) raises ``ValueError``.

    Returns:
        ``{ok, changed, profile, skills, total_count, enabled_count}``

    Raises:
        FileNotFoundError: profile home directory does not exist.
        ValueError: *name* or any element in *disabled_list* fails validation,
                    or *disabled_list* is not a ``list``.
    """
    import yaml as _yaml

    _validate_profile_settings_name(name)

    if not isinstance(disabled_list, list):
        raise ValueError("disabled_list must be a list of skill-name strings")

    for item in disabled_list:
        _validate_skill_name(item)

    if _is_root_profile(name):
        profile_home = _DEFAULT_HERMES_HOME
    else:
        profile_home = _resolve_named_profile_home(name)

    if not profile_home.is_dir():
        raise FileNotFoundError(f"Profile '{name}' not found.")

    config_path = profile_home / "config.yaml"

    # --- Read existing config ---
    cfg: dict = {}
    if config_path.is_file():
        try:
            loaded = _yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cfg = loaded
        except Exception:
            cfg = {}

    # --- Derive existing disabled set ---
    raw_disabled = cfg.get("skills", {}).get("disabled", [])
    if not isinstance(raw_disabled, list):
        raw_disabled = []
    old_set: set[str] = {str(x) for x in raw_disabled if isinstance(x, str)}

    new_set: set[str] = set(disabled_list)
    changed = new_set != old_set

    if changed:
        if new_set:
            cfg.setdefault("skills", {})["disabled"] = sorted(new_set)
        else:
            if "skills" in cfg:
                cfg["skills"].pop("disabled", None)
                if not cfg["skills"]:
                    del cfg["skills"]
        config_path.write_text(
            _yaml.safe_dump(cfg, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    result = list_profile_skills_api(name)
    result["changed"] = changed
    return result


# ---------------------------------------------------------------------------
# resolve_profile_skill_file
# ---------------------------------------------------------------------------

def resolve_profile_skill_file(name: str, skill: str):
    """Return the ``Path`` to the SKILL.md for *skill* visible to *name*.

    Searches the same ordered set of skill roots that ``list_profile_skills_api``
    uses: the profile-local ``skills/`` directory first, then external dirs
    from ``_get_external_skills_dirs()``.  A match is found when the containing
    directory name equals *skill*, OR when the frontmatter ``name:`` field
    equals *skill*.

    Args:
        name:  Profile name.
        skill: Skill name to locate.

    Returns:
        :class:`pathlib.Path` pointing to the matching ``SKILL.md``.

    Raises:
        FileNotFoundError: profile home directory does not exist, or no
                           matching skill was found.
        ValueError: *name* or *skill* fails validation.
    """
    _validate_profile_settings_name(name)
    _validate_skill_name(skill)

    if _is_root_profile(name):
        profile_home = _DEFAULT_HERMES_HOME
    else:
        profile_home = _resolve_named_profile_home(name)

    if not profile_home.is_dir():
        raise FileNotFoundError(f"Profile '{name}' not found.")

    # Build the same ordered search-root list as list_profile_skills_api.
    search_dirs: list[Path] = []
    profile_skills_dir = profile_home / "skills"
    if profile_skills_dir.is_dir():
        search_dirs.append(profile_skills_dir)
    for ext in _get_external_skills_dirs():
        try:
            ext_path = Path(ext)
        except Exception:
            continue
        if ext_path.is_dir() and ext_path not in search_dirs:
            search_dirs.append(ext_path)

    if not search_dirs:
        raise FileNotFoundError(f"Skill '{skill}' not found in profile '{name}'.")

    for skills_root in search_dirs:
        for skill_md in sorted(skills_root.rglob("SKILL.md")):
            if not skill_md.is_file():
                continue
            # Check directory name first (fast path, no file read needed).
            if skill_md.parent.name == skill:
                return skill_md
            # Fall back to frontmatter name field.
            try:
                content = skill_md.read_text(encoding="utf-8")[:4000]
            except (OSError, UnicodeDecodeError):
                continue
            fm, _ = _parse_skill_frontmatter(content)
            if str(fm.get("name", "")) == skill:
                return skill_md

    raise FileNotFoundError(f"Skill '{skill}' not found in profile '{name}'.")


# Gateway control helper override hook — tests monkeypatch this with a fake
# runner. When set, ``profile_gateway_control_api`` calls it instead of
# importing ``hermes_cli.gateway``. The hook must return a dict shaped like the
# API response (``ok``, ``running`` etc.) or raise to signal failure.
_gateway_control_hook = None


def _set_gateway_control_hook(fn) -> None:
    """Install a test-only gateway control override."""
    global _gateway_control_hook
    _gateway_control_hook = fn


def _resolve_hermes_bin() -> str:
    """Resolve the path to the ``hermes`` CLI entry script.

    The container's PATH does not include the venv's bin dir, so a bare
    ``'hermes'`` argv[0] fails with FileNotFoundError (swallowed by
    stderr=DEVNULL — silent failure). The hermes script is always
    co-located with the running Python interpreter (venvs put entry
    points next to the interpreter), so derive from ``sys.executable``.
    Falls back to ``shutil.which`` for unusual layouts.
    """
    import shutil as _shutil
    import sys as _sys
    from pathlib import Path as _Path

    # 1. Venv-relative lookup — the canonical case inside the container.
    venv_bin = _Path(_sys.executable).parent
    for candidate in (venv_bin / 'hermes', venv_bin / 'hermes.exe'):
        if candidate.is_file():
            return str(candidate)
    # 2. shutil.which on the standard PATH.
    found = _shutil.which('hermes')
    if found:
        return found
    # 3. Last resort — return the bare name and let subprocess raise a
    #    visible FileNotFoundError instead of silently DEVNULLing.
    return 'hermes'


def _default_gateway_control(name: str, action: str) -> dict:
    """In-process default gateway control backend.

    Brackets the HERMES_HOME swap via ``cron_profile_context_for_home`` so
    the child gateway process inherits the right profile via os.environ
    at fork time.

    Start/restart spawn the gateway as a DETACHED background subprocess
    invoking ``hermes gateway run`` (the foreground runner). We do NOT call
    ``gateway_command(Namespace(gateway_command='start'))`` because that
    routes through service-manager logic which sys.exit()s inside Docker
    containers — the production deployment is containerized.

    Stop uses the in-process ``stop_profile_gateway()`` which kills the
    PID recorded by start_gateway.
    """
    import subprocess as _subprocess
    import sys as _sys
    from hermes_cli import gateway as _gw  # raises ImportError if absent

    if _is_root_profile(name):
        profile_home = _DEFAULT_HERMES_HOME
    else:
        profile_home = _resolve_named_profile_home(name)

    def _spawn_gateway() -> None:
        # Detached background process. Resolve the hermes binary to an
        # absolute path so it works regardless of the container's PATH.
        # On POSIX: start_new_session=True severs the controlling terminal.
        # On Windows: DETACHED_PROCESS via creationflags.
        hermes_bin = _resolve_hermes_bin()
        kwargs: dict = {"close_fds": True}
        if _sys.platform == "win32":
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        # Surface gateway-spawn errors to a per-profile log instead of
        # DEVNULL — silent failure here was what hid the wrong-PATH bug.
        log_path = profile_home / ".gateway-stderr.log"
        log_fh = None
        try:
            log_fh = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: WPS515
        except OSError:
            pass  # Best-effort logging only.
        try:
            _subprocess.Popen(
                [hermes_bin, "gateway", "run"],
                stdin=_subprocess.DEVNULL,
                stdout=log_fh if log_fh else _subprocess.DEVNULL,
                stderr=log_fh if log_fh else _subprocess.DEVNULL,
                **kwargs,
            )
        finally:
            # Close the parent's end of the log file handle after Popen
            # inherits (duplicates) it into the child. Keeps the file
            # unlocked in the parent process on Windows.
            if log_fh is not None:
                try:
                    log_fh.close()
                except OSError:
                    pass

    try:
        with cron_profile_context_for_home(profile_home):
            if action == 'stop':
                _gw.stop_profile_gateway()
                return {'ok': True, 'running': False}
            if action in ('start', 'restart'):
                if action == 'restart':
                    try:
                        _gw.stop_profile_gateway()
                    except Exception:  # noqa: BLE001 — best-effort stop
                        logger.debug("stop during restart raised — continuing", exc_info=True)
                _spawn_gateway()
                return {'ok': True, 'running': True}
            raise ValueError(f"unknown gateway action: {action!r}")
    except SystemExit as exc:
        # Defensive: the underlying CLI helpers may call sys.exit() in some
        # platforms (notably container/wsl/termux). Converting to a normal
        # exception prevents process termination of the WebUI itself.
        raise RuntimeError(f"gateway subsystem aborted: {exc}") from exc


def _is_pid_alive(pid: int) -> bool:
    """True if a process with `pid` exists and is signal-able.

    Module-level binding so tests can monkey-patch.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # EPERM means the process exists; we just can't signal it.
        return True
    except OSError:
        return False
    return True


def _read_gateway_pid(profile_home: Path) -> int | None:
    """Return the PID from gateway.pid or None if missing/malformed."""
    pid_path = profile_home / 'gateway.pid'
    if not pid_path.exists():
        return None
    try:
        raw = pid_path.read_text(encoding='utf-8').strip()
        pid = int(raw)
        return pid if pid > 0 else None
    except (ValueError, OSError):
        return None


def _read_stderr_tail(profile_home: Path, *, max_bytes: int = 5120) -> str:
    """Return the last `max_bytes` of the gateway stderr log, sanitized."""
    log_path = profile_home / '.gateway-stderr.log'
    if not log_path.exists():
        return ''
    try:
        size = log_path.stat().st_size
        with log_path.open('rb') as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            chunk = fh.read()
        text = chunk.decode('utf-8', errors='replace')
    except OSError:
        return ''
    return _sanitize_gateway_message(text)


def _phase_age_seconds(phase_started_at: str | None) -> float:
    """Seconds elapsed since phase_started_at; inf when missing/malformed."""
    if not isinstance(phase_started_at, str):
        return float('inf')
    import datetime as _dt
    try:
        # Accept trailing 'Z' or explicit offsets.
        normalized = phase_started_at.replace('Z', '+00:00')
        started = _dt.datetime.fromisoformat(normalized)
    except ValueError:
        return float('inf')
    if started.tzinfo is None:
        started = started.replace(tzinfo=_dt.timezone.utc)
    now = _dt.datetime.now(_dt.timezone.utc)
    return (now - started).total_seconds()


def profile_gateway_status_api(name: str) -> dict:
    """Return the current gateway phase for `name`, promoting transient
    phases when the world has caught up to them.

    Promotion rules (first match wins):
      * phase 'starting' + pid alive  -> 'running'
      * phase 'starting' + age >= grace + pid dead/missing -> 'failed'
      * phase 'stopping' + pid gone   -> 'stopped'
      * phase 'running'  + pid dead   -> 'stopped'  (post-running crash)
      * phase 'failed' or 'stopped'   -> as-is (sticky)

    Raises:
        ValueError: invalid profile name.
        FileNotFoundError: profile directory missing.
    """
    _validate_profile_settings_name(name)
    if _is_root_profile(name):
        profile_home = _DEFAULT_HERMES_HOME
    else:
        profile_home = _resolve_named_profile_home(name)
    if not profile_home.is_dir():
        raise FileNotFoundError(f"Profile '{name}' not found.")

    state = _read_gateway_state(profile_home)
    phase = state.get('phase')
    phase_started_at = state.get('phase_started_at')
    pid = _read_gateway_pid(profile_home)
    pid_alive = _is_pid_alive(pid) if pid else False

    # No phase recorded -> infer from PID liveness only.
    if not phase:
        if pid_alive:
            # PID file with live process but no phase — treat as running.
            # Synthesize a 'running' state so future polls are consistent.
            _write_gateway_phase(profile_home, 'running', started_at=phase_started_at)
            return _status_payload(name, 'running', pid, None, phase_started_at)
        return _status_payload(name, 'stopped', None, None, None)

    if phase == 'starting':
        if pid_alive:
            _write_gateway_phase(profile_home, 'running', started_at=phase_started_at)
            return _status_payload(name, 'running', pid, None, phase_started_at)
        if _phase_age_seconds(phase_started_at) >= GATEWAY_START_GRACE_SECONDS:
            tail = _read_stderr_tail(profile_home)
            err = tail if tail else 'gateway failed to start within grace window'
            _write_gateway_phase(profile_home, 'failed', last_error=err)
            return _status_payload(name, 'failed', pid, err, phase_started_at)
        return _status_payload(name, 'starting', pid, None, phase_started_at)

    if phase == 'stopping':
        if not pid_alive:
            _write_gateway_phase(profile_home, 'stopped')
            return _status_payload(name, 'stopped', None, None, None)
        return _status_payload(name, 'stopping', pid, None, phase_started_at)

    if phase == 'running':
        if pid_alive:
            return _status_payload(name, 'running', pid, None, phase_started_at)
        # Post-running crash — drop to stopped, not failed.
        _write_gateway_phase(profile_home, 'stopped')
        return _status_payload(name, 'stopped', None, None, None)

    if phase == 'failed':
        return _status_payload(
            name, 'failed', pid, state.get('last_error'), phase_started_at
        )

    # Unknown phase string -> treat as stopped (defensive).
    _write_gateway_phase(profile_home, 'stopped')
    return _status_payload(name, 'stopped', None, None, None)


def _status_payload(
    name: str,
    phase: str,
    pid: int | None,
    last_error: str | None,
    phase_started_at: str | None,
) -> dict:
    return {
        'ok': True,
        'profile': name,
        'phase': phase,
        'pid': pid,
        'last_error': last_error,
        'phase_started_at': phase_started_at,
    }


def profile_gateway_control_api(name: str, action: str) -> dict:
    """Start, restart, or stop the gateway for a named profile.

    Degrades honestly: returns ``{ok: False, ...}`` when no safe backend
    wrapper is available, instead of pretending success.
    """
    _validate_profile_settings_name(name)
    action = (action or '').strip().lower()
    if action not in ('start', 'restart', 'stop'):
        raise ValueError("action must be one of: start, restart, stop")

    # Confirm the profile actually exists (FileNotFoundError -> 404 from caller).
    if _is_root_profile(name):
        profile_home = _DEFAULT_HERMES_HOME
    else:
        profile_home = _resolve_named_profile_home(name)
    if not profile_home.is_dir():
        raise FileNotFoundError(f"Profile '{name}' not found.")

    if _gateway_control_hook is not None:
        try:
            hook_result = _gateway_control_hook(name, action)
        except Exception as exc:  # noqa: BLE001 — surface any test-injected failure
            return {
                'ok': False,
                'profile': name,
                'action': action,
                'running': False,
                'configured': False,
                'message': _sanitize_gateway_message(str(exc)),
            }
        if not isinstance(hook_result, dict):
            hook_result = {'ok': True, 'running': action != 'stop'}
        hook_result.setdefault('ok', True)
        hook_result.setdefault('profile', name)
        hook_result.setdefault('action', action)
        hook_result.setdefault('configured', True)
        if hook_result.get('ok') and action in ('start', 'restart'):
            _write_gateway_last_run(profile_home)
        return hook_result

    try:
        result = _default_gateway_control(name, action)
    except Exception as exc:  # noqa: BLE001 — keep error surface narrow + safe
        return {
            'ok': False,
            'profile': name,
            'action': action,
            'running': False,
            'configured': False,
            'message': _sanitize_gateway_message(str(exc)),
        }
    if not isinstance(result, dict):
        result = {'ok': True, 'running': action != 'stop'}
    result.setdefault('ok', True)
    result.setdefault('profile', name)
    result.setdefault('action', action)
    result.setdefault('configured', True)
    if result.get('ok') and action in ('start', 'restart'):
        _write_gateway_last_run(profile_home)
    return result


def _write_gateway_last_run(profile_home: Path) -> None:
    """Stamp ``.gateway-state.json`` with last_run_at on a successful start.

    Best-effort: never fail the gateway action because the state write
    failed. The activity line reads this back via :func:`_read_gateway_state`.
    """
    import datetime as _dt
    state_path = profile_home / '.gateway-state.json'
    try:
        payload: dict = {}
        if state_path.exists():
            try:
                existing = json.loads(state_path.read_text(encoding='utf-8'))
                if isinstance(existing, dict):
                    payload = existing
            except (ValueError, OSError):
                payload = {}
        payload['last_run_at'] = (
            _dt.datetime.now(_dt.timezone.utc)
            .isoformat()
            .replace('+00:00', 'Z')
        )
        state_path.write_text(json.dumps(payload), encoding='utf-8')
    except OSError:
        logger.debug("Failed to write gateway last_run_at state", exc_info=True)


_SECRET_PATTERN = re.compile(
    r'(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+'
)


def _sanitize_gateway_message(message: str) -> str:
    """Strip obviously secret-looking substrings from gateway runner output."""
    if not message:
        return ''
    text = _SECRET_PATTERN.sub(r'\1=[redacted]', message)
    # Truncate to avoid dumping arbitrary subprocess output into UI toasts.
    return text[:280]


def delete_profile_api(name: str) -> dict:
    """Delete a profile. Switches to default first if it's the active one."""
    if _is_root_profile(name):
        raise ValueError("Cannot delete the default profile.")
    _validate_profile_name(name)

    # If deleting the active profile, switch to default first
    if _active_profile == name:
        try:
            switch_profile('default')
        except RuntimeError:
            raise RuntimeError(
                f"Cannot delete active profile '{name}' while an agent is running. "
                "Cancel or wait for it to finish."
            )

    try:
        from hermes_cli.profiles import delete_profile
        delete_profile(name, yes=True)
    except ImportError:
        # Manual fallback: just remove the directory
        import shutil
        profile_dir = _resolve_named_profile_home(name)
        if profile_dir.is_dir():
            shutil.rmtree(str(profile_dir))
        else:
            raise ValueError(f"Profile '{name}' does not exist.")

    # Drop cached root-profile-name lookup — list_profiles_api() shape changed.
    _invalidate_root_profile_cache()
    return {'ok': True, 'name': name}
