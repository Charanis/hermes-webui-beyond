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
        "_hydrateProfileRuntimeChips",
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


# ── Runtime panel ─────────────────────────────────────────────────────────


def test_runtime_panel_has_three_composer_chips():
    fn = _extract_function(PANELS_JS, "_profileRuntimePanel")
    assert "profileRuntimeProviderChip" in fn, "Provider chip missing"
    assert "profileRuntimeModelChip" in fn, "Model chip missing"
    assert "profileRuntimeReasoningChip" in fn, "Reasoning chip missing"
    # All three use the chat composer's chip class.
    chip_class_count = fn.count("composer-model-chip")
    assert chip_class_count >= 3, \
        f"expected at least 3 .composer-model-chip uses in runtime panel, got {chip_class_count}"


def test_provider_chip_reuses_composer_dropdown_styling():
    fn = _extract_function(PANELS_JS, "_profileRuntimePanel")
    assert "model-dropdown profile-runtime-dropdown" in fn, \
        "Runtime dropdown must reuse .model-dropdown chrome from the chat composer"


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
