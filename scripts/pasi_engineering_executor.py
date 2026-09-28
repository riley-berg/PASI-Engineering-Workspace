#!/usr/bin/env python3
from __future__ import annotations
import json,os,re,subprocess,sys
from pathlib import Path
import subprocess
REPO="th3-st0v3/PASI-Engineering-Workspace";BEGIN="PASI_RESULT_PATCH_BEGIN";END="PASI_RESULT_PATCH_END"
MAX_MODEL_REPAIR_ATTEMPTS=int(os.environ.get("PASI_MODEL_REPAIR_ATTEMPTS","4"))
MAX_FEEDBACK_CHARS=12000
MARKERS={k:re.compile(p,re.MULTILINE|re.IGNORECASE if k=="allow_delete" else re.MULTILINE) for k,p in {
"status":r"^PASI_RESULT_STATUS:\s*(.+)$","summary":r"^PASI_RESULT_SUMMARY:\s*(.+)$","requirements":r"^PASI_RESULT_REQUIREMENTS:\s*(.+)$","limitations":r"^PASI_RESULT_LIMITATIONS:\s*(.+)$","research":r"^PASI_RESULT_RESEARCH:\s*(.+)$","ux":r"^PASI_RESULT_UX:\s*(.+)$","backend":r"^PASI_RESULT_BACKEND:\s*(.+)$","evidence":r"^PASI_RESULT_EVIDENCE:\s*(.+)$","repository_progress":r"^PASI_RESULT_REPOSITORY_PROGRESS:\s*(.+)$","allow_delete":r"^PASI_RESULT_ALLOW_DELETE:\s*(true|false)$"}.items()}
def run(cmd,cwd,timeout,input_text=None,env=None):
    try:p=subprocess.run(cmd,cwd=cwd,input=input_text,capture_output=True,text=True,timeout=timeout,check=False,env=env)
    except (OSError,subprocess.TimeoutExpired) as e:return 124,str(e)
    return p.returncode,((p.stdout or "")+(p.stderr or "")).strip()[-30000:]
def parse(text):
    vals={};missing=[]
    for k,pat in MARKERS.items():
        m=pat.findall(text)
        if len(m)!=1:missing.append(k)
        else:vals[k]=m[0].strip()
    if missing:raise RuntimeError("missing/duplicate markers: "+", ".join(missing))
    if text.count(BEGIN)!=1 or text.count(END)!=1:raise RuntimeError("patch fence invalid")
    patch=text.split(BEGIN,1)[1].split(END,1)[0].strip("\n")
    if len(patch.encode())>250000:raise RuntimeError("patch exceeds 250 KiB")
    return vals["status"].lower(),vals,patch
def validate(vals):
    if vals["status"].lower()!="complete":raise RuntimeError("task did not complete")
    if vals["requirements"].lower()!="complete":raise RuntimeError("requirements incomplete")
    if vals["limitations"].lower() not in {"handled","none","not_applicable"}:raise RuntimeError("invalid limitations")
    if vals["research"].lower() not in {"performed","not_applicable"}:raise RuntimeError("invalid research")
    if vals["ux"].lower() not in {"verified","not_applicable"}:raise RuntimeError("invalid ux")
    if vals["backend"].lower() not in {"verified","not_applicable"}:raise RuntimeError("invalid backend")
    if len(vals["evidence"].strip())<48:raise RuntimeError("evidence too short")
def repair_feedback(base_task:str,feedback:str,attempt:int)->str:
    if not feedback.strip():
        return base_task
    return f"""{base_task}

PREVIOUS EXECUTION FEEDBACK:
This is verifier feedback from repair attempt {attempt - 1}. Treat it as authoritative evidence about what failed. Resolve the reported failure in the next attempt; do not merely explain it. Re-check the exact required completion contract, patch syntax, repository paths, and verification results before returning the contract.

{feedback[-MAX_FEEDBACK_CHARS:]}

Return the normal PASI completion contract and a corrected unified patch for the same task.
"""


def cleanup_failed_attempt(root:Path)->None:
    run(["git","reset","--hard","HEAD"],root,60)
    run(["git","clean","-fd"],root,60)


def validate_paths(root,patch):
    paths=set()
    for l in patch.splitlines():
        if l.startswith("diff --git a/"):
            m=re.match(r"diff --git a/(.+) b/(.+)$",l)
            if not m:raise RuntimeError("malformed diff header")
            paths.update(m.groups())
    if not paths:raise RuntimeError("patch has no paths")
    for raw in paths:
        if raw.startswith("/") or raw==".." or raw.startswith("../") or "/../" in raw or "\\" in raw:raise RuntimeError("unsafe path")
        p=(root/raw).resolve()
        try:p.relative_to(root.resolve())
        except ValueError:raise RuntimeError("patch escapes worktree")
        if ".git" in p.parts:raise RuntimeError("patch touches git metadata")
