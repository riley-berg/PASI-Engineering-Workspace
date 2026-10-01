#!/usr/bin/env python3
from __future__ import annotations
import json,os,re,subprocess,sys,time
from pathlib import Path
from urllib.parse import quote

from automation.computer_use.chatgpt import ChatGPTAdapterError, UrllibBridgeTransport
import subprocess
REPO="th3-st0v3/PASI-Engineering-Workspace";BEGIN="PASI_RESULT_PATCH_BEGIN";END="PASI_RESULT_PATCH_END"
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


def acknowledge_response_processing(output: str) -> dict:
    """Release the prompt handoff barrier only after the task is fully committed and clean."""
    match = re.search(r"^Prompt operation:\s*(\S+)\s*$", output, re.MULTILINE)
    if not match:
        raise RuntimeError("prompt operation id missing from ChatGPT executor output")
    operation_id = match.group(1).strip()
    transport = UrllibBridgeTransport(timeout_seconds=10.0)
    operation_payload = transport.request(
        "GET",
        f"/operation?operation_id={quote(operation_id, safe='')}"
    )
    operation = operation_payload.get("operation")
    if not isinstance(operation, dict):
        raise RuntimeError("bridge operation record missing after task commit")
    controller_id = operation.get("controller_id")
    if not isinstance(controller_id, str) or not controller_id.strip():
        raise RuntimeError("bridge controller id missing after task commit")

    last_error = None
    for attempt in range(4):
        try:
            acknowledged = transport.request(
                "POST",
                "/chat/processed",
                {
                    "operation_id": operation_id,
                    "controller_id": controller_id.strip(),
                },
            )
            if acknowledged.get("ok") is True and acknowledged.get("response_processing_complete") is True:
                return {
                    "operation_id": operation_id,
                    "controller_id": controller_id.strip(),
                    "timing": acknowledged.get("timing") or {},
                }
            last_error = f"bridge rejected response-processing acknowledgement: {acknowledged}"
        except ChatGPTAdapterError as exc:
            last_error = str(exc)
        if attempt < 3:
            time.sleep(0.15)
    raise RuntimeError(last_error or "response-processing acknowledgement failed")


def quarantine_failed_candidate(root: Path, baseline: str, task_id: str, patch: str, *, pytest_output: str = "", frontend_output: str = "", failure_reason: str = "") -> dict[str, str]:
    safe_task = re.sub(r"[^A-Za-z0-9_.-]+", "-", task_id).strip("-").lower() or "task"
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    quarantine_branch = f"pasi/quarantine/{safe_task}-{stamp}-{os.getpid()}"
    artifact_root = Path(
        os.environ.get(
            "PASI_FAILED_CANDIDATE_DIR",
            str(Path.home() / ".pasi" / "engineering-workspace-168h" / "failed-candidates"),
        )
    ).expanduser().resolve() / f"{safe_task}-{stamp}-{os.getpid()}"
    artifact_root.mkdir(parents=True, exist_ok=True)
    original_branch = ""
    result: dict[str, str] = {
        "artifact_dir": str(artifact_root),
        "baseline": baseline,
        "quarantine_branch": quarantine_branch,
    }

    try:
        original_branch = run(["git", "branch", "--show-current"], root, 30)[1].strip()
        (artifact_root / "candidate.patch").write_text(patch + "\n", encoding="utf-8")
        (artifact_root / "pytest.log").write_text(pytest_output + "\n", encoding="utf-8")
        (artifact_root / "frontend.log").write_text(frontend_output + "\n", encoding="utf-8")
        (artifact_root / "failure.json").write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "baseline": baseline,
                    "original_branch": original_branch,
                    "quarantine_branch": quarantine_branch,
                    "failure_reason": failure_reason,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        runtime_output = Path(
            os.environ.get(
                "PASI_ENGINEERING_RUNTIME_DIR",
                str(Path.home() / ".pasi" / "engineering-workspace-168h" / "runtime"),
            )
        ).expanduser() / "last-executor-output.txt"
        if runtime_output.is_file():
            (artifact_root / "model-response.txt").write_text(runtime_output.read_text(encoding="utf-8"), encoding="utf-8")

        code, out = run(["git", "switch", "-c", quarantine_branch], root, 60)
        if code:
            raise RuntimeError(f"could not create quarantine branch: {out}")
        code, out = run(["git", "add", "--all"], root, 60)
        if code:
            raise RuntimeError(f"could not stage failed candidate: {out}")
        code, out = run(
            ["git", "commit", "-m", f"pasi: quarantine failed candidate {task_id}"],
            root,
            120,
        )
        if code:
            raise RuntimeError(f"could not preserve failed candidate commit: {out}")
        result["quarantine_commit"] = run(["git", "rev-parse", "HEAD"], root, 30)[1].strip()
    except Exception as exc:
        result["quarantine_error"] = str(exc)
    finally:
        if original_branch:
            run(["git", "switch", original_branch], root, 60)
        run(["git", "reset", "--hard", baseline], root, 60)
        run(["git", "clean", "-fd"], root, 60)
        result["restored_head"] = run(["git", "rev-parse", "HEAD"], root, 30)[1].strip()
        status = run(["git", "status", "--porcelain", "--untracked-files=all"], root, 30)[1].strip()
        result["restored_clean"] = str(not bool(status))

    return result

