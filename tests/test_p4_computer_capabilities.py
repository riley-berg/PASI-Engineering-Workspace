import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pasi.core.approvals import Approval, ApprovalError, SQLiteApprovalStore
from pasi.core.computer_adapters import (
    AdapterError,
    ApplicationLauncher,
    BrowserClick,
    BrowserFill,
    BrowserNavigation,
    FileReadResult,
    ScopedFileAdapter,
    SemanticBrowserAdapter,
    ProcessInvocation,
    TypedGitAdapter,
    VSCodeAdapter,
)
from pasi.core.computer_capabilities import (
    CapabilityDescriptor,
    CapabilityError,
    SQLiteCapabilityRegistry,
)
from pasi.core.computer_control import (
    ComputerControlError,
    ComputerControlService,
    HostRecoveryController,
    RecoveryAction,
)
from pasi.core.event_store import SQLiteEventStore
from pasi.core.recovery import RecoveryClassification, DeterministicRecoveryClassifier
from pasi.core.resource_observer import HostResourceObserver, SQLiteResourceObservationStore
from pasi.core.terminal import (
    SQLiteTerminalEvidenceStore,
    TerminalCapabilityError,
    TerminalCommand,
    TypedTerminalExecutor,
)
from pasi.core.events import DurableEvent


def capability(
    capability_id: str = "terminal.exec",
    *,
    side_effect: str = "read",
    authorization: str = "operator",
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id=capability_id,
        version=1,
        operation=capability_id.rsplit(".", 1)[-1],
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        authorization=authorization,
        side_effect=side_effect,
        resource_limits={"timeout_seconds": 30, "output_bytes": 256000},
        scope="workspace",
    )


def test_capability_registry_is_durable_conflict_safe_and_digestable(tmp_path: Path):
    store = SQLiteCapabilityRegistry(tmp_path / "capabilities.db")
    original = store.register(capability())
    assert store.register(original) == original
    assert store.get(original.capability_id) == original
    digest = store.digest()
    assert digest == store.digest()

    with pytest.raises(CapabilityError):
        store.register(
            CapabilityDescriptor(
                **{**original.to_dict(), "operation": "different"}
            )
        )

    with pytest.raises(CapabilityError):
        CapabilityDescriptor(
            capability_id="bad",
            version=1,
            operation="x",
            input_schema={},
            output_schema={},
            authorization="none",
            side_effect="write",
            resource_limits={"timeout": float("nan")},
            scope="workspace",
        )

    restarted = SQLiteCapabilityRegistry(tmp_path / "capabilities.db")
    assert restarted.digest() == digest


