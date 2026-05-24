# WebUI Gateway/Profile/Session Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Gateway status provenance explicit, smooth profile runtime settings hydration, and make metadata-only session polling cheap when no external update is present.

**Architecture:** Preserve existing backend contracts and frontend hydration guards. Add narrow helpers at existing seams: Gateway evidence copy in `static/panels.js`, per-profile runtime settings cache in `static/panels.js`, and a profile-aware state DB summary fast path in `api/models.py`/`api/routes.py`.

**Tech Stack:** Python 3.12, pytest, vanilla JavaScript, Hermes WebUI static frontend, SQLite state DB helpers.

---

## Prerequisite Already Landed

Commit `2297ab4d fix: preserve webui launcher environment` already handles the launcher environment part of the original improvement set. Do not reimplement it in this plan. Keep its tests in the final verification sweep:

```bash
python3 -m pytest tests/test_bootstrap_dotenv.py tests/test_ctl_script.py -v --timeout=60
```

## Planned File Changes

- Modify `static/panels.js`: Gateway provenance copy, profile runtime settings cache, prefetch hooks, cache updates after saves.
- Modify `tests/test_profile_gateway_tile_frontend.py`: static checks for Gateway evidence rendering and no-restart/autostart clarity.
- Modify `tests/test_profile_rework_static.py`: static checks for profile runtime settings cache, in-flight dedupe, stale guards, and prefetch wiring.
- Modify `api/models.py`: make `get_state_db_session_summary` profile-aware.
- Modify `api/routes.py`: use summary fast path for metadata-only non-messaging `/api/session` requests.
- Modify `tests/test_webui_state_db_reconciliation.py`: route-level tests for no-change fast path and fallback exact merge.
- Modify `CHANGELOG.md`: release-note-ready entry.

## Sub-Agent Use

Use sub-agents only where the write scope is bounded:

- Local controller: Task 1 and Task 4, because they touch support copy/docs and require integration judgment.
- Worker sub-agent: Task 2, profile runtime cache, owning only `static/panels.js` and `tests/test_profile_rework_static.py`.
- Worker sub-agent: Task 3, session metadata fast path, owning only `api/models.py`, `api/routes.py`, and `tests/test_webui_state_db_reconciliation.py`.
- Optional reviewer sub-agent after Tasks 2 and 3 if their patches are non-trivial.

Do not dispatch implementation sub-agents in parallel. Their scopes are mostly disjoint, but sequential review keeps the repo coherent.

---

### Task 1: Gateway Provenance UX and No-Autostart Contract

**Files:**
- Modify: `static/panels.js`
- Modify: `tests/test_profile_gateway_tile_frontend.py`
- Modify: `tests/test_ctl_script.py`

- [ ] **Step 1: Add failing frontend static tests for Gateway evidence copy**

Add tests to `tests/test_profile_gateway_tile_frontend.py`:

```python
def test_gateway_tile_renders_runtime_evidence_line():
    tile = _extract_function(PANELS_JS, "_profileGatewayTile")
    repaint = _extract_function(PANELS_JS, "_repaintGatewayTile")

    assert "profile-gateway-evidence" in tile
    assert "_gatewayEvidenceText" in tile
    assert "_gatewayEvidenceText" in repaint
    assert "opsGatewayEvidence" in repaint


def test_gateway_evidence_text_distinguishes_detection_sources():
    helper = _extract_function(PANELS_JS, "_gatewayEvidenceText")

    for expected in (
        "Detected by PID",
        "Detected from runtime metadata",
        "Detected from remote health",
        "Reported by control adapter",
        "No runtime evidence",
    ):
        assert expected in helper
```

- [ ] **Step 2: Add failing no-autostart launcher contract test**

Add to `tests/test_ctl_script.py`:

```python
def test_start_command_does_not_invoke_gateway_runner():
    ctl_text = CTL.read_text(encoding="utf-8")
    start_block = ctl_text[ctl_text.index("start_cmd() {"):ctl_text.index("stop_cmd() {")]

    assert "gateway run" not in start_block
    assert "gateway stop" not in start_block
    assert ".gateway-state.json" not in start_block
    assert "gateway.pid" not in start_block
```

