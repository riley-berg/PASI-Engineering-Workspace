import json
from pathlib import Path


SCHEMAS = (
    "runtime-health-v1.json",
    "runtime-projection-v1.json",
    "runtime-control-request-v1.json",
    "runtime-control-result-v1.json",
)


def test_runtime_backend_schemas_are_versioned_and_json():
    root = Path(__file__).resolve().parents[1] / "schemas"
    for name in SCHEMAS:
        schema = json.loads((root / name).read_text(encoding="utf-8"))
        assert schema["$id"].endswith(name)
        assert schema["title"].startswith("PASI ")
