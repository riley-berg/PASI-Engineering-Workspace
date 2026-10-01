from scripts.pasi_engineering_executor import extract_canonical_task_context


def test_task_context_excludes_completed_sibling_tasks():
    body = """# P0 — Runtime proof & delivery infrastructure
Dependency: Immediate priority; all later phases depend on this evidence.

## Tasks

- [x] **P0.1 — M0 live task acceptance** — old completed task
- [ ] **P0.4 — 168-hour long-run acceptance** — current task description
- [ ] **P0.6 — CI/workflow consolidation** — another pending sibling
- [x] **P0.5 — Acceptance evidence registry** — completed sibling

## Completion rule
Each task requires implementation evidence appropriate to its scope.
"""
    context = extract_canonical_task_context(body, "P0.4")

    assert "CANONICAL TASK P0.4" in context
    assert "current task description" in context
    assert "P0.1" not in context
    assert "P0.5" not in context
    assert "P0.6" not in context
    assert "COMPLETION RULE:" in context