Run:

```bash
python3 -m pytest tests/test_profile_gateway_tile_frontend.py::test_gateway_tile_renders_runtime_evidence_line tests/test_profile_gateway_tile_frontend.py::test_gateway_evidence_text_distinguishes_detection_sources tests/test_ctl_script.py::test_start_command_does_not_invoke_gateway_runner -v --timeout=60
```

Expected: the two Gateway evidence tests fail because helper/markup is absent; the launcher no-autostart test passes if the current start block contains no Gateway runner calls.

- [ ] **Step 3: Implement Gateway evidence helper and render line**

In `static/panels.js`, near existing Gateway helper functions, add:

```javascript
function _gatewayEvidenceText(state){
  const source = state && state.status_source ? String(state.status_source) : '';
  const phase = state && state.phase ? String(state.phase) : '';
  if (source === 'pid') return 'Detected by PID';
  if (source === 'runtime_file') return 'Detected from runtime metadata';
  if (source === 'remote_health') return 'Detected from remote health';
  if (source === 'adapter') return 'Reported by control adapter';
  if (phase === 'starting') return 'Waiting for Gateway startup evidence';
  if (phase === 'stopping') return 'Waiting for Gateway shutdown evidence';
  return 'No runtime evidence';
}
```

In `_profileGatewayTile(p)`, compute and render the evidence line:

```javascript
const evidence = _gatewayEvidenceText(state);
```

Add immediately after the Gateway control block inside `_profileGatewayTile(p)`:

```html
<div class="profile-gateway-evidence" id="opsGatewayEvidence">${esc(evidence)}</div>
```

In `_repaintGatewayTile(profileName)`, update the line:

```javascript
const evidence = tile.querySelector('#opsGatewayEvidence');
if (evidence) evidence.textContent = _gatewayEvidenceText(state);
```

Do not remove or rename the existing phase labels, toggle behavior, or info dialog.

- [ ] **Step 4: Run focused Gateway tests**

Run:

```bash
python3 -m pytest tests/test_profile_gateway_tile_frontend.py tests/test_ctl_script.py -v --timeout=60
```

Expected: all selected tests pass.

---

### Task 2: Profile Runtime Settings Cache and Prefetch

**Files:**
- Modify: `static/panels.js`
- Modify: `tests/test_profile_rework_static.py`

- [ ] **Step 1: Add failing static tests for runtime cache primitives**

Add to `tests/test_profile_rework_static.py`:

```python
def test_profile_runtime_settings_cache_helpers_exist_and_dedupe_fetches():
    src = PANELS_JS.read_text(encoding="utf-8")

    assert "_profileRuntimeSettingsCache = new Map()" in src
    assert "_profileRuntimeSettingsInflight = new Map()" in src
    assert "function _fetchProfileRuntimeSettings" in src
    assert "_profileRuntimeSettingsInflight.has(profileName)" in src
    assert "_profileRuntimeSettingsInflight.delete(profileName)" in src


def test_profile_cards_prefetch_runtime_settings_on_hover_and_focus():
    load = _extract_function(PANELS_JS, "loadProfilesPanel")

    assert "card.onmouseenter" in load
    assert "card.onfocus" in load
    assert "_prefetchProfileRuntimeSettings(p.name)" in load


def test_profile_runtime_hydration_uses_cache_before_network_and_keeps_avatar_omitted():
    hydrate = _extract_function(PANELS_JS, "_hydrateProfileRuntimeSettings")

    assert "_profileRuntimeSettingsCache.get(profile.name)" in hydrate
    assert "_applyProfileRuntimeSettings(profile, cached" in hydrate
    assert "_fetchProfileRuntimeSettings(profile.name" in hydrate
    assert "include_avatar=0" in hydrate
```

Run:

