from pathlib import Path
import json
def test_engineering_workspace_uses_native_mv3_runtime():
    root=Path(__file__).resolve().parents[1]
    assert not (root/"automation"/"legacy").exists()
    manifest=root/"automation"/"chromium"/"pasi-chatgpt"/"manifest.json"
    assert manifest.is_file()
    data=json.loads(manifest.read_text(encoding="utf-8"))
    assert data["manifest_version"]==3
    assert data["name"]=="PASI ChatGPT Controller"
