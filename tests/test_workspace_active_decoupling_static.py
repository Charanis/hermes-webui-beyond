"""Static contracts for separating chat Space selection from active/default Space."""

import re
from pathlib import Path


ROOT = Path(__file__).parent.parent.resolve()
PANELS_JS = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")
ROUTES_PY = (ROOT / "api" / "routes.py").read_text(encoding="utf-8")


def _extract_function(src: str, name: str) -> str:
    m = re.search(rf"(?:async\s+)?function {re.escape(name)}\s*\([^)]*\)\s*\{{", src)
    assert m, f"function {name} not found"
    i, depth = m.end(), 1
    while i < len(src) and depth > 0:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return src[m.start():i]


def test_chat_workspace_switch_does_not_make_space_active():
    fn = _extract_function(PANELS_JS, "switchToWorkspace")
    assert "activateWorkspace" in fn, (
        "switchToWorkspace must distinguish chat-scoped Space changes from explicit activation"
    )
    assert "S._profileDefaultWorkspace=path" not in fn and "S._profileDefaultWorkspace = path" not in fn, (
        "chat-scoped Space changes must not overwrite the active/default Space"
    )
    assert "activate_workspace" in fn, (
        "session workspace updates must tell the backend whether this is explicit activation"
    )


def test_spaces_panel_activation_is_explicit():
    fn = _extract_function(PANELS_JS, "activateCurrentWorkspace")
    assert "activateWorkspace(" in fn, (
        "Spaces panel activation should use the explicit active/default Space path"
    )
    activate = _extract_function(PANELS_JS, "activateWorkspace")
    assert "/api/workspaces/activate" in activate
    assert "S._profileDefaultWorkspace=path" in activate or "S._profileDefaultWorkspace = path" in activate


def test_spaces_panel_active_badge_uses_default_space_not_open_chat_space():
    for name in ("renderWorkspacesPanel", "_renderWorkspaceDetail", "_setWorkspaceHeaderButtons"):
        fn = _extract_function(PANELS_JS, name)
        assert "S._profileDefaultWorkspace" in fn, (
            f"{name} must key ACTIVE state from the active/default Space, not S.session.workspace"
        )
        assert "const activePath = S.session ? S.session.workspace : '';" not in fn


def test_backend_session_update_does_not_activate_workspace_by_default():
    idx = ROUTES_PY.find('if parsed.path == "/api/session/update"')
    assert idx != -1, "session update route not found"
    block = ROUTES_PY[idx:idx + 1800]
    assert "activate_workspace" in block
    assert "body.get(\"activate_workspace\") is True" in block
    assert "set_last_workspace(new_ws)" not in block, (
        "session/update must not unconditionally change the active/default Space"
    )


def test_backend_chat_start_does_not_activate_workspace_by_default():
    idx = ROUTES_PY.find("def _handle_chat_start")
    assert idx != -1, "chat start handler not found"
    block = ROUTES_PY[idx:idx + 5200]
    assert "activate_workspace" in block
    assert "body.get(\"activate_workspace\") is True" in block
    assert "activate_workspace=activate_workspace" in block

    idx = ROUTES_PY.find("def _start_chat_stream_for_session")
    assert idx != -1, "chat stream start helper not found"
    block = ROUTES_PY[idx:idx + 3600]
    assert "activate_workspace: bool = False" in block
    set_idx = block.find("set_last_workspace")
    assert set_idx != -1, "explicit activation path should still be available"
    nearby = block[max(0, set_idx - 180):set_idx + 180]
    assert "if activate_workspace:" in nearby, (
        "chat start must not unconditionally change the active/default Space"
    )


def test_backend_has_explicit_workspace_activate_route():
    assert 'parsed.path == "/api/workspaces/activate"' in ROUTES_PY
    idx = ROUTES_PY.find("def _handle_workspace_activate")
    assert idx != -1, "workspace activation handler not found"
    block = ROUTES_PY[idx:idx + 900]
    assert "set_last_workspace" in block
    assert "get_last_workspace" in block
