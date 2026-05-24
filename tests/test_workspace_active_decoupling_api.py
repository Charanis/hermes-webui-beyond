"""API regressions for chat-scoped Space changes vs active/default Space."""

import json
import pathlib
import uuid
import urllib.error
import urllib.request

from tests._pytest_port import BASE


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read()), r.status


def _post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


def test_session_workspace_update_does_not_change_active_space(cleanup_test_sessions):
    workspaces, _ = _get("/api/workspaces")
    initial_last = workspaces["last"]
    root = pathlib.Path(initial_last)
    target = root / f"chat-scoped-space-{uuid.uuid4().hex[:8]}"
    target.mkdir(parents=True, exist_ok=True)
    sid = None
    try:
        added, status = _post(
            "/api/workspaces/add",
            {"path": str(target), "name": "Chat Scoped Space"},
        )
        assert status == 200, added

        created, status = _post("/api/session/new", {})
        assert status == 200, created
        sid = created["session"]["session_id"]
        cleanup_test_sessions.append(sid)

        updated, status = _post(
            "/api/session/update",
            {
                "session_id": sid,
                "workspace": str(target),
                "model": created["session"]["model"],
                "model_provider": created["session"].get("model_provider"),
            },
        )
        assert status == 200, updated
        assert updated["session"]["workspace"] == str(target)

        after_update, _ = _get("/api/workspaces")
        assert after_update["last"] == initial_last

        activated, status = _post("/api/workspaces/activate", {"path": str(target)})
        assert status == 200, activated
        assert activated["last"] == str(target)
        after_activate, _ = _get("/api/workspaces")
        assert after_activate["last"] == str(target)
    finally:
        if sid:
            _post("/api/session/delete", {"session_id": sid})
        _post("/api/workspaces/remove", {"path": str(target)})
