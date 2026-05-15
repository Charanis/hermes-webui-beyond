"""Static checks for the profile screen rework v3 (2026-05-14).

These grep the frontend source for structural contracts: the v3 helpers
exist, the v2 helpers are gone, the files grid uses Lucide icons rather
than single-letter badges, the gateway tile has the wifi indicator, and
the runtime panel exposes the provider chip. A regression that silently
deletes one of these signals is caught here without needing a browser.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()
PANELS_JS = (REPO_ROOT / "static" / "panels.js").read_text(encoding="utf-8")
STYLE_CSS = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")
ICONS_JS = (REPO_ROOT / "static" / "icons.js").read_text(encoding="utf-8")


def _extract_function(src: str, name: str) -> str:
    """Return the body of the named function (including signature)."""
    m = re.search(rf"function {re.escape(name)}\s*\([^)]*\)\s*\{{", src)
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
        ".profile-wifi.on",
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


# ── Default-model tile (rework 2026-05-15) ────────────────────────────────


def test_default_model_tile_title_and_subtitle():
    fn = _extract_function(PANELS_JS, "_profileRuntimePanel")
    # New tile title is "Default model" (descriptive), subtitle explains scope.
    assert ">Default model<" in fn, "tile title must be 'Default model'"
    assert "Used for new sessions in this profile" in fn, \
        "tile must surface the 'won't affect active conversations' subtitle"
    assert "profile-default-model-subtitle" in fn, \
        "subtitle must use the dedicated CSS class for muted styling"


def test_default_model_tile_has_two_composer_chips_only():
    """Drop from 3 chips (provider/model/reasoning) to 2 (model/reasoning).
    Provider is inferred from the chosen model — no separate provider chip."""
    fn = _extract_function(PANELS_JS, "_profileRuntimePanel")
    assert "profileDefaultModelChip" in fn, "Model chip missing"
    assert "profileDefaultReasoningChip" in fn, "Reasoning chip missing"
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


def test_default_model_tile_drops_apply_diagnostics_and_status_pill():
    """Auto-save flow — no Apply button, no Diagnostics, no status pill, no
    'Saved' diode. The tile is always saved by construction."""
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


def test_default_model_tile_includes_hidden_select_mirror():
    """The parameterised composer picker reads optgroups from a <select>
    mirror. The tile must include #profileDefaultModelSelect so
    renderModelDropdown({select: …}) has a catalog to walk."""
    fn = _extract_function(PANELS_JS, "_profileRuntimePanel")
    assert 'id="profileDefaultModelSelect"' in fn


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


# ── Gateway tile ──────────────────────────────────────────────────────────


def test_gateway_tile_uses_wifi_icon():
    fn = _extract_function(PANELS_JS, "_profileGatewayTile")
    assert "profile-wifi" in fn
    assert "li('wifi'" in fn or 'li("wifi"' in fn, "gateway tile must call li('wifi', …)"


def test_gateway_bindings_toggle_wifi_state():
    fn = _extract_function(PANELS_JS, "_bindProfileOpsConsole")
    assert "profileGatewayWifi" in fn, "_bindProfileOpsConsole must reach the wifi indicator"
    assert "just-started" in fn, "gateway start should trigger the pulse animation class"


# ── Skills tile ───────────────────────────────────────────────────────────


def test_skills_tile_has_top_chips_container():
    fn = _extract_function(PANELS_JS, "_profileSkillsTile")
    assert "opsSkillsTopChips" in fn
    assert "profile-skill-top" in fn


def test_skills_hydrator_renders_chips_and_more_overflow():
    fn = _extract_function(PANELS_JS, "_loadProfileSkillsTile")
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
    for action in ("rename", "duplicate", "remove", "diagnostics", "skills"):
        assert f'data-ops-action="{action}"' in fn, \
            f"binding for data-ops-action={action!r} is missing"
    for dropped in ("edit-soul", "open-activity"):
        assert f'data-ops-action="{dropped}"' not in fn, \
            f"v3.1 should drop the {dropped!r} action button"


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
