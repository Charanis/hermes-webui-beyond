"""Tests for selected-profile runtime settings — reasoning_effort persistence.

Plan reference: Phase 1A. Ensure that updating reasoning_effort for an inactive
named profile writes ONLY that profile's config.yaml and does not mutate the
active profile's config (the historic active-only `set_reasoning_effort`
remains in `api/config.py` and is not invoked here).
"""

import importlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

yaml = pytest.importorskip("yaml")


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
    api_pkg = sys.modules.get("api")
    if api_pkg is not None:
        for attr in ("config", "profiles"):
            module_name = f"api.{attr}"
            if module_name in _saved:
                setattr(api_pkg, attr, _saved[module_name])
            elif hasattr(api_pkg, attr):
                delattr(api_pkg, attr)
    return profiles


def _seed_profile(base: Path, name: str, config: dict | None = None) -> Path:
    profile_dir = base / "profiles" / name
    profile_dir.mkdir(parents=True, exist_ok=True)
    if config is not None:
        (profile_dir / "config.yaml").write_text(
            yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
        )
    return profile_dir


def _write_profile_state(profile_dir: Path, state: dict) -> None:
    state_path = profile_dir / "webui_state" / "profile_settings.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")


class _FakeHandler:
    def __init__(self, body: dict):
        body_bytes = json.dumps(body).encode("utf-8")
        self.status = None
        self.sent_headers = []
        self.body = bytearray()
        self.wfile = self
        self.rfile = io.BytesIO(body_bytes)
        self.headers = {"Content-Length": str(len(body_bytes))}
        self.request = None

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.sent_headers.append((name, value))

    def end_headers(self):
        pass

    def write(self, data):
        self.body.extend(data)

    def json_body(self):
        return json.loads(bytes(self.body).decode("utf-8"))


def test_reasoning_effort_persists_for_named_profile():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile_dir = _seed_profile(base, "coder", {"model": {"default": "gpt-5.5"}})
        profiles = _reload_profiles_module(base)

        result = profiles.update_profile_settings_api("coder", reasoning_effort="medium")
        assert result["reasoning_effort"] == "medium"

        cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
        assert cfg["agent"]["reasoning_effort"] == "medium"
        # Model section preserved.
        assert cfg["model"]["default"] == "gpt-5.5"


def test_reasoning_effort_empty_string_clears_override():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile_dir = _seed_profile(
            base, "coder",
            {"agent": {"reasoning_effort": "high", "other": "keep"}},
        )
        profiles = _reload_profiles_module(base)

        result = profiles.update_profile_settings_api("coder", reasoning_effort="")
        assert result["reasoning_effort"] == ""

        cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
        # 'other' agent key remains; reasoning_effort is removed.
        assert "reasoning_effort" not in cfg["agent"]
        assert cfg["agent"]["other"] == "keep"


def test_reasoning_effort_accepts_none_literal():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile_dir = _seed_profile(base, "coder", {})
        profiles = _reload_profiles_module(base)

        result = profiles.update_profile_settings_api("coder", reasoning_effort="none")
        assert result["reasoning_effort"] == "none"
        cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
        assert cfg["agent"]["reasoning_effort"] == "none"


def test_invalid_reasoning_effort_rejected():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        _seed_profile(base, "coder", {})
        profiles = _reload_profiles_module(base)

        with pytest.raises(ValueError):
            profiles.update_profile_settings_api("coder", reasoning_effort="ultra")


