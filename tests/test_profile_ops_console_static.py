"""Static checks for the Profile Ops Console redesign.

Plan reference: Phase 2C / 3C. These tests grep the static frontend assets to
enforce contracts that are easy to regress accidentally (e.g. overflow menu
silently grows a 4th item). They do not run JS — they only verify that the
markup helpers and CSS classes the design depends on exist in the source.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()
PANELS_JS = (REPO_ROOT / "static" / "panels.js").read_text(encoding="utf-8")
STYLE_CSS = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")


def test_ops_console_helpers_present():
    for helper in (
        "_profileIdentityPlane",
        "_profileOpsTiles",
        "_profileFilesSection",
        "_bindProfileOpsConsole",
        "startChatWithProfile",
    ):
        assert helper in PANELS_JS, f"missing helper {helper} in panels.js"


def test_identity_card_strings_present():
    # The mock explicitly uses the "Digital agent ID" kicker and "Profile ID: …
    # · local profile" subtitle. The plan says these are baked in.
    assert "Digital agent ID" in PANELS_JS
    assert "local profile" in PANELS_JS


def test_no_large_summary_beam():
    # The accepted mock removed the large top summary/action beam. We check
    # that the legacy "Profile Files" detail-card-title block was replaced.
    assert "profile-identity-card" not in PANELS_JS, (
        "Legacy identity card markup must be removed in favor of the Ops Console."
    )


def test_overflow_menu_exactly_three_items():
    # Find the overflow menu block and assert exactly three menu items in it.
    menu_marker = 'id="opsProfileMenu"'
    assert menu_marker in PANELS_JS
    start = PANELS_JS.index(menu_marker)
    # Look ahead a generous window for menu items.
    block = PANELS_JS[start:start + 1200]
    items = [v for v in ("rename", "duplicate", "remove") if f'data-ops-action="{v}"' in block]
    assert items == ["rename", "duplicate", "remove"], (
        f"Overflow menu must contain exactly Rename, Duplicate, Remove (found: {items})."
    )
    # Guard against silently adding additional menu items.
    assert block.count('data-ops-action="') == 3


def test_profile_files_grid_uses_five_widgets():
    assert "data-profile-file" in PANELS_JS
    # Confirm the five widget filenames are referenced.
    for fname in ("SOUL.md", "memories/MEMORY.md", "memories/USER.md", ".env", "config.yaml"):
        assert fname in PANELS_JS, f"profile file widget missing: {fname}"


def test_required_css_classes_present():
    for cls in (
        ".profile-ops-console",
        ".profile-id-card",
        ".profile-id-name",
        ".profile-avatar-preview",
        ".profile-avatar-corner-action",
        ".profile-overflow-menu",
        ".profile-ops-grid",
        ".profile-ops-tile",
        ".profile-ops-button",
        ".profile-runtime-controls",
        ".profile-ops-select",
        ".profile-ops-files-grid",
        ".profile-file-widget",
    ):
        assert cls in STYLE_CSS, f"missing CSS class {cls} in style.css"


def test_reduced_motion_rule_present():
    assert "prefers-reduced-motion" in STYLE_CSS
    # The avatar sheen animation must be respected.
    assert "profileOpsAvatarSheen" in STYLE_CSS


def test_sidebar_profile_rows_are_buttons():
    # The sidebar list now uses real <button> elements for keyboard semantics.
    # Look for the construction site and require button type.
    needle = "card = document.createElement('button')"
    assert needle in PANELS_JS, (
        "Sidebar profile rows must be <button type='button'> for keyboard accessibility."
    )


def test_start_chat_handler_switches_before_new_session():
    # startChatWithProfile must call switchToProfile *before* newSession for
    # inactive profiles, to avoid send/topbar split-brain.
    body_start = PANELS_JS.index("async function startChatWithProfile(")
    body = PANELS_JS[body_start:body_start + 1200]
    assert "switchToProfile" in body
    assert "newSession(true)" in body
    assert body.index("switchToProfile") < body.index("newSession(true)")


def test_make_active_only_when_inactive():
    # Make active button must be wired only on the inactive path.
    body_start = PANELS_JS.index("function _bindProfileOpsConsole(")
    body = PANELS_JS[body_start:body_start + 4000]
    assert "if (makeActive && !isActive)" in body, (
        "Make active button must be wired only when the profile is inactive."
    )
