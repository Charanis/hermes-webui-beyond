"""Tests for profile_gateway_status_api — phase promotion logic.

The status endpoint is the only point that promotes 'starting' to
'running' or 'failed' based on PID liveness + grace window.
"""

import importlib
import json
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
    _saved = {n: sys.modules[n] for n in ("api.config", "api.profiles") if n in sys.modules}
    for n in ("api.config", "api.profiles"):
        if n in sys.modules:
            del sys.modules[n]
    profiles = importlib.import_module("api.profiles")
    sys.modules.update(_saved)
    return profiles


def _seed_named_profile(base: Path, name: str) -> Path:
    profile_dir = base / "profiles" / name
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir


def _write_state(profile_home: Path, **fields):
    (profile_home / ".gateway-state.json").write_text(json.dumps(fields), encoding="utf-8")


def _install_fake_pid_alive(profiles, *, alive_pids: set[int]):
    """Monkey-patch _is_pid_alive on the module so tests can simulate liveness."""
    profiles._is_pid_alive = lambda pid: pid in alive_pids


def _past_iso(seconds_ago: float) -> str:
    import datetime as _dt
    t = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=seconds_ago)
    return t.isoformat().replace("+00:00", "Z")


def test_status_invalid_name_raises_value_error():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profiles = _reload_profiles_module(base)
        with pytest.raises(ValueError):
            profiles.profile_gateway_status_api("BAD NAME!")


def test_status_unknown_profile_raises_filenotfound():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profiles = _reload_profiles_module(base)
        with pytest.raises(FileNotFoundError):
            profiles.profile_gateway_status_api("ghost")


def test_status_stopped_when_no_pid_and_no_phase():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile = _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        _install_fake_pid_alive(profiles, alive_pids=set())
        result = profiles.profile_gateway_status_api("coder")
        assert result["phase"] == "stopped"
        assert result["pid"] is None
        assert result["last_error"] is None


def test_status_starting_within_grace_window():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile = _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        _install_fake_pid_alive(profiles, alive_pids=set())
        _write_state(profile, phase="starting", phase_started_at=_past_iso(2))
        result = profiles.profile_gateway_status_api("coder")
        assert result["phase"] == "starting"


def test_status_promotes_starting_to_running_when_pid_alive():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile = _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        _install_fake_pid_alive(profiles, alive_pids={9999})
        (profile / "gateway.pid").write_text("9999", encoding="utf-8")
        _write_state(profile, phase="starting", phase_started_at=_past_iso(1))
        result = profiles.profile_gateway_status_api("coder")
        assert result["phase"] == "running"
        assert result["pid"] == 9999
        # Promotion is persisted.
        persisted = json.loads((profile / ".gateway-state.json").read_text())
        assert persisted["phase"] == "running"


def test_status_promotes_starting_to_failed_after_grace_with_dead_pid():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile = _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        _install_fake_pid_alive(profiles, alive_pids=set())
        (profile / "gateway.pid").write_text("9999", encoding="utf-8")
        (profile / ".gateway-stderr.log").write_text(
            "telegram: connect refused\ntoken invalid\n", encoding="utf-8"
        )
        _write_state(profile, phase="starting", phase_started_at=_past_iso(10))
        result = profiles.profile_gateway_status_api("coder")
        assert result["phase"] == "failed"
        assert result["last_error"]
        assert "connect refused" in result["last_error"] or "token invalid" in result["last_error"]


def test_status_promotes_starting_to_failed_when_no_pid_file_after_grace():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile = _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        _install_fake_pid_alive(profiles, alive_pids=set())
        _write_state(profile, phase="starting", phase_started_at=_past_iso(10))
        result = profiles.profile_gateway_status_api("coder")
        assert result["phase"] == "failed"


def test_status_running_when_phase_running_and_pid_alive():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile = _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        _install_fake_pid_alive(profiles, alive_pids={1234})
        (profile / "gateway.pid").write_text("1234", encoding="utf-8")
        _write_state(profile, phase="running", phase_started_at=_past_iso(60))
        result = profiles.profile_gateway_status_api("coder")
        assert result["phase"] == "running"
        assert result["pid"] == 1234


def test_status_running_drops_to_stopped_when_pid_dies():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile = _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        _install_fake_pid_alive(profiles, alive_pids=set())
        (profile / "gateway.pid").write_text("1234", encoding="utf-8")
        _write_state(profile, phase="running", phase_started_at=_past_iso(60))
        result = profiles.profile_gateway_status_api("coder")
        assert result["phase"] == "stopped"
        persisted = json.loads((profile / ".gateway-state.json").read_text())
        assert persisted.get("phase") is None