def canonical_issue_context() -> str:
    import urllib.request
    token=os.environ.get("PASI_GITHUB_TOKEN","").strip() or os.environ.get("GITHUB_TOKEN","").strip()
    issue=os.environ.get("PASI_TASK_SOURCE_ISSUE","").strip()
    if not token or not issue:
        return ""
    req=urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/issues/{issue}",
        headers={"Accept":"application/vnd.github+json","Authorization":f"Bearer {token}","User-Agent":"pasi-engineering-workspace-168h"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req,timeout=20) as response:
            payload=json.loads(response.read().decode("utf-8"))
    except Exception:
        return ""
    body=payload.get("body") if isinstance(payload,dict) else ""
    return str(body or "")[:30000]


def main():
    root=Path(os.environ.get("PASI_ACCEPTANCE_WORKTREE","")).expanduser().resolve()
    if not root.is_dir():raise SystemExit("PASI_ACCEPTANCE_WORKTREE required")
    top=Path(subprocess.check_output(["git","-C",str(root),"rev-parse","--show-toplevel"],text=True).strip())
    if top!=root:raise SystemExit("acceptance worktree is not top-level")
    code,status=run(["git","status","--porcelain","--untracked-files=all"],root,30)
    if code or status.strip():raise SystemExit("acceptance worktree must start clean")
    if not os.environ.get("PASI_TASK_ID") or not os.environ.get("PASI_TASK_TITLE"):raise SystemExit("task metadata missing")
    runtime=Path(os.environ.get("PASI_ENGINEERING_RUNTIME_DIR",str(Path.home()/".pasi"/"engineering-workspace-168h"/"runtime"))).expanduser().resolve();runtime.mkdir(parents=True,exist_ok=True)
    ext=Path(
        os.environ.get(
            "PASI_ENGINEERING_EXTENSION_ROOT",
            str(Path(__file__).resolve().parents[1] / "extensions" / "pasi-chatgpt"),
        )
    ).expanduser().resolve()
    code,out=run([sys.executable,str(Path(__file__).with_name("pasi_engineering_browser_preflight.py")),"--extension-root",str(ext)],Path(__file__).resolve().parents[1],60);(runtime/"last-preflight.txt").write_text(out+"\n",encoding="utf-8")
    if code:return code
    task_id=os.environ["PASI_TASK_ID"]
    task_phase=os.environ.get("PASI_TASK_PHASE","")
    task_issue=os.environ.get("PASI_TASK_SOURCE_ISSUE","")
    previous = os.environ.get("PASI_TASK_PREVIOUS_CONTEXT", "").strip()
    continuity = f"\n\nRUN CONTINUITY CONTEXT:\n{previous}" if previous else ""
    task=os.environ["PASI_TASK_TITLE"]+f"\n\nCanonical task {task_id}; phase {task_phase}; source issue #{task_issue}. Work only in PASI Engineering Workspace.\n\nCANONICAL ISSUE CONTEXT:\n{canonical_issue_context()}{continuity}"
    before=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip()
    feedback=""
    vals={}
    patch=""
    for attempt in range(1,MAX_MODEL_REPAIR_ATTEMPTS+1):
        task_prompt=repair_feedback(task,feedback,attempt)
        code,out=run([sys.executable,"-m","scripts.pasi_chat_guard",task_prompt,"--repo",str(root),"--extension-root",str(ext),"--timeout",os.environ.get("PASI_TASK_TIMEOUT_SECONDS","1800")],Path(__file__).resolve().parents[1],float(os.environ.get("PASI_TASK_TIMEOUT_SECONDS","1800"))+60)
        (runtime/"last-executor-output.txt").write_text(out+"\n",encoding="utf-8")
        if code:
            feedback=f"Chat guard failed with exit code {code}.\n\nVerifier/process output:\n{out[-MAX_FEEDBACK_CHARS:]}"
            print(f"PASI model repair attempt {attempt}/{MAX_MODEL_REPAIR_ATTEMPTS} failed: chat guard exit {code}",file=sys.stderr)
            if attempt==MAX_MODEL_REPAIR_ATTEMPTS:
                print(out,file=sys.stderr)
                return code or 1
            continue
        response=out.split("=== CHATGPT RESPONSE ===",1)[1] if "=== CHATGPT RESPONSE ===" in out else out
        try:
            status,vals,patch=parse(response)
            vals["status"]=status
            validate(vals)
            validate_paths(root,patch)
        except Exception as exc:
            feedback=f"Completion-contract or patch validation failed: {exc}\n\nPrevious model response:\n{response[-MAX_FEEDBACK_CHARS:]}"
            cleanup_failed_attempt(root)
            print(f"PASI model repair attempt {attempt}/{MAX_MODEL_REPAIR_ATTEMPTS} failed: {exc}",file=sys.stderr)
            if attempt==MAX_MODEL_REPAIR_ATTEMPTS:
                print(response,file=sys.stderr)
                return 1
            continue
        patch_error=None
        for args,input_text in [(["git","apply","--check","--whitespace=nowarn","-"],patch),(["git","apply","--whitespace=nowarn","-"],patch)]:
            code,msg=run(args,root,60,input_text)
            if code:
                patch_error=msg
                break
        if patch_error is not None:
            feedback=f"Patch application failed. Correct the unified diff and return a complete replacement patch.\n\nGit verifier output:\n{patch_error[-MAX_FEEDBACK_CHARS:]}"
            cleanup_failed_attempt(root)
            print(f"PASI model repair attempt {attempt}/{MAX_MODEL_REPAIR_ATTEMPTS} failed: patch application",file=sys.stderr)
            if attempt==MAX_MODEL_REPAIR_ATTEMPTS:
                print(patch_error,file=sys.stderr)
                return 1
            continue
        code,msg=run(["git","diff","--check"],root,60)
        if code:
            feedback=f"Git diff validation failed after applying the patch. Repair the patch and return a corrected unified diff.\n\nGit verifier output:\n{msg[-MAX_FEEDBACK_CHARS:]}"
            cleanup_failed_attempt(root)
            print(f"PASI model repair attempt {attempt}/{MAX_MODEL_REPAIR_ATTEMPTS} failed: git diff --check",file=sys.stderr)
            if attempt==MAX_MODEL_REPAIR_ATTEMPTS:
                print(msg,file=sys.stderr)
                return 1
            continue
        test_env=os.environ.copy()
        worktree_src=root/"src"
        inherited=test_env.get("PYTHONPATH","")
        inherited_parts=[p for p in inherited.split(os.pathsep) if p and Path(p).resolve()!=worktree_src.resolve()]
        test_env["PYTHONPATH"]=os.pathsep.join([str(worktree_src),*inherited_parts])
        code,msg=run([sys.executable,"-m","pytest","-q"],root,900,env=test_env)
        if code:
            feedback=f"Python verification failed after applying the patch. Fix the reported tests and return a corrected unified diff.\n\nPytest output:\n{msg[-MAX_FEEDBACK_CHARS:]}"
            cleanup_failed_attempt(root)
            print(f"PASI model repair attempt {attempt}/{MAX_MODEL_REPAIR_ATTEMPTS} failed: pytest",file=sys.stderr)
            if attempt==MAX_MODEL_REPAIR_ATTEMPTS:
                print(msg,file=sys.stderr)
                return 1
            continue
        code,msg=run(["node","--test","web/app.test.js"],root,300)
        if code:
            feedback=f"Frontend verification failed after applying the patch. Fix the reported tests and return a corrected unified diff.\n\nFrontend test output:\n{msg[-MAX_FEEDBACK_CHARS:]}"
            cleanup_failed_attempt(root)
            print(f"PASI model repair attempt {attempt}/{MAX_MODEL_REPAIR_ATTEMPTS} failed: frontend tests",file=sys.stderr)
            if attempt==MAX_MODEL_REPAIR_ATTEMPTS:
                print(msg,file=sys.stderr)
                return 1
            continue
        run(["git","add","--all"],root,60)
        msg=re.sub(r"[^A-Za-z0-9 .:_/-]+","",os.environ["PASI_TASK_ID"]+" "+os.environ["PASI_TASK_TITLE"]).strip()[:120]
        code,out=run(["git","commit","-m",f"pasi: {msg}"],root,120)
        if code:
            feedback=f"Git commit failed after all verification passed. Preserve the intended patch and fix the commit-stage failure.\n\nGit output:\n{out[-MAX_FEEDBACK_CHARS:]}"
            cleanup_failed_attempt(root)
            print(f"PASI model repair attempt {attempt}/{MAX_MODEL_REPAIR_ATTEMPTS} failed: git commit",file=sys.stderr)
            if attempt==MAX_MODEL_REPAIR_ATTEMPTS:
                print(out,file=sys.stderr)
                return 1
            continue
        after=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip()
        code,status=run(["git","status","--porcelain","--untracked-files=all"],root,30)
        if code or status.strip() or after==before:
            feedback=f"Post-commit verification failed. Expected a new clean commit from {before}, got {after!r}; status was {status!r}. Preserve the task intent and repair the repository state."
            cleanup_failed_attempt(root)
            print(f"PASI model repair attempt {attempt}/{MAX_MODEL_REPAIR_ATTEMPTS} failed: post-commit verification",file=sys.stderr)
            if attempt==MAX_MODEL_REPAIR_ATTEMPTS:
                return 1
            continue
        print(json.dumps({"executor":"pasi-engineering-executor","repository":REPO,"task_id":os.environ["PASI_TASK_ID"],"commit_before":before,"commit_after":after,"pytest":"passed","frontend":"passed","evidence_chars":len(vals["evidence"]),"repair_attempt":attempt},indent=2))
        return 0
    return 1
if __name__=="__main__":raise SystemExit(main())
