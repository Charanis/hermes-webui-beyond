"""Static checks for the profile screen rework v3 (2026-05-14).

These grep the frontend source for structural contracts: the v3 helpers
exist, the v2 helpers are gone, the files grid uses Lucide icons rather
than single-letter badges, the gateway tile has the wifi indicator, and
the Runtime tile reuses the composer model picker. A regression that silently
deletes one of these signals is caught here without needing a browser.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()
PANELS_JS = (REPO_ROOT / "static" / "panels.js").read_text(encoding="utf-8")
STYLE_CSS = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")
INDEX_HTML = (REPO_ROOT / "static" / "index.html").read_text(encoding="utf-8")
ICONS_JS = (REPO_ROOT / "static" / "icons.js").read_text(encoding="utf-8")
SESSIONS_JS = (REPO_ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
MESSAGES_JS = (REPO_ROOT / "static" / "messages.js").read_text(encoding="utf-8")
COMMANDS_JS = (REPO_ROOT / "static" / "commands.js").read_text(encoding="utf-8")
BOOT_JS = (REPO_ROOT / "static" / "boot.js").read_text(encoding="utf-8")


def _extract_function(src: str, name: str) -> str:
    """Return the body of the named function (including signature)."""
    m = re.search(rf"(?:async\s+)?function {re.escape(name)}\s*\([^)]*\)\s*\{{", src)
    assert m, f"function {name} not found in source"
    i, depth = m.end(), 1
    while i < len(src) and depth > 0:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return src[m.start():i]


# ── Wiring of new icons + CSS ─────────────────────────────────────────────


def test_wifi_icon_registered_in_icons_js():
    assert "'wifi'" in ICONS_JS


def test_v3_css_classes_defined():
    for selector in (
        ".profile-hero",
        ".profile-hero-avatar",
        ".profile-hero-name",
        ".profile-hero-activity",
        ".profile-hero-description",
        ".profile-hero-description-edit",
        ".profile-wifi",
        '.profile-wifi[data-state="running"]',
        ".profile-skill-chip",
    ):
        assert selector in STYLE_CSS, f"missing CSS selector {selector}"


def test_v3_dropped_legacy_classes():
    # The "Activity beam" tile is gone (folded into the hero), as is the
    # mono-font "handle" line that used to render "profile/<name> · local · …".
    assert ".profile-activity-line" not in STYLE_CSS, \
        "standalone activity-line CSS should be removed (folded into hero)"
    assert ".profile-hero-handle" not in STYLE_CSS, \
        "profile-hero-handle CSS should be removed"


def test_profile_list_cards_are_full_width_left_aligned_tiles():
    assert re.search(r"\.profile-card\s*\{[^}]*width:100%", STYLE_CSS), \
        "profile cards in the left list should fill the panel width"
    assert re.search(r"\.profile-card\s*\{[^}]*box-sizing:border-box", STYLE_CSS), \
        "full-width profile cards must include border/padding in their width"
    assert re.search(r"\.profile-card\s*\{[^}]*text-align:left", STYLE_CSS), \
        "profile card button text must be left-aligned"
    assert re.search(r"\.profile-card-header\s*\{[^}]*justify-content:flex-start", STYLE_CSS), \
        "profile card contents should start from the left, not spread or center"


def test_profile_list_active_pill_uses_success_palette():
    m = re.search(r"\.profile-card-active-pill\s*\{(?P<body>[^}]*)\}", STYLE_CSS)
    assert m, "missing .profile-card-active-pill styles"
    body = m.group("body")
    assert "var(--success)" in body, \
        "active profile chip should use the green success palette"
    assert "var(--accent" not in body, \
        "active profile chip should not use the yellow/accent palette"


def test_profile_list_uses_active_as_default_signal_without_redundant_label():
    fn = _extract_function(PANELS_JS, "loadProfilesPanel")
    assert "profile-card-active-pill" in fn, \
        "left profile list should still render the Active chip"
    assert "const defaultBadge" not in fn, \
        "left profile list should not render a separate default badge"
    assert "${defaultBadge}" not in fn, \
        "profile name line should not include a redundant default label"
    assert "ariaLabelParts.push('(default)')" not in fn, \
        "profile card aria-label should not duplicate active/default state"


def test_profile_list_gateway_status_uses_wifi_icon_not_dot():
    fn = _extract_function(PANELS_JS, "loadProfilesPanel")
    assert "profile-card-gateway" in fn, \
        "left profile list should show gateway status with a named wifi affordance"
    assert "li('wifi'" in fn or 'li("wifi"' in fn, \
        "left profile list gateway status should use the shared Lucide wifi icon"
    assert "profile-opt-badge running" not in fn, \
        "left profile list should not reuse the ambiguous dot badge"


def test_profile_list_gateway_icon_has_compact_state_styles():
    assert ".profile-card-gateway" in STYLE_CSS, \
        "left profile list gateway icon needs its own compact styles"
    assert "profile-card-actions" in STYLE_CSS, \
        "gateway and chat controls should share a right-aligned action cluster"
    assert ".profile-card-gateway.profile-wifi" in STYLE_CSS, \
        "left profile list gateway icon should reuse the gateway tile wifi state styles"
    assert '.profile-wifi[data-state="running"]' in STYLE_CSS
    assert '.profile-wifi[data-state="stopped"]' in STYLE_CSS


def test_profile_list_gateway_icon_lives_next_to_chat_button():
    fn = _extract_function(PANELS_JS, "loadProfilesPanel")
    row_start = fn.find('class="profile-card-actions"')
    assert row_start != -1, "profile rows should have a right-aligned actions cluster"
    row = fn[row_start:row_start + 500]
    assert "gatewaySignal" in row, \
        "gateway status icon should live next to the chat bubble, not before the name"
    assert "profile-card-chat-btn" in row
    assert row.find("gatewaySignal") < row.find("profile-card-chat-btn"), \
        "gateway status icon should sit immediately to the left of the chat bubble"
    name_start = fn.find('class="profile-card-name')
    assert "profile-card-gateway" not in fn[name_start:name_start + 260], \
        "gateway status icon should not be embedded in the profile name text"


def test_profile_list_gateway_icon_uses_cached_live_state():
    fn = _extract_function(PANELS_JS, "loadProfilesPanel")
    assert "_profileCardGatewayPhase(p)" in fn, \
        "profile rows should reuse the live gateway state cache when available"
    helper = _extract_function(PANELS_JS, "_profileCardGatewayPhase")
    assert "_gatewayStateByProfile.get(profile.name)" in helper
    label = _extract_function(PANELS_JS, "_profileCardGatewayLabel")
    assert "_gatewayLabelForPhase" in label, \
        "profile rows should expose the same gateway phase labels as the detail tile"


def test_gateway_repaint_updates_left_profile_list_wifi_state():
    fn = _extract_function(PANELS_JS, "_repaintGatewayTile")
    assert "_repaintProfileCardGateway(profileName, phase)" in fn, \
        "gateway repaint should update the left profile row indicator too"
    helper = _extract_function(PANELS_JS, "_repaintProfileCardGateway")
    assert ".profile-card-gateway" in helper
    assert "querySelectorAll" in helper
    assert "setAttribute('data-state', phase" in helper


def test_hero_avatar_is_256px():
    # The hero avatar must render at 256×256 per the rework spec.
    assert re.search(r"\.profile-hero-avatar\s*\{[^}]*width:256px", STYLE_CSS), \
        "profile-hero-avatar must declare width:256px"
    assert re.search(r"\.profile-hero-avatar\s*\{[^}]*height:256px", STYLE_CSS), \
        "profile-hero-avatar must declare height:256px"


# ── v3 helpers present, v2 helpers gone ───────────────────────────────────


def test_v3_helpers_defined():
    for helper in (
        "_profileHeroDossier",
        "_profileRuntimePanel",
        "_profileGatewayTile",
        "_profileSkillsTile",
        "_profileFilesSection",
        "_hydrateProfileDescription",
        "_hydrateProfileActivity",
        "_hydrateProfileDefaultModel",
        "_loadProfileSkillsTile",
    ):
        assert f"function {helper}" in PANELS_JS, f"missing helper {helper}"


def test_v2_helpers_removed():
    # _profileIdentityPlane and _profileOpsTiles are gone; only their removal
    # comments survive ("// _profileIdentityPlane: removed..." etc.).
    assert "function _profileIdentityPlane" not in PANELS_JS
    assert "function _profileOpsTiles" not in PANELS_JS


def test_standalone_activity_line_helper_removed_in_v3_1():
    # The activity line was folded into the hero dossier — its dedicated
    # helper and its container id are both gone.
    assert "function _profileActivityLine" not in PANELS_JS
    assert "_hydrateProfilePersona" not in PANELS_JS, \
        "persona hydrator should be renamed to _hydrateProfileDescription"


def test_render_detail_calls_v3_helpers_in_order():
    fn = _extract_function(PANELS_JS, "_renderProfileDetail")
    order = []
    for needle in (
        "_profileHeroDossier",
        "_profileRuntimePanel",
        "_profileGatewayTile",
        "_profileSkillsTile",
        "_profileFilesSection",
    ):
        idx = fn.find(needle)
        assert idx >= 0, f"_renderProfileDetail does not call {needle}"
        order.append((idx, needle))
    assert order == sorted(order), \
        f"helpers called in wrong order: {[n for _, n in order]}"
    # The standalone activity line must NOT be rendered separately anymore.
    assert "_profileActivityLine" not in fn
    assert 'id="profileActivityLine"' not in PANELS_JS


# ── Hero dossier ──────────────────────────────────────────────────────────


def test_hero_dossier_has_no_bare_diode_next_to_name():
    """The Active pill carries the diode; a second one next to the name
    would just repeat the signal."""
    fn = _extract_function(PANELS_JS, "_profileHeroDossier")
    # The hero name container is .profile-hero-name. There must be at most one
    # profile-status-dot inside the hero, and it must live inside the pill.
    matches = re.findall(r"profile-status-dot", fn)
    assert len(matches) <= 1, \
        f"hero dossier should have at most one status dot (inside the Active pill), found {len(matches)}"


def test_hero_dossier_uses_inline_actions_not_overflow_menu():
    fn = _extract_function(PANELS_JS, "_profileHeroDossier")
    assert 'data-ops-action="rename"' in fn
    assert 'data-ops-action="duplicate"' in fn
    assert 'data-ops-action="remove"' in fn
    # The v2 overflow menu IDs must not appear in v3 hero.
    assert "opsMoreActions" not in fn
    assert "opsProfileMenu" not in fn


def test_hero_dossier_renders_activity_slot():
    # The activity line lives inside the hero now (v3.1), replacing the
    # monospaced "profile/<name> · local" handle line.
    fn = _extract_function(PANELS_JS, "_profileHeroDossier")
    assert 'id="profileHeroActivity"' in fn, \
        "hero must contain the activity slot retargeted from the standalone row"
    assert "profile-hero-handle" not in fn, \
        "the v2 handle line must be gone from the hero"
    assert "profile/${name}" not in fn, \
        "the v2 handle template literal must be removed"


def test_hero_dossier_has_inline_description_editor():
    fn = _extract_function(PANELS_JS, "_profileHeroDossier")
    assert 'id="profileHeroDescription"' in fn, \
        "hero must expose the description slot"
    assert 'id="profileHeroDescriptionEdit"' in fn, \
        "hero must expose the description pencil button"
    # The v2 'Edit persona' SOUL-bound button is gone — SOUL is editable
    # via the files grid instead.
    assert 'data-ops-action="edit-soul"' not in fn


def test_description_hydrator_targets_correct_element_and_posts_settings():
    fn = _extract_function(PANELS_JS, "_hydrateProfileDescription")
    assert "profileHeroDescription" in fn
    assert "/api/profile/persona" in fn
    assert "data.description" in fn
    # Save flow posts to /api/profile/settings with a description field.
    save = _extract_function(PANELS_JS, "_exitProfileDescriptionEdit")
    assert "/api/profile/settings" in save
    assert "description" in save


def test_activity_hydrator_writes_into_hero_slot():
    fn = _extract_function(PANELS_JS, "_hydrateProfileActivity")
    assert "profileHeroActivity" in fn, \
        "activity hydrator must write into the hero slot (v3.1)"
    # The standalone container id is dead.
    assert "profileActivityLine" not in fn
    # No more "Open activity ›" link — folded out in v3.1.
    assert "open-activity" not in fn


# ── Runtime tile (profile routing rework 2026-05-17) ─────────────────────


def test_runtime_tile_title_and_compact_rows():
    fn = _extract_function(PANELS_JS, "_profileRuntimePanel")
    assert ">Runtime<" in fn, "tile title must be 'Runtime'"
    assert "profile-runtime-row-label\">Default Model" in fn
    assert "profile-runtime-row-label\">Fallback Model" in fn
    assert "Configure auxiliary tool models" in fn
    assert "profile-runtime-footer-action" in fn, \
        "auxiliary-models entry must be pinned to the bottom of the Runtime tile"
    assert "Used for new sessions" not in fn, \
        "scope explainer text was intentionally removed to keep the tile compact"


def test_runtime_tile_keeps_single_tile_footprint():
    assert not re.search(r"\.profile-runtime-tile\s*\{[^}]*grid-column\s*:\s*span\s+2", STYLE_CSS), \
        "Runtime must not be forced to span two grid columns"


def test_runtime_tile_reuses_composer_chips_and_adds_fallback():
    """Runtime uses existing picker chrome: default model + reasoning,
    fallback model, and no separate provider chip."""
    fn = _extract_function(PANELS_JS, "_profileRuntimePanel")
    assert "profileDefaultModelChip" in fn, "Model chip missing"
    assert "profileDefaultReasoningChip" in fn, "Reasoning chip missing"
    assert "profileFallbackModelChip" in fn, "Fallback model chip missing"
    # No provider chip — was profileRuntimeProviderChip in v3.
    assert "profileRuntimeProviderChip" not in fn, \
        "Provider chip must be removed — provider is inferred from the chosen model"
    assert "profileDefaultProviderChip" not in fn, \
        "Provider chip must be removed entirely"
    # Both chips share the chat composer's chrome.
    assert "composer-model-chip" in fn
    # The model dropdown reuses the composer's .model-dropdown chrome (same renderer).
    assert "model-dropdown profile-default-model-dropdown" in fn, \
        "Model dropdown must reuse .model-dropdown chrome from the chat composer"
    # Reasoning dropdown reuses the composer's .composer-reasoning-dropdown chrome.
    assert "composer-reasoning-dropdown profile-default-reasoning-dropdown" in fn, \
        "Reasoning dropdown must reuse the chat composer's .composer-reasoning-dropdown"


def test_runtime_tile_drops_apply_diagnostics_status_and_new_chat():
    """Auto-save flow — no Apply button, no Diagnostics, no status pill, no
    'Saved' diode. New-chat moved to the profile list."""
    fn = _extract_function(PANELS_JS, "_profileRuntimePanel")
    # No Apply button (was id="opsRuntimeApply").
    assert "opsRuntimeApply" not in fn
    assert ">Apply<" not in fn
    # No Diagnostics button.
    assert 'data-ops-action="diagnostics"' not in fn
    assert ">Diagnostics<" not in fn
    # No status pill ids.
    assert "profileRuntimeStatusPill" not in fn
    assert "opsRuntimeDot" not in fn
    assert "opsRuntimeState" not in fn
    # No "Saved" diode label.
    assert ">Saved<" not in fn
    assert "opsStartChat" not in fn
    assert "New Chat" not in fn


def test_runtime_tile_includes_hidden_select_mirrors():
    """The parameterised composer picker reads optgroups from a <select>
    mirror. The tile must include model selects so
    renderModelDropdown({select: …}) has a catalog to walk."""
    fn = _extract_function(PANELS_JS, "_profileRuntimePanel")
    assert 'id="profileDefaultModelSelect"' in fn
    assert 'id="profileFallbackModelSelect"' in fn


def test_default_model_dropdown_uses_parameterised_composer_renderer():
    """The model picker should call the chat composer's actual renderer
    (renderModelDropdown) with opts pointing at the tile's own select +
    dropdown, NOT a stripped-down knockoff."""
    fn = _extract_function(PANELS_JS, "_toggleProfileDefaultModelDropdown")
    # Must invoke renderModelDropdown with an opts object.
    assert "renderModelDropdown({" in fn, \
        "_toggleProfileDefaultModelDropdown must call renderModelDropdown with opts"
    assert "select: sel" in fn or "select:sel" in fn, \
        "renderModelDropdown opts must pass the tile's select"
    assert "dropdown: dd" in fn or "dropdown:dd" in fn, \
        "renderModelDropdown opts must pass the tile's dropdown"
    assert "onSelect" in fn, "renderModelDropdown opts must wire onSelect to the auto-save path"


def test_fallback_model_dropdown_uses_parameterised_composer_renderer():
    fn = _extract_function(PANELS_JS, "_toggleProfileFallbackModelDropdown")
    assert "renderModelDropdown({" in fn
    assert "select: sel" in fn or "select:sel" in fn
    assert "dropdown: dd" in fn or "dropdown:dd" in fn
    assert "_onProfileFallbackModelPicked" in fn


def test_render_model_dropdown_is_parameterised():
    """renderModelDropdown() in ui.js must accept an opts object so other
    surfaces can reuse it without it hard-coding the chat composer's
    DOM ids."""
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    fn = _extract_function(ui_js, "renderModelDropdown")
    # The signature accepts opts (back-compat: with no args, behaviour is
    # identical to the legacy call).
    assert re.search(r"function renderModelDropdown\(\s*opts\s*\)", fn), \
        "renderModelDropdown must accept an optional opts argument"
    # When opts.dropdown / opts.select are passed, they override the
    # composer globals.
    assert "opts && opts.dropdown" in fn
    assert "opts && opts.select" in fn
    assert "opts.onSelect" in fn or "typeof opts.onSelect" in fn


def test_persist_profile_default_model_posts_to_settings():
    """Auto-save path POSTs to /api/profile/settings with name/model/
    provider/reasoning_effort."""
    fn = _extract_function(PANELS_JS, "_persistProfileDefaultModel")
    assert "/api/profile/settings" in fn
    assert "reasoning_effort" in fn
    # Provider is derived via _modelStateForSelect from the chosen model.
    assert "_modelStateForSelect" in fn
    # No status pill writes (the tile has no pill).
    assert "opsRuntimeDot" not in fn
    assert "Saving" not in fn  # no "Saving…" indicator


def test_profile_list_has_right_aligned_chat_icon():
    fn = _extract_function(PANELS_JS, "loadProfilesPanel")
    assert "profile-card-chat-btn" in fn
    assert "message-square" in fn
    assert "startChatWithProfile(p.name)" in fn
    assert "stopPropagation" in fn
    assert ".profile-card-chat-btn" in STYLE_CSS


def test_profile_chat_icon_starts_chat_without_switching_active_profile():
    fn = _extract_function(PANELS_JS, "startChatWithProfile")
    assert "switchToProfile" not in fn, \
        "profile-row chat must not change the active/default profile"
    assert "_profileChatDefaults(profileName)" in fn
    assert "newSession(true, defaults)" in fn
    defaults = _extract_function(PANELS_JS, "_profileChatDefaults")
    assert "/api/profile/settings?name=" in defaults
    assert "include_avatar=0" in defaults


def test_profile_scoped_new_session_uses_requested_profile_without_consuming_active_defaults():
    fn = _extract_function(SESSIONS_JS, "newSession")
    assert "hasOption('profile')" in fn
    assert "const targetProfile=" in fn
    assert "profile:targetProfile" in fn
    assert "explicitWorkspace" in fn, \
        "profile-card chat must be able to pass a profile workspace without inheriting the active session workspace"


def test_send_and_queue_use_session_profile_before_active_profile():
    assert "function currentSessionProfile()" in SESSIONS_JS
    assert "return (S.session&&S.session.profile)||S.activeProfile||'default';" in SESSIONS_JS
    assert "profile:currentSessionProfile()" in MESSAGES_JS
    assert "profile:currentSessionProfile()" in COMMANDS_JS
    assert "profile:S.activeProfile||S.session.profile||'default'" not in MESSAGES_JS
    assert "profile:S.activeProfile||'default'" not in MESSAGES_JS
    assert "profile:S.activeProfile||S.session.profile||'default'" not in COMMANDS_JS
    assert "profile:S.activeProfile||'default'" not in COMMANDS_JS


def test_new_chat_and_no_session_send_do_not_wait_for_sidebar_refresh():
    assert "await newSession();await renderSessionList()" not in BOOT_JS
    assert "if(!S.session){await newSession();await renderSessionList();}" not in MESSAGES_JS
    assert "if(!S.session){await newSession();await renderSessionList();}" not in COMMANDS_JS
    assert "void renderSessionList({deferWhileInteracting:true})" in BOOT_JS
    assert "void renderSessionList({deferWhileInteracting:true})" in MESSAGES_JS
    assert "void renderSessionList({deferWhileInteracting:true})" in COMMANDS_JS


def test_session_list_includes_current_non_active_profile_session():
    fn = _extract_function(SESSIONS_JS, "renderSessionList")
    assert "needsCurrentProfile" in fn
    assert "sessionProfile!==S.activeProfile" in fn
    assert "(_showAllProfiles||needsCurrentProfile) ? '?all_profiles=1' : ''" in fn


def test_response_mode_and_default_space_live_in_hero_not_separate_tile():
    hero = _extract_function(PANELS_JS, "_profileHeroDossier")
    detail = _extract_function(PANELS_JS, "_renderProfileDetail")
    assert "profileResponseModeSelect" in hero
    assert "Response Style" in hero
    assert '<option value="">Soul-driven</option>' in hero
    assert ">none</option>" not in hero
    assert "profileDefaultWorkspaceChip" in hero
    assert "profileDefaultWorkspaceDropdown" in hero
    assert "_profileDefaultSpaceTile" not in detail
    assert "function _profileDefaultSpaceTile" not in PANELS_JS


def test_response_style_and_default_space_are_bottom_aligned_and_matched():
    assert re.search(
        r"\.profile-hero-actions\s*\{[^}]*margin-top\s*:\s*auto[^}]*align-items\s*:\s*end",
        STYLE_CSS,
    ), "hero controls must sit on the bottom edge of the hero body"
    assert re.search(
        r"\.profile-response-mode-control,\s*\.profile-default-workspace-control\s*\{[^}]*grid-template-rows\s*:\s*auto\s+36px",
        STYLE_CSS,
    ), "response style and default space controls must share label/control row sizing"
    assert re.search(
        r"\.profile-response-mode-control\s+\.profile-ops-select\s*\{[^}]*border-radius\s*:\s*999px",
        STYLE_CSS,
    ), "response style select should visually match the workspace chip chrome"


def test_compression_budget_and_tools_tiles_render():
    detail = _extract_function(PANELS_JS, "_renderProfileDetail")
    for helper in (
        "_profileContextCompressionTile",
        "_profileWorkstepBudgetTile",
        "_profileToolAccessTile",
    ):
        assert helper in detail
    assert "profileCompressionThreshold" in PANELS_JS
    assert "profileMaxTurnsInput" in PANELS_JS
    assert "profile-toolset-pill" in PANELS_JS


def test_profile_default_space_reuses_workspace_dropdown_renderer():
    renderer = _extract_function(PANELS_JS, "renderWorkspaceDropdownInto")
    assert re.search(r"function renderWorkspaceDropdownInto\(\s*dd\s*,\s*workspaces\s*,\s*currentWs\s*,\s*opts", renderer), \
        "workspace dropdown renderer must accept options for profile-default reuse"
    assert "opts.onSelect" in renderer or "typeof onSelect" in renderer
    assert "includeSessionActions" in renderer, \
        "profile default picker must be able to omit new-chat-only footer actions"

    toggle = _extract_function(PANELS_JS, "_toggleProfileDefaultWorkspaceDropdown")
    assert "renderWorkspaceDropdownInto(dd, data.workspaces" in toggle
    assert "includeSessionActions: false" in toggle
    assert "_persistProfileDefaultWorkspace" in PANELS_JS

    wiring = _extract_function(PANELS_JS, "_wireProfileRuntimeSettingHandlers")
    assert "profileDefaultWorkspaceChange" not in wiring
    assert "showInputDialog" not in wiring


def test_context_compression_is_always_on_without_disable_switch():
    tile = _extract_function(PANELS_JS, "_profileContextCompressionTile")
    assert "profileCompressionEnabled" not in tile
    assert "profile-switch" not in tile

    payload = _extract_function(PANELS_JS, "_profileCompressionPayload")
    assert "profileCompressionEnabled" not in payload
    assert "enabled: true" in payload or "enabled:true" in payload

    wiring = _extract_function(PANELS_JS, "_wireProfileRuntimeSettingHandlers")
    assert "profileCompressionEnabled" not in wiring


def test_profile_runtime_controls_wire_before_async_hydration():
    detail = _extract_function(PANELS_JS, "_renderProfileDetail")
    prime_idx = detail.find("_primeProfileRuntimeControls(p)")
    hydrate_idx = detail.find("_hydrateProfileRuntimeSettings(p")
    assert prime_idx >= 0, \
        "profile render must synchronously prime runtime controls"
    assert hydrate_idx >= 0, \
        "profile render must still hydrate saved runtime settings"
    assert prime_idx < hydrate_idx, \
        "runtime handlers must be attached before async settings/model hydration"

    prime = _extract_function(PANELS_JS, "_primeProfileRuntimeControls")
    assert "_applyProfileCompression(_PROFILE_COMPRESSION_DEFAULTS)" in prime
    assert "_wireProfileDefaultModelHandlers(profile.name)" in prime
    assert "_wireProfileRuntimeSettingHandlers(profile.name)" in prime


def test_profile_runtime_hydration_preserves_dirty_controls():
    hydrate = _extract_function(PANELS_JS, "_hydrateProfileRuntimeSettings")
    wiring = _extract_function(PANELS_JS, "_wireProfileRuntimeSettingHandlers")
    assert "_isCurrentProfileRuntimeHydration" in hydrate, \
        "stale profile hydration must not repaint after the user switches profiles"
    assert "dirty.compression" in hydrate, \
        "late settings fetch must not overwrite a compression edit already made in the UI"
    assert "_markProfileRuntimeDirty('compression')" in wiring, \
        "compression slider input must mark the field dirty before save/hydration races"


def test_profile_runtime_hydration_omits_full_avatar_payload():
    hydrate = _extract_function(PANELS_JS, "_hydrateProfileRuntimeSettings")

    assert "include_avatar=0" in hydrate, \
        "runtime settings hydration must not fetch full uploaded avatar data URLs"


def test_profile_runtime_saves_ignore_stale_post_completions():
    setting = _extract_function(PANELS_JS, "_persistProfileSetting")
    assert "const token = _profileRuntimeHydrationSeq" in setting, \
        "runtime setting saves must capture the rendered profile token before awaiting"
    assert "_isCurrentProfileRuntimeHydration(profileName, token)" in setting, \
        "runtime setting saves must gate success and failure DOM side effects"
    assert re.search(r"if\s*\(\s*_isCurrentProfileRuntimeHydration\(profileName,\s*token\)\s*&&\s*typeof onFailure", setting), \
        "failed stale runtime saves must not roll back the newly selected profile"

    default_model = _extract_function(PANELS_JS, "_persistProfileDefaultModel")
    assert "const token = _profileRuntimeHydrationSeq" in default_model
    assert "loadProfilesPanel()" not in default_model, \
        "default model saves must not re-render the profile detail after an async POST"
    assert "_refreshProfileCardRuntimeMeta(profileName)" in default_model, \
        "default model saves should refresh the left-list meta without repainting controls"

    default_workspace = _extract_function(PANELS_JS, "_persistProfileDefaultWorkspace")
    assert "const token = _profileRuntimeHydrationSeq" in default_workspace
    assert "_isCurrentProfileRuntimeHydration(profileName, token)" in default_workspace, \
        "late default-space saves must not repaint another profile's workspace chip"

    auxiliary = _extract_function(PANELS_JS, "_persistProfileAuxModel")
    assert "const token = _profileRuntimeHydrationSeq" in auxiliary
    assert "_isCurrentProfileRuntimeHydration(profileName, token)" in auxiliary, \
        "late auxiliary-model saves must not repaint a stale auxiliary model overlay"


def test_auxiliary_models_screen_reuses_model_picker():
    fn = _extract_function(PANELS_JS, "_openProfileAuxModels")
    assert "profile-skills-manager-overlay" in fn
    assert "profileAuxModelsTitle" in fn
    assert "composer-model-chip" in fn
    assert "model-dropdown profile-default-model-dropdown profile-aux-model-dropdown" in fn
    toggle = _extract_function(PANELS_JS, "_toggleProfileAuxModelDropdown")
    assert "renderModelDropdown({" in toggle
    persist = _extract_function(PANELS_JS, "_persistProfileAuxModel")
    assert "auxiliary_models" in persist


# ── Gateway tile ──────────────────────────────────────────────────────────


def test_gateway_tile_uses_wifi_icon():
    fn = _extract_function(PANELS_JS, "_profileGatewayTile")
    assert "profile-wifi" in fn
    assert "li('wifi'" in fn or 'li("wifi"' in fn, "gateway tile must call li('wifi', …)"


def test_gateway_repaint_updates_wifi_state_and_disabled_control():
    fn = _extract_function(PANELS_JS, "_repaintGatewayTile")
    assert "profileGatewayWifi" in fn, "_repaintGatewayTile must reach the wifi indicator"
    assert "setAttribute('data-state', phase)" in fn
    assert "control_available" in fn
    assert "toggle.disabled" in fn


# ── Skills tile ───────────────────────────────────────────────────────────


def test_skills_tile_has_top_chips_container():
    fn = _extract_function(PANELS_JS, "_profileSkillsTile")
    assert "opsSkillsTopChips" in fn
    assert "profile-skill-top" in fn


def test_skills_hydrator_renders_chips_and_more_overflow():
    fn = _extract_function(PANELS_JS, "_applyProfileSkillsSummary")
    assert "profile-skill-chip" in fn
    assert "profile-skill-more" in fn


# ── Files grid (Lucide icons replace letter badges) ───────────────────────


def test_files_section_uses_lucide_icons():
    fn = _extract_function(PANELS_JS, "_profileFilesSection")
    # Accept either literal li('icon',…) calls or `icon: 'icon'` entries in a
    # data-driven files array — both wire through to li() at render time.
    assert "li(f.icon" in fn or any(
        re.search(rf"li\(\s*['\"]{re.escape(icon)}['\"]", fn)
        for icon in ("user", "brain", "settings", "lock", "file-code")
    ), "files section must render Lucide icons (either li(f.icon, …) or literal li('user', …))"
    for icon in ("user", "brain", "settings", "lock", "file-code"):
        # The icon name must appear at least once, either as a literal li(...)
        # arg or as a value in the files array.
        present = (
            re.search(rf"li\(\s*['\"]{re.escape(icon)}['\"]", fn)
            or re.search(rf"icon:\s*['\"]{re.escape(icon)}['\"]", fn)
        )
        assert present, f"missing Lucide icon {icon!r} reference in files section"


def test_files_section_drops_single_letter_badges():
    fn = _extract_function(PANELS_JS, "_profileFilesSection")
    # The v2 files used icon: 'S' / 'M' / 'U' / 'E' / 'Y'. None should survive
    # in the live helper.
    for letter in ("'S'", "'M'", "'U'", "'E'", "'Y'"):
        assert f"icon: {letter}" not in fn, f"v2 letter badge still present: icon: {letter}"


# ── Bindings ──────────────────────────────────────────────────────────────


def test_bindings_handle_v3_action_buttons():
    fn = _extract_function(PANELS_JS, "_bindProfileOpsConsole")
    # v3.1 dropped the edit-soul and open-activity hero buttons — SOUL is
    # editable via the files grid; activity stats render in the hero itself.
    # The 2026-05-15 default-model rework follow-up also dropped diagnostics
    # (its button is not rendered anywhere on the v3 profile screen).
    for action in ("rename", "duplicate", "remove", "skills"):
        assert f'data-ops-action="{action}"' in fn, \
            f"binding for data-ops-action={action!r} is missing"
    for dropped in ("edit-soul", "open-activity", "diagnostics"):
        assert f'data-ops-action="{dropped}"' not in fn, \
            f"binding for the dropped action {dropped!r} must be removed"


def test_bindings_wire_description_edit():
    fn = _extract_function(PANELS_JS, "_bindProfileOpsConsole")
    assert "profileHeroDescription" in fn, \
        "binding for description click is missing"
    assert "profileHeroDescriptionEdit" in fn, \
        "binding for description pencil is missing"
    assert "_enterProfileDescriptionEdit" in fn, \
        "binding must invoke the inline editor"


# ── Hero overflow menu (rework v3.1, 2026-05-15) ─────────────────────────


def test_hero_overflow_menu_present_with_expected_items():
    fn = _extract_function(PANELS_JS, "_profileHeroDossier")
    assert 'id="profileHeroMenuButton"' in fn, "missing ⋯ menu button"
    assert 'id="profileHeroMenu"' in fn, "missing menu container"
    assert 'aria-haspopup="menu"' in fn, "menu button must declare popup role"
    for action in ("rename", "edit-description", "duplicate", "remove"):
        assert f'data-ops-action="{action}"' in fn, \
            f"hero menu must contain action={action!r}"


def test_inline_action_row_no_longer_holds_destructive_actions():
    fn = _extract_function(PANELS_JS, "_profileHeroDossier")
    # Locate the substring of the inline-actions row.
    marker = 'class="profile-hero-actions"'
    idx = fn.find(marker)
    assert idx >= 0, "hero must still render the .profile-hero-actions row"
    # The row should end at the next </div> after the marker; pull a generous
    # slice and confirm none of the moved actions appear there.
    slice_ = fn[idx:idx + 600]
    for moved in ("rename", "duplicate", "remove"):
        assert f'data-ops-action="{moved}"' not in slice_, \
            f"action={moved!r} should live in the ⋯ menu, not the inline row"


def test_make_active_lives_in_hero_name_slot_as_pill_action():
    fn = _extract_function(PANELS_JS, "_profileHeroDossier")
    assert 'id="opsMakeActive"' in fn, "inactive profiles must still expose Make Active"
    assert "profile-active-pill--action" in fn, \
        "Make Active should be styled as the inactive replacement for the Active pill"
    assert fn.find('id="opsMakeActive"') < fn.find('class="profile-hero-actions"'), \
        "Make Active should render in the title row before the lower action controls"
    idx = fn.find('class="profile-hero-actions"')
    assert 'id="opsMakeActive"' not in fn[idx:idx + 600], \
        "Make Active must not remain in the lower hero action row"


def test_make_active_uses_yellow_action_palette_not_success_green():
    m = re.search(r"\.profile-active-pill--action\s*\{(?P<body>[^}]*)\}", STYLE_CSS)
    assert m, "missing Make Active action pill styles"
    body = m.group("body")
    assert "var(--warning" in body or "var(--accent" in body or "var(--gold" in body, \
        "Make Active should use a yellow/action palette so it stands out from Active"
    assert "var(--success)" not in body, \
        "Make Active should not look like the green Active state"


def test_make_active_uses_lightweight_profile_panel_activation():
    binder = _extract_function(PANELS_JS, "_bindProfileOpsConsole")
    assert "_activateProfileFromPanel(profileName)" in binder, \
        "Make Active should use the profile-panel activation path"
    assert "switchToProfile(profileName)" not in binder, \
        "Make Active should not run the full chat/session profile switch path"
    activate = _extract_function(PANELS_JS, "_activateProfileFromPanel")
    assert "/api/profile/switch" in activate
    assert "newSession" not in activate
    assert "populateModelDropdown" not in activate
    assert "loadWorkspaceList" not in activate


def test_profile_header_drops_redundant_activate_and_delete_icons():
    assert "btnActivateProfileDetail" not in INDEX_HTML, \
        "profile header should not render the redundant activate icon button"
    assert "btnDeleteProfileDetail" not in INDEX_HTML, \
        "profile header should not render the redundant delete icon button"
    fn = _extract_function(PANELS_JS, "_setProfileHeaderButtons")
    assert "btnActivateProfileDetail" not in fn
    assert "btnDeleteProfileDetail" not in fn


def test_bindings_wire_hero_overflow_menu():
    fn = _extract_function(PANELS_JS, "_bindProfileOpsConsole")
    assert "profileHeroMenuButton" in fn, "binding for the menu button is missing"
    assert "profileHeroMenu" in fn, "binding for the menu container is missing"
    assert 'data-ops-action="edit-description"' in fn, \
        "binding must handle the new edit-description menu item"


# ── In-app input dialog (replaces window.prompt) ─────────────────────────


def test_show_input_dialog_defined():
    assert "function showInputDialog" in PANELS_JS, \
        "showInputDialog must be defined globally (replaces window.prompt)"


def test_rename_does_not_call_window_prompt():
    fn = _extract_function(PANELS_JS, "_opsRenameProfile")
    assert "window.prompt" not in fn, \
        "rename must use the in-app input dialog, not window.prompt"
    assert "showInputDialog" in fn
    assert "maxlength" in fn, "rename dialog must enforce the 32-char cap"


def test_duplicate_does_not_call_window_prompt():
    fn = _extract_function(PANELS_JS, "_opsDuplicateProfile")
    assert "window.prompt" not in fn
    assert "showInputDialog" in fn
    assert "maxlength" in fn


def test_no_remaining_window_prompt_calls_in_panels_js():
    # Defense in depth — any prompt() left in panels.js is a regression.
    assert "window.prompt(" not in PANELS_JS, \
        "window.prompt is forbidden in panels.js (use showInputDialog)"


def test_input_dialog_css_defined():
    for selector in (".input-dialog", ".input-dialog-card",
                     ".input-dialog-error", ".input-dialog-counter"):
        assert selector in STYLE_CSS, f"missing CSS selector {selector}"


def test_hero_menu_css_defined():
    for selector in (".profile-hero-menu",
                     ".profile-hero-menu-button",
                     ".profile-hero-menu-item"):
        assert selector in STYLE_CSS, f"missing CSS selector {selector}"


# ── Description editor: Cancel/Save bubble-trigger fix (2026-05-15) ──────


def test_description_inline_action_clicks_stop_propagation():
    """The Cancel/Save buttons inside the inline description editor MUST call
    event.stopPropagation() on click. Otherwise the click bubbles up to the
    #profileHeroDescription host listener — but by the time the bubble
    arrives, _exitProfileDescriptionEdit has already cleared
    dataset.editing, so the host's guard (editing === '1') misses, and the
    host re-enters edit mode immediately. Result before fix: Cancel appeared
    to do nothing; Save left the editor open after the POST resolved.
    """
    fn = _extract_function(PANELS_JS, "_enterProfileDescriptionEdit")
    # Locate the inner click handler that dispatches on data-desc-action.
    idx = fn.find("data-desc-action")
    assert idx >= 0, "edit-mode binding for [data-desc-action] is missing"
    # Grep the tail of the function for stopPropagation — the handler must
    # call it before invoking _exitProfileDescriptionEdit.
    tail = fn[idx:]
    assert "stopPropagation" in tail, (
        "Cancel/Save click handlers in the inline description editor must "
        "call event.stopPropagation() so the click does not bubble to the "
        "host's click listener and re-enter edit mode."
    )


# ── Default-model tile follow-up fixes (2026-05-15 pass 2) ──────────────


def test_default_model_tile_has_responsive_chip_override():
    """The shared `.composer-model-chip` class is shrunk to 44×44 at <640px
    by the composer footer's compaction rules. The profile tile needs a
    more-specific override so its chip keeps label + chevron and fills the
    tile column. Specificity = `.profile-default-model-tile .composer-model-*`
    beats the composer's single-class `@media(max-width:640px)` rule without
    `!important`."""
    assert ".profile-default-model-tile .composer-model-label" in STYLE_CSS, \
        "missing profile-tile-scoped label override (regression: chip becomes icon-only at narrow widths)"
    assert ".profile-default-model-tile .composer-model-chevron" in STYLE_CSS, \
        "missing profile-tile-scoped chevron override"
    assert ".profile-default-model-tile .composer-model-chip" in STYLE_CSS, \
        "missing profile-tile-scoped chip override"
    # No !important — the brief says use higher specificity instead.
    chip_block = re.search(
        r"\.profile-default-model-tile \.composer-model-chip\s*\{[^}]*\}",
        STYLE_CSS,
    )
    assert chip_block, "could not locate the chip override block"
    assert "!important" not in chip_block.group(0), \
        "override must win on specificity alone, not !important"


def test_default_model_dropdown_supports_flip_up():
    """When the tile sits near the viewport bottom on a short screen, the
    dropdown must flip above the chip instead of clipping. The CSS owns the
    resting position; a .flipped class swaps top↔bottom."""
    assert ".profile-default-model-dropdown.model-dropdown.flipped" in STYLE_CSS, \
        "missing flip-up CSS rule for the model dropdown"
    assert ".profile-default-reasoning-dropdown.composer-reasoning-dropdown.flipped" in STYLE_CSS, \
        "missing flip-up CSS rule for the reasoning dropdown"
    # JS-side helper must exist and be called from at least one toggle path.
    assert "function _positionProfileDefaultDropdown" in PANELS_JS, \
        "missing _positionProfileDefaultDropdown flip-up helper"
    toggle_model = _extract_function(PANELS_JS, "_toggleProfileDefaultModelDropdown")
    assert "_positionProfileDefaultDropdown" in toggle_model, \
        "model toggle must call the flip-up positioner after opening"
    toggle_reasoning = _extract_function(PANELS_JS, "_toggleProfileDefaultReasoningDropdown")
    assert "_positionProfileDefaultDropdown" in toggle_reasoning, \
        "reasoning toggle must call the flip-up positioner after opening"


def test_persist_default_model_has_revert_path_on_failure():
    """Auto-save must capture priors, attempt the POST, and on failure revert
    the chip + select-mirror to the prior values and surface a toast (no
    'Saved' diode for the happy path by design)."""
    fn = _extract_function(PANELS_JS, "_persistProfileDefaultModel")
    assert "try {" in fn or "try{" in fn, "_persistProfileDefaultModel must wrap the POST in try/catch"
    assert "catch" in fn, "_persistProfileDefaultModel must have a catch branch"
    assert "showToast" in fn, "failure path must surface a toast"
    assert "Default model save failed" in fn, \
        "failure toast must use a recognisable message"
    assert "console.warn" in fn, "failure path must console-warn for debuggability"
    # Revert path must restore the prior model chip label.
    assert "_applyProfileDefaultModelChip(priorModel)" in fn, \
        "failure path must revert the model chip to the prior value"
    assert "_applyProfileDefaultReasoningChip(priorReasoning)" in fn, \
        "failure path must revert the reasoning chip to the prior value"


def test_persist_default_model_signature_accepts_priors():
    """The persist function takes a priors object captured at the call site
    (before the optimistic UI update) so a failed POST can revert without
    re-fetching."""
    assert re.search(
        r"function _persistProfileDefaultModel\(\s*profileName\s*,\s*priors\s*\)",
        PANELS_JS,
    ), "_persistProfileDefaultModel must accept a priors argument"
    # Both callers must pass priors.
    model_picked = _extract_function(PANELS_JS, "_onProfileDefaultModelPicked")
    assert "_persistProfileDefaultModel(profileName, priors)" in model_picked, \
        "model-picked handler must forward priors"
    reasoning_picked = _extract_function(PANELS_JS, "_onProfileDefaultReasoningPicked")
    assert "_persistProfileDefaultModel(profileName, priors)" in reasoning_picked, \
        "reasoning-picked handler must forward priors"


def test_render_reasoning_dropdown_is_parameterised():
    """Mirrors renderModelDropdown: the reasoning renderer in ui.js must
    accept an opts object so other surfaces can reuse it without duplicating
    the row build + click logic."""
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    assert "function renderReasoningDropdown" in ui_js, \
        "missing renderReasoningDropdown extraction"
    fn = _extract_function(ui_js, "renderReasoningDropdown")
    assert re.search(r"function renderReasoningDropdown\(\s*opts\s*\)", fn), \
        "renderReasoningDropdown must accept an optional opts argument"
    assert "opts && opts.dropdown" in fn or "opts.dropdown" in fn, \
        "renderReasoningDropdown must read opts.dropdown"
    assert "opts.onSelect" in fn or "typeof opts.onSelect" in fn, \
        "renderReasoningDropdown must support opts.onSelect"
    # When a foreign onSelect is supplied, rows must stop propagation so the
    # composer's document-level handler doesn't double-fire.
    assert "stopPropagation" in fn, \
        "foreign-surface rows must call ev.stopPropagation() to keep the composer's document-level handler from firing"


def test_profile_reasoning_uses_shared_renderer_not_local_opts():
    """The local _PROFILE_DEFAULT_REASONING_OPTS constant must be gone — the
    profile tile must call renderReasoningDropdown(opts) instead."""
    assert "_PROFILE_DEFAULT_REASONING_OPTS" not in PANELS_JS, \
        "local reasoning opts list must be removed — use renderReasoningDropdown(opts) instead"
    toggle = _extract_function(PANELS_JS, "_toggleProfileDefaultReasoningDropdown")
    assert "renderReasoningDropdown({" in toggle, \
        "reasoning toggle must invoke the shared renderer with opts"


def test_diagnostics_handler_block_removed():
    """No `[data-ops-action=\"diagnostics\"]` element is rendered anywhere on
    the v3 profile screen, so the bindings block that targeted it is dead
    code. Removed in the 2026-05-15 follow-up."""
    assert 'data-ops-action="diagnostics"' not in PANELS_JS, \
        "diagnostics querySelector is dead code — no element renders this attribute on the v3 screen"


def test_description_editor_full_width_when_active():
    """While the inline editor is active, the description host promotes to a
    block so the textarea + counter/actions row reclaim the full width of the
    hero body (otherwise the row's flex track shares space with the now-
    redundant pencil button and clips the editor at narrow viewports)."""
    # Find the rule for the active-editing state.
    assert re.search(
        r"\.profile-hero-description-row:has\(\s*\.profile-hero-description\[data-editing=\"1\"\]\s*\)\s*\{[^}]*display\s*:\s*block",
        STYLE_CSS,
    ), "row must switch to block layout while the editor is active"
    # The pencil must be hidden while editing (it's redundant — the editor is open).
    assert re.search(
        r"\.profile-hero-description-row:has\(\s*\.profile-hero-description\[data-editing=\"1\"\]\s*\)\s*\.profile-hero-description-edit\s*\{[^}]*display\s*:\s*none",
        STYLE_CSS,
    ), "pencil must be hidden while editing"
