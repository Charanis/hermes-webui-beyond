"""Static frontend checks for the profile-scoped Gateway Tile contract."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANELS_JS = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")


def _extract_function(src: str, name: str) -> str:
    m = re.search(rf"function {re.escape(name)}\s*\([^)]*\)\s*\{{", src)
    assert m, f"function {name} not found"
    i, depth = m.end(), 1
    while i < len(src) and depth > 0:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    assert depth == 0, f"function {name} did not parse cleanly"
    return src[m.start():i]


def test_gateway_tile_knows_unknown_and_unavailable_are_not_stopped():
    label = _extract_function(PANELS_JS, "_gatewayLabelForPhase")
    toggle = _extract_function(PANELS_JS, "_gatewayToggleLabelForPhase")
    assert "case 'unknown': return 'Unknown';" in label
    assert "case 'unavailable': return 'Unavailable';" in label
    assert "Gateway: Check status" in toggle
    assert "Gateway: Unavailable" in toggle


def test_gateway_status_contract_fields_are_cached_and_repainted():
    refresh = _extract_function(PANELS_JS, "_refreshGatewayStatus")
    repaint = _extract_function(PANELS_JS, "_repaintGatewayTile")
    for field in ("control_available", "status_source", "health", "detail", "desired_enabled"):
        assert field in refresh, f"_refreshGatewayStatus must cache {field} from backend contract"
    assert "state.control_available === false" in repaint
    assert "disabled" in repaint


def test_gateway_detail_render_starts_stable_visible_poller_after_immediate_refresh():
    bind = _extract_function(PANELS_JS, "_bindProfileOpsConsole")
    assert "_refreshGatewayStatus(profileName)" in bind
    # Stable polling must start for all visible phases, not only starting/stopping.
    assert "_startGatewayPoller(profileName)" in bind
    assert "st.phase === 'starting' || st.phase === 'stopping'" not in bind


def test_gateway_poller_uses_fast_transient_and_slow_stable_cadences_and_stops_when_hidden():
    poller = _extract_function(PANELS_JS, "_startGatewayPoller")
    assert "_GATEWAY_TRANSIENT_POLL_MS" in PANELS_JS, "transient states should poll about every 1.5s"
    assert "_GATEWAY_STABLE_POLL_MS" in PANELS_JS, "stable states should poll every 10-15s"
    assert "_GATEWAY_TRANSIENT_POLL_MS" in poller
    assert "_GATEWAY_STABLE_POLL_MS" in poller
    assert "document.visibilityState === 'hidden'" in poller
    assert "_currentPanel !== 'profiles'" in poller
    assert "_currentProfileDetail" in poller
    assert "setTimeout" in poller and "setInterval" not in poller


def test_gateway_poller_does_not_stop_after_stale_transient_recovers_to_stable_phase():
    poller = _extract_function(PANELS_JS, "_startGatewayPoller")
    assert "const result = await _refreshGatewayStatus(profileName);" in poller
    assert "const phase = (result && result.phase) || state.phase || 'stopped';" in poller
    assert "schedule(_GATEWAY_TRANSIENT_PHASES.has(phase) ? _GATEWAY_TRANSIENT_POLL_MS : _GATEWAY_STABLE_POLL_MS);" in poller
    assert "phase && phase !== 'starting' && phase !== 'stopping'" not in poller
    assert "_stopGatewayPoller(profileName);" in poller


def test_gateway_tile_renders_keyboard_info_button_and_copyable_dialog_path():
    tile = _extract_function(PANELS_JS, "_profileGatewayTile")
    bind = _extract_function(PANELS_JS, "_bindProfileOpsConsole")
    dialog = _extract_function(PANELS_JS, "_openGatewayInfoDialog")
    assert "profile-gateway-info" in tile
    assert "data-gateway-info" in tile
    assert "aria-label=\"View gateway status details\"" in tile
    assert "data-gateway-info" in bind
    assert "_openGatewayInfoDialog(profileName)" in bind
    for required in ("role=\"dialog\"", "Gateway status details", "Profile", "Phase", "Status source", "Health reason", "Copy", "Close", "textarea"):
        assert required in dialog
    assert "navigator.clipboard.writeText" in dialog


def test_gateway_toggle_never_restarts_and_respects_unavailable_control():
    toggle = _extract_function(PANELS_JS, "_onGatewayToggle")
    assert "restart" not in toggle
    assert "state.control_available === false" in toggle
    assert "phase === 'unavailable'" in toggle


def test_gateway_info_css_supports_button_tooltip_and_dialog():
    for selector in (
        ".profile-gateway-info",
        ".profile-gateway-info-tooltip",
        ".gateway-info-dialog",
        ".gateway-info-detail",
    ):
        assert selector in STYLE_CSS, f"missing CSS selector {selector}"