def test_status_stopping_while_pid_alive():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile = _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        _install_fake_pid_alive(profiles, alive_pids={1234})
        (profile / "gateway.pid").write_text("1234", encoding="utf-8")
        _write_state(profile, phase="stopping", phase_started_at=_past_iso(2))
        result = profiles.profile_gateway_status_api("coder")
        assert result["phase"] == "stopping"


def test_status_stopping_promotes_to_stopped_when_pid_gone():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile = _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        _install_fake_pid_alive(profiles, alive_pids=set())
        _write_state(profile, phase="stopping", phase_started_at=_past_iso(1))
        result = profiles.profile_gateway_status_api("coder")
        assert result["phase"] == "stopped"


def test_status_failed_is_sticky():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile = _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        _install_fake_pid_alive(profiles, alive_pids=set())
        _write_state(
            profile,
            phase="failed",
            phase_started_at=_past_iso(30),
            last_error="bad token",
        )
        result = profiles.profile_gateway_status_api("coder")
        assert result["phase"] == "failed"
        assert result["last_error"] == "bad token"


def test_status_redacts_secrets_in_last_error():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile = _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        _install_fake_pid_alive(profiles, alive_pids=set())
        (profile / ".gateway-stderr.log").write_text(
            "TELEGRAM_BOT_TOKEN=abc123secret\nfailed\n", encoding="utf-8"
        )
        _write_state(profile, phase="starting", phase_started_at=_past_iso(10))
        result = profiles.profile_gateway_status_api("coder")
        assert result["phase"] == "failed"
        assert "abc123secret" not in (result["last_error"] or "")
        assert "[redacted]" in (result["last_error"] or "").lower() or "redacted" in (result["last_error"] or "")


def test_status_last_error_captures_tail_not_head_of_stderr():
    """When stderr log has stale noise at the front (e.g., a previous
    failed run's box-drawing) and the actual failure cause at the end
    (e.g., 'No messaging platforms enabled'), last_error must reflect
    the tail so the UI tooltip shows the meaningful diagnostic — not
    the box-drawing prefix that happened to land at offset 0 of the
    last-5KB read."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile = _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        _install_fake_pid_alive(profiles, alive_pids=set())
        head_noise = "X" * 1200  # simulate stale prior-run output
        tail_signal = "WARNING gateway.run: No messaging platforms enabled.\n"
        (profile / ".gateway-stderr.log").write_text(
            head_noise + "\n" + tail_signal, encoding="utf-8"
        )
        _write_state(profile, phase="starting", phase_started_at=_past_iso(10))
        result = profiles.profile_gateway_status_api("coder")
        assert result["phase"] == "failed"
        assert "No messaging platforms enabled" in (result["last_error"] or "")
        # The head noise should be truncated away by the tail slice.
        assert "X" * 1000 not in (result["last_error"] or "")


def test_status_synthesizes_running_when_no_phase_but_pid_alive():
    """Orphaned-process recovery: PID file exists, process alive, but state
    file is empty. The status API should synthesize a 'running' state."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile = _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        _install_fake_pid_alive(profiles, alive_pids={4242})
        (profile / "gateway.pid").write_text("4242", encoding="utf-8")
        # No .gateway-state.json — clean recovery scenario.
        result = profiles.profile_gateway_status_api("coder")
        assert result["phase"] == "running"
        assert result["pid"] == 4242
        # Synthesized phase is persisted.
        persisted = json.loads((profile / ".gateway-state.json").read_text())
        assert persisted["phase"] == "running"
        assert isinstance(persisted["phase_started_at"], str)


def test_status_promotion_preserves_phase_started_at_across_polls():
    """After 'starting' -> 'running' promotion, the original start
    timestamp must survive a second poll (regression: do not stamp a
    fresh timestamp on every read)."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / ".hermes"
        (base / "profiles").mkdir(parents=True)
        profile = _seed_named_profile(base, "coder")
        profiles = _reload_profiles_module(base)
        _install_fake_pid_alive(profiles, alive_pids={5555})
        (profile / "gateway.pid").write_text("5555", encoding="utf-8")
        original_started = _past_iso(3)
        _write_state(profile, phase="starting", phase_started_at=original_started)

        first = profiles.profile_gateway_status_api("coder")
        assert first["phase"] == "running"
        assert first["phase_started_at"] == original_started

        # Second poll must read the same (preserved) timestamp.
        second = profiles.profile_gateway_status_api("coder")
        assert second["phase"] == "running"
        assert second["phase_started_at"] == original_started
