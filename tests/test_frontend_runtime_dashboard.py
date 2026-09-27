from pathlib import Path


def test_frontend_runtime_dashboard_assets_exist_and_reference_real_runtime_contract():
    root = Path(__file__).resolve().parents[1]
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    js = (root / "web" / "app.js").read_text(encoding="utf-8")
    assert 'id="operation-id"' in html
    assert 'id="timeline"' in html
    assert 'data-action="recover"' in html
    assert "/v1/runtime/health" in js
    assert "/v1/runtime/operations/" in js
    assert "/v1/runtime/controls" in js


def test_dashboard_server_script_uses_loopback_runtime_api():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "serve_runtime_dashboard.py").read_text(encoding="utf-8")
    assert 'host="127.0.0.1"' in script
    assert "static_root=WEB_ROOT" in script
