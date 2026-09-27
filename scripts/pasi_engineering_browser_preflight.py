#!/usr/bin/env python3
from __future__ import annotations
import json,os,re,secrets,stat,subprocess,sys,time
from pathlib import Path
from urllib.request import Request,urlopen
def token_path():return Path(os.environ.get("PASI_BRIDGE_TOKEN_FILE",str(Path.home()/".pasi"/"bridge-token"))).expanduser()
def token():
    p=token_path();p.parent.mkdir(parents=True,exist_ok=True)
    if not p.exists():p.write_text(secrets.token_urlsafe(48)+"\n",encoding="utf-8");p.chmod(stat.S_IRUSR|stat.S_IWUSR)
    return p.read_text(encoding="utf-8").strip()
def get(path,t):
    with urlopen(Request("http://127.0.0.1:8765"+path,headers={"Authorization":f"Bearer {t}"}),timeout=3) as r:return json.loads(r.read(2000000).decode("utf-8"))
def healthy(t):
    try:return isinstance(get("/health",t),dict)
    except Exception:return False
def version(root):
    m=re.search(r"\bCONTROLLER_VERSION\s*=\s*['\"]([^'\"]+)['\"]",(root/"content.js").read_text(encoding="utf-8"));return m.group(1) if m else None
def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument("--extension-root",type=Path,required=True);a=p.parse_args()
    root=a.extension_root.expanduser().resolve();m=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    if m.get("manifest_version")!=3:raise SystemExit("MV3 extension required")
    t=token();runtime=Path(os.environ.get("PASI_ENGINEERING_RUNTIME_DIR",str(Path.home()/".pasi"/"engineering-workspace-168h"/"runtime"))).expanduser().resolve();runtime.mkdir(parents=True,exist_ok=True)
    if not healthy(t):
        log=(runtime/"bridge.log").open("a",encoding="utf-8");env={**os.environ,"PASI_BRIDGE_TOKEN":t,"PASI_RUNTIME_DIR":str(runtime)}
        pr=subprocess.Popen([sys.executable,"-m","automation.orchestrator.bridge"],cwd=Path(__file__).resolve().parents[1],env=env,stdout=log,stderr=log,start_new_session=True);(runtime/"bridge.pid").write_text(str(pr.pid)+"\n",encoding="utf-8");log.close()
    deadline=time.monotonic()+20
    while time.monotonic()<deadline and not healthy(t):time.sleep(.5)
    if not healthy(t):raise SystemExit(f"bridge did not become healthy: {runtime/'bridge.log'}")
    (root/".bridge-token").write_text(t+"\n",encoding="utf-8")
    obs=get("/browser/health",t);o=obs.get("observation",{});d=o.get("data",{}) if isinstance(o,dict) else {};expected=version(root)
    if d.get("kind") not in {"chatgpt_health","chatgpt_state"}:raise SystemExit("browser controller health unavailable")
    if expected and d.get("controller_version")!=expected:raise SystemExit("controller version mismatch")
    print(json.dumps({"bridge":"healthy","extension_root":str(root),"controller_version":expected,"browser":d},indent=2))
if __name__=="__main__":main()
