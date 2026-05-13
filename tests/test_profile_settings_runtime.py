"""Tests for selected-profile runtime settings — reasoning_effort persistence.

Plan reference: Phase 1A. Ensure that updating reasoning_effort for an inactive
named profile writes ONLY that profile's config.yaml and does not mutate the
active profile's config (the historic active-only `set_reasoning_effort`
remains in `api/config.py` and is not invoked here).
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
    return profiles


def _seed_profile(base: Path, name: str, config: dict | None = None) -> Path:
    profile_dir = base / "profiles" / name
    profile_dir.mkdir(parents=True, exist_ok=True)
    if config is not None:
        (profile_dir / "config.yaml").write_text(
            yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
        )
    return profile_dir


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
