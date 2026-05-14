"""Tests for read_profile_persona — voice excerpt from SOUL.md.

Profile screen rework (2026-05-14): the persona endpoint exposes the first
non-blank paragraph of a profile's SOUL.md without leaking the full body, so
the hero dossier can render a voice quote.
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


def _seed_profile(base: Path, name: str) -> Path:
    pdir = base / "profiles" / name
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir


def _write_soul(profile_dir: Path, body: str) -> None:
    (profile_dir / "SOUL.md").write_text(body, encoding="utf-8")


def test_persona_missing_profile_raises_file_not_found():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profiles = _reload_profiles_module(base)
        with pytest.raises(FileNotFoundError):
            profiles.read_profile_persona_api("ghost")


def test_persona_invalid_name_raises_value_error():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profiles = _reload_profiles_module(base)
        with pytest.raises(ValueError):
            profiles.read_profile_persona_api("../escape")


def test_persona_empty_name_raises_value_error():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profiles = _reload_profiles_module(base)
        with pytest.raises(ValueError):
            profiles.read_profile_persona_api("")


def test_persona_soul_missing_returns_empty_voice():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        _seed_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        result = profiles.read_profile_persona_api("coder")
        assert result["name"] == "coder"
        assert result["soul_present"] is False
        assert result["soul_chars"] == 0
        assert result["voice"] == ""


def test_persona_soul_empty_file():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        pdir = _seed_profile(base, "coder")
        _write_soul(pdir, "")
        profiles = _reload_profiles_module(base)
        result = profiles.read_profile_persona_api("coder")
        assert result["soul_present"] is True
        assert result["soul_chars"] == 0
        assert result["voice"] == ""


def test_persona_returns_first_non_blank_paragraph():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        pdir = _seed_profile(base, "coder")
        _write_soul(
            pdir,
            "\n\n  \n# Heading\n\nA calm, terse engineering pair.\n\nSecond paragraph never shown.\n",
        )
        profiles = _reload_profiles_module(base)
        result = profiles.read_profile_persona_api("coder")
        assert result["voice"] == "A calm, terse engineering pair."


def test_persona_skips_heading_only_paragraphs_and_returns_body():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        pdir = _seed_profile(base, "coder")
        _write_soul(pdir, "# Heading\n\n## Subheading\n\nThe real voice line.")
        profiles = _reload_profiles_module(base)
        result = profiles.read_profile_persona_api("coder")
        assert result["voice"] == "The real voice line."


def test_persona_voice_truncated_to_240_chars_with_ellipsis():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        pdir = _seed_profile(base, "coder")
        long_para = "x" * 500
        _write_soul(pdir, long_para)
        profiles = _reload_profiles_module(base)
        result = profiles.read_profile_persona_api("coder")
        # 240 chars of body + a single ellipsis character
        assert len(result["voice"]) == 241
        assert result["voice"].endswith("…")
        assert result["voice"][:240] == "x" * 240


def test_persona_does_not_leak_full_soul_body():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        pdir = _seed_profile(base, "coder")
        _write_soul(pdir, "first para\n\nsecret-second-paragraph-with-credentials")
        profiles = _reload_profiles_module(base)
        result = profiles.read_profile_persona_api("coder")
        assert "secret-second-paragraph" not in result["voice"]
        # But soul_chars reports the full size so the UI can show edit affordances.
        assert result["soul_chars"] > len("first para")


def test_persona_supports_default_profile():
    """The default profile lives under HERMES_HOME directly, not under profiles/."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        base.mkdir(parents=True)
        (base / "profiles").mkdir(exist_ok=True)
        # Write SOUL.md at the root (where the default profile lives).
        (base / "SOUL.md").write_text("Default agent voice.", encoding="utf-8")
        profiles = _reload_profiles_module(base)
        result = profiles.read_profile_persona_api("default")
        assert result["name"] == "default"
        assert result["soul_present"] is True
        assert result["voice"] == "Default agent voice."
