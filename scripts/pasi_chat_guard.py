#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os,subprocess,sys
from pathlib import Path
from typing import Any
from automation.computer_use.capability_gateway import CapabilityGateway
from automation.computer_use.local_access import LocalAccessBroker
BEGIN="PASI_COMPUTER_REQUEST_BEGIN"; END="PASI_COMPUTER_REQUEST_END"
def extract(text:str)->list[dict[str,Any]]:
    if BEGIN not in text or END not in text:return []
    out=[]
    for line in text.split(BEGIN,1)[1].split(END,1)[0].splitlines():
        try:o=json.loads(line.strip())
        except json.JSONDecodeError:continue
        if isinstance(o,dict):out.append(o)
        if len(out)>=3:break
    return out
def main()->int:
    p=argparse.ArgumentParser();p.add_argument("task");p.add_argument("--repo",type=Path,required=True);p.add_argument("--timeout",type=float,default=1800);p.add_argument("--extension-root",type=Path,default=None);a=p.parse_args()
    root=a.repo.expanduser().resolve(); current=a.task.strip()
    for round_no in range(4):
        env=os.environ.copy()
        cmd=[sys.executable,"-m","scripts.pasi_chat",current,"--repo",str(root),"--phase",env.get("PASI_TASK_PHASE",""),"--task-id",env.get("PASI_TASK_ID",""),"--issue",env.get("PASI_TASK_SOURCE_ISSUE",""),"--timeout",str(a.timeout)]
        ext = a.extension_root or Path(env.get("PASI_ENGINEERING_EXTENSION_ROOT",str(Path(__file__).resolve().parents[1]/"extensions"/"pasi-chatgpt")))
        cmd.extend(["--extension-root",str(ext.expanduser().resolve())])
        pr=subprocess.run(cmd,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=a.timeout+45,check=False)
        output=(pr.stdout or "")+(pr.stderr or ""); print(output,end="")
        requests=extract(output)
        if pr.returncode!=0 or not requests or round_no>=3:return pr.returncode
        results=[CapabilityGateway(LocalAccessBroker(root)).dispatch(req) for req in requests]
        print(f"PASI computer capability round {round_no+1}: executed {len(results)} request(s).",file=sys.stderr)
        current=f"""CURRENT TASK:
{a.task}

PASI COMPUTER RESULTS:
{json.dumps(results,indent=2,ensure_ascii=False)[:30000]}

Continue the same Engineering Workspace task using these results as evidence only. Do not request unrestricted access. Return the normal completion contract and patch."""
    return 1
if __name__=="__main__":raise SystemExit(main())