```bash
python3 -m pytest tests/test_profile_rework_static.py::test_profile_runtime_settings_cache_helpers_exist_and_dedupe_fetches tests/test_profile_rework_static.py::test_profile_cards_prefetch_runtime_settings_on_hover_and_focus tests/test_profile_rework_static.py::test_profile_runtime_hydration_uses_cache_before_network_and_keeps_avatar_omitted -v --timeout=60
```

Expected: fail before implementation.

- [ ] **Step 2: Add cache helpers**

In `static/panels.js`, near `_profileRuntimeSettings` globals:

```javascript
const _profileRuntimeSettingsCache = new Map();
const _profileRuntimeSettingsInflight = new Map();

function _cacheProfileRuntimeSettings(profileName, settings){
  if (!profileName || !settings || typeof settings !== 'object') return settings || {};
  const cached = Object.assign({}, settings);
  _profileRuntimeSettingsCache.set(profileName, cached);
  return cached;
}

async function _fetchProfileRuntimeSettings(profileName, opts){
  if (!profileName) return {};
  const force = !!(opts && opts.force);
  if (!force && _profileRuntimeSettingsCache.has(profileName)) {
    return _profileRuntimeSettingsCache.get(profileName);
  }
  if (!force && _profileRuntimeSettingsInflight.has(profileName)) {
    return _profileRuntimeSettingsInflight.get(profileName);
  }
  const promise = api('/api/profile/settings?name=' + encodeURIComponent(profileName) + '&include_avatar=0')
    .then(settings => _cacheProfileRuntimeSettings(profileName, settings || {}))
    .catch(() => ({}))
    .finally(() => {
      _profileRuntimeSettingsInflight.delete(profileName);
    });
  _profileRuntimeSettingsInflight.set(profileName, promise);
  return promise;
}

function _prefetchProfileRuntimeSettings(profileName){
  if (!profileName || _profileRuntimeSettingsCache.has(profileName) || _profileRuntimeSettingsInflight.has(profileName)) return;
  _fetchProfileRuntimeSettings(profileName).catch(() => {});
}
```

- [ ] **Step 3: Extract runtime settings apply logic**

Create a helper from the body of `_hydrateProfileRuntimeSettings` after `settings` is fetched:

```javascript
function _applyProfileRuntimeSettings(profile, settings, token){
  if (!profile || !profile.name) return false;
  if (!_isCurrentProfileRuntimeHydration(profile.name, token)) return false;
  const dirty = _profileRuntimeDirty || _freshProfileRuntimeDirty();
  const prior = _profileRuntimeSettings || {};
  const next = Object.assign({}, prior, settings || {});
  if (dirty.default_reasoning) next.reasoning_effort = prior.reasoning_effort || '';
  if (dirty.fallback_model) next.fallback_model = prior.fallback_model || {};
  if (dirty.response_mode) next.response_mode = prior.response_mode || '';
  if (dirty.compression) next.compression = prior.compression || _profileCompressionPayload();
  if (dirty.max_turns) next.max_turns = prior.max_turns;
  if (dirty.default_workspace) next.default_workspace = prior.default_workspace || profile.default_workspace || '';
  if (dirty.toolsets) {
    next.toolsets = prior.toolsets || [];
    next.toolsets_configured = !!prior.toolsets_configured;
  }
  if (dirty.auxiliary_models) next.auxiliary_models = prior.auxiliary_models || [];
  _profileRuntimeSettings = next;

  const fallback = (_profileRuntimeSettings && _profileRuntimeSettings.fallback_model) || {};
  const defaultModelChip = $('profileDefaultModelChip');
  const fallbackModelChip = $('profileFallbackModelChip');
  const currentDefaultModel = defaultModelChip ? (defaultModelChip.dataset.modelValue || '') : '';
  const currentFallbackModel = fallbackModelChip ? (fallbackModelChip.dataset.modelValue || '') : '';
  _populateProfileModelSelect('profileDefaultModelSelect', dirty.default_model ? currentDefaultModel : (profile.model || ''), profile.provider || null);
  _populateProfileModelSelect('profileFallbackModelSelect', dirty.fallback_model ? currentFallbackModel : (fallback.model || ''), fallback.provider || null);
  if (!dirty.default_model) _applyProfileDefaultModelChip(profile.model || '');
  if (!dirty.default_reasoning) _applyProfileDefaultReasoningChip(typeof _profileRuntimeSettings.reasoning_effort === 'string' ? _profileRuntimeSettings.reasoning_effort : '');
  if (!dirty.fallback_model) _applyProfileFallbackModelChip(fallback.model || '');
  if (!dirty.response_mode) _applyProfileResponseMode(_profileRuntimeSettings.response_mode || '');
  if (!dirty.compression) _applyProfileCompression(_profileRuntimeSettings.compression || {});
  if (!dirty.max_turns) _applyProfileMaxTurns(_profileRuntimeSettings.max_turns);
  if (!dirty.default_workspace) _applyProfileDefaultWorkspace(_profileRuntimeSettings.default_workspace || profile.default_workspace || '');
  if (!dirty.toolsets) _applyProfileToolsets(_profileRuntimeSettings.toolsets || [], !!_profileRuntimeSettings.toolsets_configured);
  _wireProfileDefaultModelHandlers(profile.name);
  _wireProfileRuntimeSettingHandlers(profile.name);
  return true;
}
```

