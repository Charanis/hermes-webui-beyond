# WebUI Gateway, Profile, and Session Reliability Design

## Goal

Reduce ambiguity and avoidable latency in three WebUI surfaces:

- Gateway status should clearly say whether it was started by WebUI or merely detected from existing runtime evidence.
- Profile runtime settings should feel stable when switching profile cards and must remain profile-isolated.
- Active-session refresh polling should avoid full message materialization when no external session update is likely.

## Background

Investigation on 2026-05-24 found that `hermes webui start` does not start the per-profile Gateway. It starts the WebUI daemon; Gateway control is profile-scoped and happens through `/api/profile/gateway` or external `hermes gateway` commands.

The launcher environment precedence problem found during investigation has already been committed in `2297ab4d fix: preserve webui launcher environment`. That commit makes the `hermes webui start` path preserve resolved launcher values such as `HERMES_HOME` and `HERMES_WEBUI_STATE_DIR`.

Focused tests already passing before this work:

- `tests/test_profile_gateway_status.py`
- `tests/test_profile_gateway_control.py`
- `tests/test_profile_gateway_routes.py`
- `tests/test_issue1879_cross_container_gateway_liveness.py`
- `tests/test_profile_settings_runtime.py`
- `tests/test_profile_rework_static.py`
- `tests/test_profile_gateway_tile_frontend.py`
- `tests/test_issue1611_session_profile_filtering.py`
- `tests/test_session_sidebar_index_routes.py`
- `tests/test_webui_external_refresh_frontend.py`
- `tests/test_metadata_save_wipe_1558.py`

## Non-Goals

- Do not make WebUI startup auto-start Gateway.
- Do not change the selected-profile, active-profile, and Gateway-profile separation.
- Do not remove the current async profile hydration guards.
- Do not rewrite session storage, sidebar indexing, or state DB reconciliation.
- Do not change real user state during tests; use isolated `HERMES_HOME` and `HERMES_WEBUI_STATE_DIR`.

## Design

### 1. Gateway Provenance and No-Autostart Contract

The backend already returns `status_source`, `health`, and `detail` for profile Gateway status. The frontend should make that provenance visible in the Gateway tile and dialog:

- `pid` -> detected from a live process PID.
- `runtime_file` -> detected from fresh Gateway runtime metadata.
- `remote_health` -> detected from configured remote health.
- `adapter` -> provided by the selected control adapter.
- missing source -> no runtime evidence yet.

The profile tile should continue to show phase, but also include a compact evidence line such as `Detected by PID` or `No runtime evidence`. The info dialog should continue to expose raw fields for support/debugging.

Add a focused no-autostart contract test. It can be static if process-level startup would be too slow or brittle, but it must prove the WebUI launcher path does not invoke `hermes gateway run` or write Gateway state.

### 2. Profile Runtime Settings Cache

Keep the current flow:

1. `/api/profiles` renders cards and summary.
2. Selecting a card primes safe defaults immediately.
3. `/api/profile/settings?include_avatar=0` hydrates full runtime settings.
4. Sequence guards prevent stale profile responses from applying to another profile.
5. Dirty flags prevent async hydration from overwriting in-progress edits.

Add a small frontend cache:

- Cache key: profile name.
- Cache value: sanitized runtime settings returned by `/api/profile/settings?include_avatar=0`.
- In-flight map: one fetch per profile at a time.
- Cache updates after successful hydration and after successful profile settings saves.
- Card `mouseenter` and `focus` may prefetch settings.
- When a cached value exists during selection, apply it immediately after priming controls, then refresh in the background.

The cache must not skip stale-hydration checks and must not apply cached settings for a profile that is no longer selected.

### 3. Session Metadata Fast Path

The active-session external refresh poll calls `/api/session?messages=0&resolve_model=0` every 5 seconds while visible and idle. Today, the metadata-only route can still read and merge full state DB messages for exact counts.

Add a safe fast path:

- Make `get_state_db_session_summary(sid, profile=None)` profile-aware, using the same profile DB selection rules as `get_state_db_session_messages`.
- For metadata-only non-messaging sessions, first compare cheap state DB summary against sidecar/index metadata.
- If state DB has no newer timestamp and no higher count than known metadata, return metadata without loading full messages.
- If summary indicates possible external changes, fall back to the current exact message merge.
- If summary is unavailable or malformed, fall back to the current exact path.

This keeps correctness for external updates while making the common "no update" poll cheap.

### 4. Documentation and Verification

Update release-ready documentation:

- `CHANGELOG.md` for user-visible clarity/performance changes.
- Gateway/profile/session docs only if a new user-facing behavior or support workflow is introduced.

Verification must include:

- Focused Gateway tests.
- Focused profile runtime/frontend static tests.
- Focused session metadata/state DB tests.
- A browser check against isolated state for profile switching.
- A final `git status --short --branch` sanity check.

## Acceptance Criteria

- `hermes webui start` remains WebUI-only; no Gateway autostart behavior is introduced.
- Gateway tile/dialog identifies the source of a running/unknown/stopped state in user-support-friendly language.
- Profile runtime settings are cached per profile and switching between profiles does not bleed settings.
- Profile hydration still uses `include_avatar=0`.
- Dirty controls still win over async profile hydration.
- Metadata-only active-session polling avoids full state DB message reads in the no-change case.
- If state DB summary indicates a possible update, existing exact merge behavior still runs.
- All new tests are deterministic and use isolated state.

## Risks

- Gateway wording could imply WebUI controls a Gateway that was only detected. Mitigation: use "Detected by..." copy.
- Profile settings cache could apply stale settings after a rapid card switch. Mitigation: keep the existing sequence guard and selected-profile check around cached and fetched applies.
- Session fast path could miss external updates. Mitigation: only fast-return when summary is not newer than known metadata; otherwise fall back to exact merge.