def test_named_profile_settings_does_not_touch_default_config():
    """Updating reasoning on a named profile must not mutate the root profile."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        # Root profile config — should remain unchanged.
        (base / "config.yaml").write_text(
            yaml.safe_dump({"agent": {"reasoning_effort": "low"}}, sort_keys=False),
            encoding="utf-8",
        )
        profile_dir = _seed_profile(base, "coder", {})
        profiles = _reload_profiles_module(base)

        profiles.update_profile_settings_api("coder", reasoning_effort="high")
        root_cfg = yaml.safe_load((base / "config.yaml").read_text(encoding="utf-8"))
        assert root_cfg["agent"]["reasoning_effort"] == "low"

        coder_cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
        assert coder_cfg["agent"]["reasoning_effort"] == "high"


def test_get_profile_settings_returns_reasoning_effort():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        _seed_profile(
            base, "coder",
            {"agent": {"reasoning_effort": "minimal"}, "model": {"default": "x"}},
        )
        profiles = _reload_profiles_module(base)

        settings = profiles.get_profile_settings_api("coder")
        assert settings["reasoning_effort"] == "minimal"
        assert settings["model"] == "x"


def test_profile_settings_can_omit_uploaded_avatar_payload_for_runtime_hydration():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile_dir = _seed_profile(base, "coder", {})
        data_url = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
        )
        _write_profile_state(profile_dir, {"avatar": {"type": "image", "value": data_url}})
        profiles = _reload_profiles_module(base)

        full = profiles.get_profile_settings_api("coder")
        lightweight = profiles.get_profile_settings_api("coder", include_avatar=False)

        assert full["avatar"] == {"type": "image", "value": data_url}
        assert lightweight["avatar"] is None


def test_uploaded_avatar_summary_is_lazy_route_and_image_route_decodes():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile_dir = _seed_profile(base, "coder", {})
        data_url = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
        )
        _write_profile_state(profile_dir, {"avatar": {"type": "image", "value": data_url}})
        profiles = _reload_profiles_module(base)

        summary = profiles.get_profile_avatar_summary_api("coder")
        payload, content_type, etag = profiles.read_profile_avatar_image_api("coder")

        assert summary["type"] == "asset"
        assert summary["value"].startswith("api/profile/avatar-image?name=coder&v=")
        assert data_url not in summary["value"]
        assert content_type == "image/png"
        assert payload.startswith(b"\x89PNG")
        assert len(etag) == 16


def test_profile_settings_default_compression_is_enabled():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        _seed_profile(base, "coder", {})
        profiles = _reload_profiles_module(base)

        settings = profiles.get_profile_settings_api("coder")

        assert settings["compression"]["enabled"] is True
        assert settings["compression"]["threshold"] == 0.5
        assert settings["compression"]["protect_last_n"] == 20


def test_runtime_settings_round_trip_to_profile_config():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile_dir = _seed_profile(base, "coder", {"model": {"default": "gpt-5.5"}})
        profiles = _reload_profiles_module(base)

        result = profiles.update_profile_settings_api(
            "coder",
            fallback_model={
                "provider": "google",
                "model": "google/gemini-2.5-flash",
            },
            response_mode="technical",
            compression={
                "enabled": True,
                "threshold": 0.5,
                "protect_last_n": 20,
            },
            max_turns=150,
        )

        assert result["fallback_model"] == {
            "provider": "google",
            "model": "google/gemini-2.5-flash",
        }
        assert result["response_mode"] == "technical"
        assert result["compression"]["enabled"] is True
        assert result["compression"]["threshold"] == 0.5
        assert result["compression"]["protect_last_n"] == 20
        assert result["max_turns"] == 150

        cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
        assert cfg["fallback_providers"] == [
            {"provider": "google", "model": "google/gemini-2.5-flash"}
        ]
        assert cfg["agent"]["personality"] == "technical"
        assert cfg["compression"]["enabled"] is True
        assert cfg["compression"]["threshold"] == 0.5
        assert cfg["compression"]["protect_last_n"] == 20
        assert cfg["agent"]["max_turns"] == 150
        assert cfg["model"]["default"] == "gpt-5.5"


def test_compression_update_cannot_disable_profile_compression():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile_dir = _seed_profile(
            base,
            "coder",
            {"compression": {"enabled": False, "threshold": 0.25, "protect_last_n": 5}},
        )
        profiles = _reload_profiles_module(base)

        result = profiles.update_profile_settings_api(
            "coder",
            compression={"enabled": False, "threshold": 0.95, "protect_last_n": 40},
        )

        assert result["compression"]["enabled"] is True
        assert result["compression"]["threshold"] == 0.95
        assert result["compression"]["protect_last_n"] == 40

        cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
        assert cfg["compression"]["enabled"] is True
        assert cfg["compression"]["threshold"] == 0.95
        assert cfg["compression"]["protect_last_n"] == 40


def test_extended_runtime_settings_round_trip_to_profile_config():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile_dir = _seed_profile(
            base,
            "coder",
            {
                "model": {"default": "gpt-5.5"},
                "auxiliary": {
                    "vision": {
                        "provider": "openai",
                        "model": "openai/gpt-5.4-mini",
                        "timeout": 45,
                    },
                    "compression": {
                        "provider": "anthropic",
                        "model": "anthropic/claude-haiku-4-5",
                        "extra_body": {"temperature": 0.1},
                    },
                },
                "platform_toolsets": {
                    "cli": ["terminal", "file", "web"],
                    "telegram": ["web"],
                },
                "terminal": {"cwd": "/keep/terminal"},
            },
        )
        profiles = _reload_profiles_module(base)

        result = profiles.update_profile_settings_api(
            "coder",
            auxiliary_models=[
                {
                    "task": "vision",
                    "provider": "google",
                    "model": "google/gemini-2.5-flash",
                },
                {"task": "compression", "provider": "", "model": ""},
            ],
            toolsets=["terminal", "file", "web", "terminal"],
            default_workspace="/workspace/coder",
        )

        vision = next(item for item in result["auxiliary_models"] if item["task"] == "vision")
        compression = next(
            item for item in result["auxiliary_models"] if item["task"] == "compression"
        )
        assert vision["provider"] == "google"
        assert vision["model"] == "google/gemini-2.5-flash"
        assert compression["provider"] == ""
        assert compression["model"] == ""
        assert result["toolsets"] == ["terminal", "file", "web"]
        assert result["default_workspace"] == "/workspace/coder"

        cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
        assert cfg["model"]["default"] == "gpt-5.5"
        assert cfg["auxiliary"]["vision"]["provider"] == "google"
        assert cfg["auxiliary"]["vision"]["model"] == "google/gemini-2.5-flash"
        assert cfg["auxiliary"]["vision"]["timeout"] == 45
        assert "provider" not in cfg["auxiliary"]["compression"]
        assert "model" not in cfg["auxiliary"]["compression"]
        assert cfg["auxiliary"]["compression"]["extra_body"] == {"temperature": 0.1}
        assert cfg["platform_toolsets"]["cli"] == ["terminal", "file", "web"]
        assert cfg["platform_toolsets"]["telegram"] == ["web"]
        assert cfg["workspace"] == "/workspace/coder"
        assert cfg["terminal"]["cwd"] == "/keep/terminal"


def test_extended_runtime_settings_accept_auxiliary_model_dict_and_clear_workspace():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile_dir = _seed_profile(
            base,
            "coder",
            {
                "model": {"default": "gpt-5.5"},
                "default_workspace": "/old/default",
                "auxiliary": {
                    "title_generation": {
                        "provider": "openai",
                        "model": "openai/gpt-5.4-mini",
                        "base_url": "https://example.test/v1",
                    },
                },
            },
        )
        profiles = _reload_profiles_module(base)

        result = profiles.update_profile_settings_api(
            "coder",
            auxiliary_models={
                "title_generation": {
                    "provider": "google",
                    "model": "google/gemini-2.5-flash",
                },
                "curator": {"provider": "", "model": ""},
            },
            default_workspace="",
        )

        title = next(
            item for item in result["auxiliary_models"]
            if item["task"] == "title_generation"
        )
        assert title["provider"] == "google"
        assert title["model"] == "google/gemini-2.5-flash"
        assert result["default_workspace"] == ""

        cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
        assert cfg["auxiliary"]["title_generation"]["provider"] == "google"
        assert cfg["auxiliary"]["title_generation"]["model"] == "google/gemini-2.5-flash"
        assert cfg["auxiliary"]["title_generation"]["base_url"] == "https://example.test/v1"
        assert "workspace" not in cfg
        assert "default_workspace" not in cfg


def test_profile_settings_post_passes_extended_runtime_fields(monkeypatch):
    from api import routes

    profiles_module = sys.modules.get("api.profiles")
    if profiles_module is None:
        profiles_module = importlib.import_module("api.profiles")

    captured = {}

    def fake_update(name, **updates):
        captured["name"] = name
        captured["updates"] = updates
        return {"name": name, **updates}

    monkeypatch.setattr(profiles_module, "update_profile_settings_api", fake_update)

    body = {
        "name": "coder",
        "fallback_model": {"provider": "google", "model": "google/gemini-2.5-flash"},
        "response_mode": "technical",
        "compression": {"enabled": True, "threshold": 0.5},
        "max_turns": 150,
        "auxiliary_models": {
            "vision": {"provider": "google", "model": "google/gemini-2.5-flash"}
        },
        "toolsets": ["terminal", "file"],
        "default_workspace": "/workspace/coder",
        "ignored": "nope",
    }
    handler = _FakeHandler(body)

    routes.handle_post(handler, urlparse("http://example.test/api/profile/settings"))
    assert handler.status == 200
    assert captured == {
        "name": "coder",
        "updates": {
            "fallback_model": body["fallback_model"],
            "response_mode": "technical",
            "compression": body["compression"],
            "max_turns": 150,
            "auxiliary_models": body["auxiliary_models"],
            "toolsets": ["terminal", "file"],
            "default_workspace": "/workspace/coder",
        },
    }


def test_empty_fallback_model_clears_profile_fallback_providers():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile_dir = _seed_profile(
            base,
            "coder",
            {
                "fallback_providers": [
                    {"provider": "google", "model": "google/gemini-2.5-flash"}
                ],
                "model": {"default": "gpt-5.5"},
            },
        )
        profiles = _reload_profiles_module(base)

        result = profiles.update_profile_settings_api("coder", fallback_model={})

        assert result["fallback_model"] == {}
        cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
        assert "fallback_providers" not in cfg
        assert cfg["model"]["default"] == "gpt-5.5"


def test_invalid_response_mode_rejected():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        _seed_profile(base, "coder", {})
        profiles = _reload_profiles_module(base)

        with pytest.raises(ValueError):
            profiles.update_profile_settings_api("coder", response_mode="verbose")


@pytest.mark.parametrize("max_turns", [0, 1001])
def test_invalid_max_turns_rejected(max_turns):
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        _seed_profile(base, "coder", {})
        profiles = _reload_profiles_module(base)

        with pytest.raises(ValueError):
            profiles.update_profile_settings_api("coder", max_turns=max_turns)