Then simplify `_hydrateProfileRuntimeSettings`:

```javascript
const cached = _profileRuntimeSettingsCache.get(profile.name);
if (cached) _applyProfileRuntimeSettings(profile, cached, token);
let settings = await _fetchProfileRuntimeSettings(profile.name, { force: !!cached });
if (!_isCurrentProfileRuntimeHydration(profile.name, token)) return;
_applyProfileRuntimeSettings(profile, settings || {}, token);
```

- [ ] **Step 4: Wire prefetch on profile cards**

In `loadProfilesPanel()`, after assigning `card.onclick`/`card.onkeydown`, add:

```javascript
card.onmouseenter = () => _prefetchProfileRuntimeSettings(p.name);
card.onfocus = () => _prefetchProfileRuntimeSettings(p.name);
```

Do not prefetch full avatars.

- [ ] **Step 5: Update cache after successful settings saves**

Where profile runtime save handlers merge returned settings into `_profileRuntimeSettings`, add:

```javascript
_cacheProfileRuntimeSettings(profileName, _profileRuntimeSettings);
```

Use the local variable name already present in each save handler (`profileName` or `profile.name`). Do not invent a global current-profile fallback.

- [ ] **Step 6: Run focused profile tests**

Run:

```bash
python3 -m pytest tests/test_profile_rework_static.py tests/test_profile_settings_runtime.py -v --timeout=60
```

Expected: all selected tests pass.

---

### Task 3: Session Metadata Summary Fast Path

**Files:**
- Modify: `api/models.py`
- Modify: `api/routes.py`
- Modify: `tests/test_webui_state_db_reconciliation.py`

- [ ] **Step 1: Add failing tests for profile-aware summary and no-change fast path**

Add tests to `tests/test_webui_state_db_reconciliation.py` near existing metadata-only reconciliation tests:

```python
def test_state_db_session_summary_reads_named_profile_db(tmp_path, monkeypatch):
    import importlib
    import sqlite3

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    import api.models as models
    models = importlib.reload(models)

    profile_home = tmp_path / "home" / "profiles" / "research"
    profile_home.mkdir(parents=True)
    db_path = profile_home / "state.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL)")
        conn.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)", ("sid_profile", "user", "hi", 10.5))

    summary = models.get_state_db_session_summary("sid_profile", profile="research")

    assert summary == {"message_count": 1, "last_message_at": 10.5}


def test_metadata_only_session_uses_summary_without_full_state_read_when_not_newer(monkeypatch, tmp_path):
    import api.routes as routes

    sid = "webui_metadata_summary_no_change"
    sidecar_messages = [
        {"role": "user", "content": "old user", "timestamp": 1000.0},
        {"role": "assistant", "content": "old assistant", "timestamp": 1001.0},
    ]
    _install_test_session(monkeypatch, tmp_path, sid, sidecar_messages)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("metadata no-change fast path must not read full state DB messages")

    monkeypatch.setattr(routes, "get_state_db_session_messages", fail_if_called)
    monkeypatch.setattr(
        routes,
        "get_state_db_session_summary",
        lambda sid_arg, profile=None: {"message_count": 2, "last_message_at": 1001.0},
    )

    handler = _GetHandler(f"/api/session?session_id={sid}&messages=0&resolve_model=0")
    routes.handle_get(handler, urlparse(handler.path))

    assert handler.status == 200
    session = handler.response_json["session"]
    assert session["messages"] == []
    assert session["message_count"] == 2
    assert session["last_message_at"] == 1001.0
```

