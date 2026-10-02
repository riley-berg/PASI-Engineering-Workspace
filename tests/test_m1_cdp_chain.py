from __future__ import annotations

from datetime import datetime, timezone

from scripts import pasi_m1_cdp_chain as chain


def test_m1_chain_is_fixed_at_twenty_operations() -> None:
    assert chain.TARGET_OPERATIONS == 20
    assert chain.EXECUTION_MODE == "supervised_m1"


def test_runner_state_preserves_supervised_mode_and_deadline(tmp_path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(chain, "STATE_DIR", state_dir)
    monkeypatch.setattr(chain, "BRIDGE_RUNTIME_DIR", runtime_dir)

    now = datetime.now(timezone.utc).isoformat()
    chain.write_runner_state({
        "run_id": "m1-test",
        "status": "running",
        "execution_mode": chain.EXECUTION_MODE,
        "started_at": now,
        "deadline_at": now,
        "target_operations": 20,
    })

    state = (state_dir / "state.json").read_text(encoding="utf-8")
    runtime_state = (runtime_dir / "state.json").read_text(encoding="utf-8")
    assert '"execution_mode": "supervised_m1"' in state
    assert '"target_operations": 20' in state
    assert '"execution_mode": "supervised_m1"' in runtime_state


def test_runner_pid_cleanup_removes_all_pid_locations(tmp_path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    runtime_dir = tmp_path / "runtime"
    legacy = tmp_path / "legacy" / "runner.pid"
    monkeypatch.setattr(chain, "STATE_DIR", state_dir)
    monkeypatch.setattr(chain, "BRIDGE_RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(chain, "LEGACY_PID_PATH", legacy)

    chain.write_runner_pid(12345)
    assert (state_dir / "runner.pid").exists()
    assert (runtime_dir / "runner.pid").exists()
    assert legacy.exists()

    chain.clear_runner_pid()
    assert not (state_dir / "runner.pid").exists()
    assert not (runtime_dir / "runner.pid").exists()
    assert not legacy.exists()
