"""Tests for profile-scoped gateway control endpoint.

Plan reference: Phase 1C. The endpoint must:
  * validate action in {start, restart, stop}
  * call a profile-scoped runner with the action and profile name
  * return a structured unavailable response when the backend is missing
  * never leak secret-looking strings from runner output
"""

import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _reload_profiles_module(base_home: Path):
    os.environ["HERMES_BASE_HOME"] = str(base_home)
    os.environ["HERMES_HOME"] = str(base_home)

    _saved = {name: sys.modules[name] for name in ["api.config", "api.profiles"]
              if name in sys.modules}
    for name in ["api.config", "api.profiles"]:
        if name in sys.modules:
            del sys.modules[name]

    profiles = importlib.import_module("api.profiles")
    sys.modules.update(_saved)
    return profiles


def _seed_named_profile(base: Path, name: str) -> Path:
    profile_dir = base / "profiles" / name
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir


def test_invalid_action_rejected():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        with pytest.raises(ValueError):
            profiles.profile_gateway_control_api("coder", "burninate")


def test_unknown_profile_404():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profiles = _reload_profiles_module(base)
        with pytest.raises(FileNotFoundError):
            profiles.profile_gateway_control_api("ghost", "start")


def test_hook_receives_action_and_profile():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        calls: list[tuple[str, str]] = []

        def fake_hook(name, action):
            calls.append((name, action))
            return {"ok": True, "running": action != "stop"}

        profiles._set_gateway_control_hook(fake_hook)
        try:
            result = profiles.profile_gateway_control_api("coder", "start")
            assert result["ok"] is True
            assert result["profile"] == "coder"
            assert result["action"] == "start"
            assert calls == [("coder", "start")]
        finally:
            profiles._set_gateway_control_hook(None)


def test_hook_failure_is_sanitized_not_raised():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)

        def boom(name, action):
            raise RuntimeError("gateway exploded with api_key=SHHH-DONT-TELL")

        profiles._set_gateway_control_hook(boom)
        try:
            result = profiles.profile_gateway_control_api("coder", "stop")
        finally:
            profiles._set_gateway_control_hook(None)
        assert result["ok"] is False
        # Sanitized: secret token should not appear verbatim.
        assert "SHHH-DONT-TELL" not in result["message"]
        assert "api_key=[redacted]" in result["message"]


def test_unavailable_when_no_backend_and_no_hook():
    """Without a hook and without hermes_cli.gateway, the endpoint must degrade
    honestly with unavailable=True instead of pretending success."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        # Ensure no hook installed by other tests bleeds through.
        profiles._set_gateway_control_hook(None)
        # If hermes_cli.gateway happens to be importable in this env, skip the
        # honest-degradation assertion — the test is then tautological.
        try:
            import hermes_cli.gateway  # type: ignore  # noqa: F401
        except ImportError:
            result = profiles.profile_gateway_control_api("coder", "start")
            assert result["ok"] is False
            assert result.get("unavailable") is True
            assert "not available" in result["message"].lower()