Run:

```bash
python3 -m pytest tests/test_webui_state_db_reconciliation.py::test_state_db_session_summary_reads_named_profile_db tests/test_webui_state_db_reconciliation.py::test_metadata_only_session_uses_summary_without_full_state_read_when_not_newer -v --timeout=60
```

Expected: fail before implementation.

- [ ] **Step 2: Make `get_state_db_session_summary` profile-aware**

In `api/models.py`, change the signature:

```python
def get_state_db_session_summary(sid, *, profile=None) -> dict:
```

Replace the DB path selection with:

```python
if isinstance(profile, str) and profile:
    db_path = _get_profile_home(profile) / 'state.db'
    if not db_path.exists():
        db_path = _active_state_db_path()
else:
    db_path = _active_state_db_path()
```

Keep the existing `COUNT(*)` and `MAX(timestamp)` query.

- [ ] **Step 3: Add route helper for safe fast-path decision**

In `api/routes.py`, near `/api/session` helper functions if a suitable section exists, add:

```python
def _metadata_summary_not_newer(summary: dict, metadata_count, metadata_last) -> bool:
    if not isinstance(summary, dict):
        return False
    try:
        summary_count = int(summary.get("message_count") or 0)
    except (TypeError, ValueError):
        return False
    try:
        known_count = int(metadata_count or 0)
    except (TypeError, ValueError):
        known_count = 0
    try:
        summary_last = float(summary.get("last_message_at") or 0)
    except (TypeError, ValueError):
        summary_last = 0
    try:
        known_last = float(metadata_last or 0)
    except (TypeError, ValueError):
        known_last = 0
    return summary_count <= known_count and summary_last <= known_last
```

- [ ] **Step 4: Use fast path in metadata-only `/api/session`**

In the `elif not is_messaging_session:` branch under `not load_messages`, before calling `get_state_db_session_messages`, add:

```python
state_db_summary = get_state_db_session_summary(sid, profile=_session_profile)
metadata_compact = s.compact()
metadata_count = metadata_compact.get("message_count")
metadata_last = metadata_compact.get("last_message_at") or metadata_compact.get("updated_at")
if _metadata_summary_not_newer(state_db_summary, metadata_count, metadata_last):
    state_db_messages = []
    sidecar_metadata_messages = []
    _summary_message_count = int(metadata_count or state_db_summary.get("message_count") or 0)
    try:
        _summary_last_message_at = float(metadata_last or state_db_summary.get("last_message_at") or 0)
    except (TypeError, ValueError):
        _summary_last_message_at = 0
else:
    state_db_messages = get_state_db_session_messages(sid, profile=_session_profile)
    sidecar_metadata_session = Session.load(sid)
    sidecar_metadata_messages = (
        getattr(sidecar_metadata_session, "messages", []) or []
        if sidecar_metadata_session
        else []
    )
```

Then ensure the later summary-count block does not recompute `_summary_message_count` from `_all_msgs` when it was already set by the fast path. Use a sentinel before the branch:

```python
_summary_message_count = None
_summary_last_message_at = None
```

and later:

```python
if not load_messages and _summary_message_count is None:
    ...
```

- [ ] **Step 5: Add fallback test for possible newer summary**

