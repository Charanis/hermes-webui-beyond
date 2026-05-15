"""Per-profile skill enable/disable API.

Covers ``list_profile_skills_api`` and ``toggle_profile_skill_api`` —
the WebUI surface that the Ops Console Skills tile + manager modal
calls. The agent's runtime reads the same ``skills.disabled`` key from
``config.yaml`` via ``get_disabled_skill_names``, so a toggle here is
naturally respected by the agent at runtime.
"""

from pathlib import Path

import pytest

# These tests work without the hermes-agent install — they exercise the
# WebUI-side helpers directly against on-disk profile homes.


def _write_skill(skills_dir: Path, category: str, name: str, description: str):
    """Create a SKILL.md file the WebUI scanner will pick up."""
    skill_dir = skills_dir / category / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody for {name}",
        encoding="utf-8",
    )


@pytest.fixture()
def profile_home_with_skills(tmp_path, monkeypatch):
    """Create a default profile home containing three skills.

    ``conftest.py`` pins ``HERMES_BASE_HOME`` to the test state dir for
    isolation; that takes priority over ``HERMES_HOME`` in
    ``_resolve_base_hermes_home`` (see api/profiles.py:64), so the
    fixture must override BOTH env vars before the module reload.
    """
    home = tmp_path / "hermes_home"
    (home / "skills").mkdir(parents=True)
    _write_skill(home / "skills", "research", "deep-dive", "Investigate things deeply.")
    _write_skill(home / "skills", "research", "summarize", "Summarize sources.")
    _write_skill(home / "skills", "ops", "deploy", "Run a deploy.")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BASE_HOME", str(home))
    import importlib
    import api.profiles as profiles_mod
    importlib.reload(profiles_mod)
    yield profiles_mod, home


def test_list_skills_marks_all_enabled_by_default(profile_home_with_skills):
    profiles_mod, home = profile_home_with_skills
    summary = profiles_mod.list_profile_skills_api("default")
    assert summary["ok"] is True
    assert summary["profile"] == "default"
    names = sorted(s["name"] for s in summary["skills"])
    assert names == ["deep-dive", "deploy", "summarize"]
    assert summary["total_count"] == 3
    assert summary["enabled_count"] == 3
    # Each skill should carry a category derived from its directory.
    by_name = {s["name"]: s for s in summary["skills"]}
    assert by_name["deep-dive"]["category"] == "research"
    assert by_name["deploy"]["category"] == "ops"
    assert all(s["enabled"] for s in summary["skills"])


def test_toggle_disable_then_re_enable_persists_in_config_yaml(profile_home_with_skills):
    profiles_mod, home = profile_home_with_skills

    # Disable one skill.
    result = profiles_mod.toggle_profile_skill_api("default", "deep-dive", False)
    assert result["changed"] is True
    assert result["enabled_count"] == 2
    by_name = {s["name"]: s for s in result["skills"]}
    assert by_name["deep-dive"]["enabled"] is False
    assert by_name["summarize"]["enabled"] is True

    # Config.yaml must hold the disabled list now.
    import yaml
    cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["skills"]["disabled"] == ["deep-dive"]

    # Re-enable: list must clean up the empty key from config.yaml.
    result2 = profiles_mod.toggle_profile_skill_api("default", "deep-dive", True)
    assert result2["changed"] is True
    assert result2["enabled_count"] == 3
    cfg2 = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}
    assert "skills" not in cfg2 or "disabled" not in cfg2.get("skills", {}), (
        "Empty disabled list should be removed from config.yaml to keep it tidy."
    )


def test_toggle_is_idempotent_when_state_already_matches(profile_home_with_skills):
    profiles_mod, _home = profile_home_with_skills
    # Disabling a skill that's already enabled returns changed=True (state flips).
    first = profiles_mod.toggle_profile_skill_api("default", "deploy", False)
    assert first["changed"] is True
    # Asking for the same target state again should be a no-op (changed=False).
    second = profiles_mod.toggle_profile_skill_api("default", "deploy", False)
    assert second["changed"] is False
    assert second["enabled_count"] == first["enabled_count"]


def test_toggle_rejects_invalid_skill_name(profile_home_with_skills):
    profiles_mod, _home = profile_home_with_skills
    with pytest.raises(ValueError):
        profiles_mod.toggle_profile_skill_api("default", "../escape", False)
    with pytest.raises(ValueError):
        profiles_mod.toggle_profile_skill_api("default", "", True)


def test_resolve_profile_skill_file_returns_existing_skill_md(profile_home_with_skills):
    profiles_mod, home = profile_home_with_skills
    p = profiles_mod.resolve_profile_skill_file("default", "summarize")
    assert p.name == "SKILL.md"
    assert "summarize" in str(p)


def test_resolve_profile_skill_file_raises_when_unknown(profile_home_with_skills):
    profiles_mod, _home = profile_home_with_skills
    with pytest.raises(FileNotFoundError):
        profiles_mod.resolve_profile_skill_file("default", "no-such-skill")