def extract_canonical_task_context(body: str, task_id: str) -> str:
    text = str(body or "")
    task_id = str(task_id or "").strip()
    if not task_id:
        return ""

    phase = re.search(r"^# (P[0-9]+) — (.+)$", text, re.MULTILINE)
    dependency = re.search(r"^Dependency:\s*(.+)$", text, re.MULTILINE)
    task = re.search(
        rf"^\s*- \[[ xX]\] \*\*{re.escape(task_id)} — ([^*]+)\*\*(?:\s+—\s+(.+?))?\s*$",
        text,
        re.MULTILINE,
    )

    sections = []
    if phase:
        sections.append(f"PHASE: {phase.group(1)} — {phase.group(2).strip()}")
    if dependency:
        sections.append(f"DEPENDENCY: {dependency.group(1).strip()}")
    if task:
        description = task.group(2).strip() if task.lastindex and task.lastindex >= 2 and task.group(2) else ""
        if description:
            sections.append(f"CANONICAL TASK {task_id}: {task.group(1).strip()} — {description}")
        else:
            sections.append(f"CANONICAL TASK {task_id}: {task.group(1).strip()}")

    completion = re.search(
        r"^## Completion rule\n(.*?)(?=^## |\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if completion:
        sections.append("COMPLETION RULE:\n" + completion.group(1).strip())

    return "\n\n".join(section for section in sections if section).strip()


def canonical_issue_context() -> str:
    import urllib.request
    token=os.environ.get("PASI_GITHUB_TOKEN","").strip() or os.environ.get("GITHUB_TOKEN","").strip()
    issue=os.environ.get("PASI_TASK_SOURCE_ISSUE","").strip()
    task_id=os.environ.get("PASI_TASK_ID","").strip()
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
    return extract_canonical_task_context(str(body or "")[:30000], task_id)


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
    task=os.environ["PASI_TASK_TITLE"]+f"\n\nCanonical task {task_id}; phase {task_phase}; source issue #{task_issue}. Work only in PASI Engineering Workspace.\n\nCANONICAL TASK CONTEXT:\n{canonical_issue_context()}{continuity}"
    code,out=run([sys.executable,"-m","scripts.pasi_chat_guard",task,"--repo",str(root),"--extension-root",str(ext),"--timeout",os.environ.get("PASI_TASK_TIMEOUT_SECONDS","1800")],Path(__file__).resolve().parents[1],float(os.environ.get("PASI_TASK_TIMEOUT_SECONDS","1800"))+60);(runtime/"last-executor-output.txt").write_text(out+"\n",encoding="utf-8")
    if code:print(out,file=sys.stderr);return code
    response=out.split("=== CHATGPT RESPONSE ===",1)[1] if "=== CHATGPT RESPONSE ===" in out else out
    status,vals,patch=parse(response);vals["status"]=status;validate(vals);validate_paths(root,patch)
    before=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip()
    for args,input_text in [(["git","apply","--check","--whitespace=nowarn","-"],patch),(["git","apply","--whitespace=nowarn","-"],patch)]:
        code,msg=run(args,root,60,input_text); 
        if code:print(msg,file=sys.stderr);return 1
    code,msg=run(["git","diff","--check"],root,60)
    if code:
        quarantine = quarantine_failed_candidate(root, before, task_id, patch, failure_reason="git diff --check failed", pytest_output=msg)
        print(json.dumps({"verification_failure":"git diff --check","quarantine":quarantine},indent=2),file=sys.stderr)
        return 1
    # The acceptance worktree is the task source of truth. Keep pytest from
    # importing an editable install that points at another checkout.
    test_env=os.environ.copy()
    worktree_src=root/"src"
    inherited=test_env.get("PYTHONPATH","")
    inherited_parts=[p for p in inherited.split(os.pathsep) if p and Path(p).resolve()!=worktree_src.resolve()]
    test_env["PYTHONPATH"]=os.pathsep.join([str(worktree_src),*inherited_parts])
    code,msg=run([sys.executable,"-m","pytest","-q"],root,900,env=test_env)
    pytest_output = msg
    if code:
        quarantine = quarantine_failed_candidate(
            root, before, task_id, patch,
            pytest_output=pytest_output,
            failure_reason="pytest failed",
        )
        print(json.dumps({"verification_failure":"pytest","quarantine":quarantine},indent=2),file=sys.stderr)
        return 1
    code,msg=run(["node","--test","web/app.test.js"],root,300)
    frontend_output = msg
    if code:
        quarantine = quarantine_failed_candidate(
            root, before, task_id, patch,
            pytest_output=pytest_output,
            frontend_output=frontend_output,
            failure_reason="frontend tests failed",
        )
        print(json.dumps({"verification_failure":"frontend","quarantine":quarantine},indent=2),file=sys.stderr)
        return 1
    run(["git","add","--all"],root,60);msg=re.sub(r"[^A-Za-z0-9 .:_/-]+","",os.environ["PASI_TASK_ID"]+" "+os.environ["PASI_TASK_TITLE"]).strip()[:120]
    code,out=run(["git","commit","-m",f"pasi: {msg}"],root,120)
    if code:print(out,file=sys.stderr);return 1
    after=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip();code,status=run(["git","status","--porcelain","--untracked-files=all"],root,30)
    if code or status.strip() or after==before:return 1
    processing_ack = acknowledge_response_processing(out)
    print(json.dumps({"executor":"pasi-engineering-executor","repository":REPO,"task_id":os.environ["PASI_TASK_ID"],"commit_before":before,"commit_after":after,"pytest":"passed","frontend":"passed","evidence_chars":len(vals["evidence"]),"response_processing_ack":processing_ack},indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
