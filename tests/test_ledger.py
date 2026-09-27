from pathlib import Path

import pytest

from pasi.core.ledger import InvalidLedgerEntry, OperationLedgerEntry
from pasi.core.ledger_store import (
    DuplicateLedgerEntry,
    LedgerEntryNotFound,
    LineageConflict,
    SQLiteOperationLedger,
)


def entry(
    operation_id: str,
    *,
    parent: str = "",
    task: str = "task-1",
    run: str = "run-1",
) -> OperationLedgerEntry:
    return OperationLedgerEntry(
        operation_id=operation_id,
        task_id=task,
        run_id=run,
        parent_operation_id=parent,
        provider="ollama",
        branch="pasi/task",
        pr_number=12,
    )


def test_ledger_persists_lineage_and_restart(tmp_path: Path):
    path = tmp_path / "ledger.db"
    ledger = SQLiteOperationLedger(path)

    root = ledger.register(entry("op-1"))
    child = ledger.register(entry("op-2", parent="op-1"))
    grandchild = ledger.register(entry("op-3", parent="op-2"))

    assert ledger.lineage("op-3") == (root, child, grandchild)
    assert ledger.children("op-1") == (child,)
    assert [item.operation_id for item in ledger.list_task("task-1")] == [
        "op-1", "op-2", "op-3"
    ]

    restarted = SQLiteOperationLedger(path)
    assert restarted.get("op-2") == child
    assert [item.operation_id for item in restarted.list_run("run-1")] == [
        "op-1", "op-2", "op-3"
    ]


def test_ledger_rejects_duplicate_and_missing_parent(tmp_path: Path):
    ledger = SQLiteOperationLedger(tmp_path / "ledger.db")
    ledger.register(entry("op-1"))

    with pytest.raises(DuplicateLedgerEntry):
        ledger.register(entry("op-1"))

    with pytest.raises(LineageConflict, match="parent operation does not exist"):
        ledger.register(entry("op-2", parent="missing"))

    with pytest.raises(LedgerEntryNotFound):
        ledger.get("missing")


def test_ledger_prevents_cross_task_and_cross_run_lineage(tmp_path: Path):
    ledger = SQLiteOperationLedger(tmp_path / "ledger.db")
    ledger.register(entry("op-1"))

    with pytest.raises(LineageConflict, match="different task"):
        ledger.register(entry("op-2", parent="op-1", task="task-2"))

    with pytest.raises(LineageConflict, match="different run"):
        ledger.register(entry("op-3", parent="op-1", run="run-2"))


def test_ledger_entry_rejects_invalid_identity():
    with pytest.raises(InvalidLedgerEntry):
        OperationLedgerEntry(operation_id="", task_id="task", run_id="run")