def test_typed_terminal_executes_real_bounded_subprocess_and_persists_evidence(tmp_path: Path):
    evidence = SQLiteTerminalEvidenceStore(tmp_path / "terminal.db")
    executor = TypedTerminalExecutor(
        workspace_root=tmp_path,
        executable_allowlist=(sys.executable,),
        evidence_store=evidence,
    )
    result = executor.execute(
        TerminalCommand(
            operation_id="op-terminal-1",
            argv=(sys.executable, "-c", "print('PASI_TERMINAL_OK')"),
            cwd=tmp_path,
        )
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "PASI_TERMINAL_OK"
    assert len(result.stdout_digest) == 64
    assert evidence.get("op-terminal-1").stdout_digest == result.stdout_digest

    with pytest.raises(TerminalCapabilityError):
        executor.execute(
            TerminalCommand(
                operation_id="op-escape",
                argv=(sys.executable, "-c", "print('bad')"),
                cwd=tmp_path / "..",
            )
        )

    with pytest.raises(TerminalCapabilityError):
        executor.execute(
            TerminalCommand(
                operation_id="op-other",
                argv=("sh", "-c", "echo bad"),
                cwd=tmp_path,
            )
        )


def test_typed_terminal_timeout_is_explicit(tmp_path: Path):
    evidence = SQLiteTerminalEvidenceStore(tmp_path / "terminal.db")
    executor = TypedTerminalExecutor(
        workspace_root=tmp_path,
        executable_allowlist=(sys.executable,),
        evidence_store=evidence,
    )
    result = executor.execute(
        TerminalCommand(
            operation_id="op-timeout",
            argv=(sys.executable, "-c", "import time; time.sleep(0.2)"),
            cwd=tmp_path,
            timeout_seconds=0.05,
        )
    )
    assert result.timed_out is True
    assert result.exit_code is None


class FakeProcess:
    def __init__(self):
        self.calls = []

    def run(self, invocation: ProcessInvocation) -> int:
        self.calls.append(invocation)
        return 0


class FakeBrowser:
    def __init__(self):
        self.calls = []

    def navigate(self, operation):
        self.calls.append(("navigate", operation))
        return {"ok": True}

    def click(self, operation):
        self.calls.append(("click", operation))
        return {"ok": True}

    def fill(self, operation):
        self.calls.append(("fill", operation))
        return {"ok": True}


def test_semantic_vscode_adapter_uses_typed_process_calls_and_scoped_paths(tmp_path: Path):
    process = FakeProcess()
    adapter = VSCodeAdapter(
        workspace_root=tmp_path,
        process=process,
        executable="code",
    )
    adapter.open_file(tmp_path / "src" / "main.py", line=12, column=4)
    assert process.calls[0].argv == (
        "code",
        "--reuse-window",
        str((tmp_path / "src" / "main.py").resolve()) + ":12:4",
    )

    with pytest.raises(AdapterError):
        adapter.open_file(tmp_path / ".." / "outside.py")


def test_semantic_browser_rejects_disallowed_transport_and_accepts_typed_targets():
    browser = FakeBrowser()
    adapter = SemanticBrowserAdapter(browser, allowed_hosts=("example.com",))
    adapter.navigate(BrowserNavigation("op-1", "https://example.com/a"))
    adapter.click(BrowserClick("op-1", "submit-button"))
    adapter.fill(BrowserFill("op-1", "name-input", "Riley"))
    assert [name for name, _ in browser.calls] == ["navigate", "click", "fill"]

    with pytest.raises(AdapterError):
        adapter.navigate(BrowserNavigation("op-1", "http://example.com/a"))

    with pytest.raises(AdapterError):
        adapter.navigate(BrowserNavigation("op-1", "https://chatgpt.com/c/unsafe-model-channel"))


def test_file_git_and_application_adapters_are_scoped():
    process = FakeProcess()
    with pytest.raises(AdapterError):
        ScopedFileAdapter(Path.cwd()).read(Path("/tmp/pasi-outside-file"))
    git = TypedGitAdapter(workspace_root=Path.cwd(), process=process)
    assert git.run("status") == 0
    with pytest.raises(AdapterError):
        git.run("push")

    launcher = ApplicationLauncher(("code",), process)
    assert launcher.launch("code", ("--version",), cwd=Path.cwd()) == 0
    with pytest.raises(AdapterError):
        launcher.launch("powershell", (), cwd=Path.cwd())


def test_approval_is_single_use_scoped_and_restart_safe(tmp_path: Path):
    path = tmp_path / "approvals.db"
    store = SQLiteApprovalStore(path)
    approval = Approval.issue(
        approval_id="approval-1",
        operation_id="op-1",
        capability_id="file.write",
        target="src/main.py",
        token="secret",
        ttl_seconds=60,
    )
    store.create(approval)
    consumed = store.consume(
        "approval-1",
        token="secret",
        operation_id="op-1",
        capability_id="file.write",
        target="src/main.py",
    )
    assert consumed.used is True

    restarted = SQLiteApprovalStore(path)
    with pytest.raises(ApprovalError):
        restarted.consume(
            "approval-1",
            token="secret",
            operation_id="op-1",
            capability_id="file.write",
            target="src/main.py",
        )

    expired = Approval(
        approval_id="approval-expired",
        operation_id="op-1",
        capability_id="file.write",
        target="src/main.py",
        issued_at=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
        expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        token_digest=__import__("hashlib").sha256(b"secret").hexdigest(),
    )
    restarted.create(expired)
    with pytest.raises(ApprovalError):
        restarted.consume(
            "approval-expired",
            token="secret",
            operation_id="op-1",
            capability_id="file.write",
            target="src/main.py",
        )


def test_control_service_requires_approval_and_emits_durable_event(tmp_path: Path):
    registry = SQLiteCapabilityRegistry(tmp_path / "capabilities.db")
    registry.register(capability("file.write", side_effect="write", authorization="approval"))
    approvals = SQLiteApprovalStore(tmp_path / "approvals.db")
    events = SQLiteEventStore(tmp_path / "events.db")
    service = ComputerControlService(
        registry=registry,
        approvals=approvals,
        events=events,
    )

    with pytest.raises(ComputerControlError):
        service.execute(
            operation_id="op-write",
            capability_id="file.write",
            target="src/main.py",
            action=lambda: {"written": True},
        )

    approval = Approval.issue(
        approval_id="approval-write",
        operation_id="op-write",
        capability_id="file.write",
        target="src/main.py",
        token="write-token",
        ttl_seconds=60,
    )
    approvals.create(approval)

    result = service.execute(
        operation_id="op-write",
        capability_id="file.write",
        target="src/main.py",
        action=lambda: {"written": True},
        approval_id="approval-write",
        approval_token="write-token",
    )
    assert result.approval_id == "approval-write"
    event = events.list(operation_id="op-write")[0]
    assert event.event_type == "computer.action.completed"
    assert event.payload["capability_id"] == "file.write"


def test_host_recovery_is_finite_and_preserves_operation_identity():
    controller = HostRecoveryController(DeterministicRecoveryClassifier())
    calls = []

    outcome = controller.recover(
        operation_id="op-recover",
        failure_code="connection_lost",
        failure_family="connection",
        retry_count=0,
        actions=(
            RecoveryAction("reconnect", lambda: calls.append("reconnect") or False),
            RecoveryAction("restart-hook", lambda: calls.append("restart") or True),
        ),
    )
    assert outcome.operation_id == "op-recover"
    assert outcome.classification is RecoveryClassification.RECOVERABLE
    assert outcome.succeeded_action == "restart-hook"
    assert calls == ["reconnect", "restart"]

    terminal = controller.recover(
        operation_id="op-terminal",
        failure_code="integrity-failure",
        failure_family="integrity",
        retry_count=0,
        actions=(RecoveryAction("should-not-run", lambda: True),),
    )
    assert terminal.classification is RecoveryClassification.TERMINAL
    assert terminal.actions_attempted == ()


def test_host_resource_observer_records_real_snapshot(tmp_path: Path):
    store = SQLiteResourceObservationStore(tmp_path / "resource.db")
    observer = HostResourceObserver(store)
    first = observer.sample(operation_id="op-resource", run_id="run-1")
    second = observer.sample(operation_id="op-resource", run_id="run-1")
    assert first.rss_bytes > 0
    assert second.cpu_seconds >= 0
    assert second.disk_free_bytes > 0
    assert store.list(operation_id="op-resource") == (first, second)


def test_p4_schemas_are_versioned():
    root = Path(__file__).resolve().parents[1] / "schemas"
    expected = (
        "computer-capability-v1.json",
        "terminal-command-v1.json",
        "browser-operation-v1.json",
        "approval-v1.json",
        "recovery-outcome-v1.json",
        "resource-snapshot-v1.json",
    )
    for name in expected:
        payload = json.loads((root / name).read_text(encoding="utf-8"))
        assert payload["$id"].endswith(name)
        assert payload["title"].startswith("PASI ")