Add a test that patches `get_state_db_session_summary` to return a higher count or newer timestamp, patches `get_state_db_session_messages` to return a message list, and asserts the route still uses the exact merge path.

```python
def test_metadata_only_session_falls_back_to_exact_merge_when_summary_is_newer(monkeypatch, tmp_path):
    import api.routes as routes

    sid = "webui_metadata_summary_newer"
    sidecar_messages = [
        {"role": "user", "content": "old user", "timestamp": 1000.0},
        {"role": "assistant", "content": "old assistant", "timestamp": 1001.0},
    ]
    _install_test_session(monkeypatch, tmp_path, sid, sidecar_messages)

    calls = {"state_messages": 0}

    def fake_state_messages(sid_arg, profile=None):
        calls["state_messages"] += 1
        assert sid_arg == sid
        return [
            {"role": "user", "content": "old user", "timestamp": 1000.0},
            {"role": "assistant", "content": "old assistant", "timestamp": 1001.0},
            {"role": "user", "content": "external user", "timestamp": 1002.0},
            {"role": "assistant", "content": "external assistant", "timestamp": 1003.0},
        ]

    monkeypatch.setattr(routes, "get_state_db_session_messages", fake_state_messages)
    monkeypatch.setattr(
        routes,
        "get_state_db_session_summary",
        lambda sid_arg, profile=None: {"message_count": 4, "last_message_at": 1003.0},
    )

    handler = _GetHandler(f"/api/session?session_id={sid}&messages=0&resolve_model=0")
    routes.handle_get(handler, urlparse(handler.path))

    assert handler.status == 200
    session = handler.response_json["session"]
    assert calls["state_messages"] == 1
    assert session["messages"] == []
    assert session["message_count"] == 4
    assert session["last_message_at"] == 1003.0
```

Run:

```bash
python3 -m pytest tests/test_webui_state_db_reconciliation.py -v --timeout=60
```

Expected: all selected tests pass.

---

### Task 4: Docs, Changelog, and Full Verification

**Files:**
- Modify: `CHANGELOG.md`
- Optionally modify: `docs/troubleshooting.md` if Gateway provenance copy creates a useful support note.

- [ ] **Step 1: Update changelog**

Add an unreleased bullet in `CHANGELOG.md` matching the existing style:

```markdown
- Clarified profile Gateway status provenance, smoothed profile runtime settings hydration with per-profile caching, and made idle active-session metadata polling avoid full transcript reads when no external update is present.
```

- [ ] **Step 2: Run focused suites**

Run:

```bash
python3 -m pytest \
  tests/test_profile_gateway_status.py \
  tests/test_profile_gateway_control.py \
  tests/test_profile_gateway_routes.py \
  tests/test_issue1879_cross_container_gateway_liveness.py \
  tests/test_profile_gateway_tile_frontend.py \
  tests/test_profile_rework_static.py \
  tests/test_profile_settings_runtime.py \
  tests/test_issue1611_session_profile_filtering.py \
  tests/test_session_sidebar_index_routes.py \
  tests/test_webui_external_refresh_frontend.py \
  tests/test_metadata_save_wipe_1558.py \
  tests/test_webui_state_db_reconciliation.py \
  tests/test_bootstrap_dotenv.py \
  tests/test_ctl_script.py \
  -v --timeout=60
```

Expected: all selected tests pass.

- [ ] **Step 3: Run isolated browser profile switching smoke test**

Use isolated `HERMES_HOME` and `HERMES_WEBUI_STATE_DIR`, create two throwaway profiles, set different runtime settings, open the Profiles panel in the in-app browser, and verify:

```text
alpha -> response_mode technical, max_turns 9, workspace alpha-space
beta  -> response_mode teacher, max_turns 17, workspace beta-space
switching back to alpha still shows alpha values
```

Also verify Gateway tile shows a provenance/evidence line.

- [ ] **Step 4: Final repository sanity check**

Run:

```bash
git status --short --branch
git diff --stat
```

Expected: only intentional implementation/doc files are modified before final commit.