def test_skills_with_no_frontmatter_fall_back_to_dirname_and_first_line(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    skill_dir = home / "skills" / "misc" / "barebones"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# Heading first\n\nDescription line lives here.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BASE_HOME", str(home))
    import importlib
    import api.profiles as profiles_mod
    importlib.reload(profiles_mod)

    summary = profiles_mod.list_profile_skills_api("default")
    assert summary["total_count"] == 1
    row = summary["skills"][0]
    assert row["name"] == "barebones"
    assert row["description"] == "Description line lives here."
    assert row["enabled"] is True


def test_no_skills_directory_returns_empty_summary(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BASE_HOME", str(home))
    import importlib
    import api.profiles as profiles_mod
    importlib.reload(profiles_mod)

    summary = profiles_mod.list_profile_skills_api("default")
    assert summary["ok"] is True
    assert summary["skills"] == []
    assert summary["enabled_count"] == 0
    assert summary["total_count"] == 0


def test_set_disabled_replaces_full_list_in_one_write(profile_home_with_skills):
    """Batched Save: POST {disabled:[...]} replaces the whole set."""
    profiles_mod, home = profile_home_with_skills
    result = profiles_mod.set_profile_disabled_skills_api(
        "default", ["deep-dive", "deploy"]
    )
    assert result["changed"] is True
    assert result["enabled_count"] == 1
    by_name = {s["name"]: s for s in result["skills"]}
    assert by_name["deep-dive"]["enabled"] is False
    assert by_name["deploy"]["enabled"] is False
    assert by_name["summarize"]["enabled"] is True

    import yaml
    cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert sorted(cfg["skills"]["disabled"]) == ["deep-dive", "deploy"]


def test_set_disabled_empty_list_clears_the_key(profile_home_with_skills):
    profiles_mod, home = profile_home_with_skills
    # First disable two so the key exists.
    profiles_mod.set_profile_disabled_skills_api("default", ["deep-dive", "deploy"])
    # Now clear.
    result = profiles_mod.set_profile_disabled_skills_api("default", [])
    assert result["changed"] is True
    assert result["enabled_count"] == 3
    import yaml
    cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}
    assert "skills" not in cfg or "disabled" not in cfg.get("skills", {})


def test_set_disabled_is_idempotent_when_set_unchanged(profile_home_with_skills):
    profiles_mod, _home = profile_home_with_skills
    first = profiles_mod.set_profile_disabled_skills_api("default", ["deep-dive"])
    assert first["changed"] is True
    # Same set in different order → no change.
    again = profiles_mod.set_profile_disabled_skills_api("default", ["deep-dive"])
    assert again["changed"] is False
    assert again["enabled_count"] == first["enabled_count"]


def test_set_disabled_rejects_invalid_names(profile_home_with_skills):
    profiles_mod, _home = profile_home_with_skills
    with pytest.raises(ValueError):
        profiles_mod.set_profile_disabled_skills_api("default", ["../escape"])
    with pytest.raises(ValueError):
        profiles_mod.set_profile_disabled_skills_api("default", "not-a-list")


def test_skill_cache_is_invalidated_on_skill_md_write(profile_home_with_skills):
    """Editing a SKILL.md via the WebUI must show the new description next list."""
    profiles_mod, home = profile_home_with_skills
    # Prime the cache.
    summary = profiles_mod.list_profile_skills_api("default")
    by_name = {s["name"]: s for s in summary["skills"]}
    deep_dive_path = Path(by_name["deep-dive"]["path"])
    assert by_name["deep-dive"]["description"] == "Investigate things deeply."

    # Overwrite content (mtime of *containing dir* won't change, so the
    # cache must invalidate explicitly when we tell it to).
    deep_dive_path.write_text(
        "---\nname: deep-dive\ndescription: Brand new description.\n---\nbody",
        encoding="utf-8",
    )
    # Without invalidation, the cache would still serve the old row.
    profiles_mod._invalidate_skill_cache_for_path(deep_dive_path)

    again = profiles_mod.list_profile_skills_api("default")
    by_name_again = {s["name"]: s for s in again["skills"]}
    assert by_name_again["deep-dive"]["description"] == "Brand new description."


def test_skill_cache_is_shared_across_profile_calls(profile_home_with_skills, monkeypatch):
    """Two list calls on the same dir parse SKILL.md only once.

    Patches Path.read_text on SKILL.md files to count calls; the second
    list_profile_skills_api invocation must not re-read.
    """
    profiles_mod, _home = profile_home_with_skills
    # Prime once.
    profiles_mod.list_profile_skills_api("default")

    import pathlib
    original_read_text = pathlib.Path.read_text
    read_calls: list = []

    def _spy_read_text(self, *a, **kw):
        if self.name == "SKILL.md":
            read_calls.append(str(self))
        return original_read_text(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "read_text", _spy_read_text)
    profiles_mod.list_profile_skills_api("default")
    assert read_calls == [], (
        f"Expected zero SKILL.md reads on cache hit, got {len(read_calls)}: {read_calls}"
    )


def test_list_rejects_invalid_profile_name(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    (home / "skills").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BASE_HOME", str(home))
    import importlib
    import api.profiles as profiles_mod
    importlib.reload(profiles_mod)
    with pytest.raises(ValueError):
        profiles_mod.list_profile_skills_api("../escape")
    with pytest.raises(ValueError):
        profiles_mod.list_profile_skills_api("")


def test_list_raises_for_unknown_named_profile(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    (home / "profiles").mkdir(parents=True)
    (home / "skills").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BASE_HOME", str(home))
    import importlib
    import api.profiles as profiles_mod
    importlib.reload(profiles_mod)
    with pytest.raises(FileNotFoundError):
        profiles_mod.list_profile_skills_api("ghost-profile")
