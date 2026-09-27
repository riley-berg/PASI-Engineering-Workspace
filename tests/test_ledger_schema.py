import json
from pathlib import Path


def test_operation_ledger_schema_is_valid_and_versioned():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "operation-ledger-v1.json")
        .read_text(encoding="utf-8")
    )
    assert schema["$id"].endswith("operation-ledger-v1.json")
    assert "parent_operation_id" in schema["required"]
