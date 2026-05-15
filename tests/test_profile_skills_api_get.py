"""Tests for /api/profile/skills GET endpoint.

The endpoint returns the list of skills the agent has installed, each
annotated with whether it is enabled for the named profile.
"""

import importlib
import json as _json
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _reload_profiles(base_home: Path):
    os.environ["HERMES_BASE_HOME"] = str(base_home)
    os.environ["HERMES_HOME"] = str(base_home)
    for name in ["api.config", "api.profiles"]:
        if name in sys.modules:
            del sys.modules[name]
    return importlib.import_module("api.profiles")


def _seed_profile(base: Path, name: str) -> Path:
    profile_dir = base / "profiles" / name
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir


def _seed_skill(base: Path, skill_name: str) -> Path:
    skill_dir = base / "skills" / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: test skill\n---\nbody\n",
        encoding="utf-8",
    )
    return skill_dir


def test_unknown_profile_404():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profiles = _reload_profiles(base)
        with pytest.raises(FileNotFoundError):
            profiles.profile_skills_api("ghost")


def test_empty_skills_when_none_installed():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        _seed_profile(base, "coder")
        profiles = _reload_profiles(base)
        result = profiles.profile_skills_api("coder")
        assert result == {"profile": "coder", "skills": []}


def test_lists_installed_skills_disabled_by_default():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        _seed_profile(base, "coder")
        _seed_skill(base, "git-helper")
        _seed_skill(base, "test-runner")
        profiles = _reload_profiles(base)
        result = profiles.profile_skills_api("coder")
        names = [s["name"] for s in result["skills"]]
        assert names == ["git-helper", "test-runner"]
        assert all(s["enabled"] is False for s in result["skills"])


def test_enabled_set_persisted_per_profile():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        prof_dir = _seed_profile(base, "coder")
        _seed_skill(base, "git-helper")
        _seed_skill(base, "test-runner")
        (prof_dir / "skills.enabled.json").write_text(
            '["git-helper"]', encoding="utf-8"
        )
        profiles = _reload_profiles(base)
        result = profiles.profile_skills_api("coder")
        by_name = {s["name"]: s for s in result["skills"]}
        assert by_name["git-helper"]["enabled"] is True
        assert by_name["test-runner"]["enabled"] is False


def test_malformed_enabled_json_treated_as_empty():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        prof_dir = _seed_profile(base, "coder")
        _seed_skill(base, "git-helper")
        (prof_dir / "skills.enabled.json").write_text("not json", encoding="utf-8")
        profiles = _reload_profiles(base)
        result = profiles.profile_skills_api("coder")
        assert result["skills"] == [{"name": "git-helper", "label": "git-helper", "enabled": False}]
