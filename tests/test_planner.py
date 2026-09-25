import json

from pasi.core.planner import Planner

class FakeModel:
    def complete(self, prompt: str) -> str:
        request = json.loads(prompt)
        return json.dumps({"kind": "research", "task_id": request["task_id"], "instruction": "Inspect the primary source and record evidence."})

def test_planner_proposes_one_bounded_action():
    decision = Planner(FakeModel()).propose(phase_id="P0", task_id="P0.1", task_text="Establish runtime proof.")
    assert decision.kind == "research"
    assert decision.task_id == "P0.1"

def test_planner_rejects_task_identity_drift():
    class BadModel(FakeModel):
        def complete(self, prompt: str) -> str:
            return json.dumps({"kind": "research", "task_id": "other", "instruction": "Do it"})
    try:
        Planner(BadModel()).propose(phase_id="P0", task_id="P0.1", task_text="Establish runtime proof.")
    except ValueError as exc:
        assert "different task id" in str(exc)
    else:
        raise AssertionError("planner accepted task identity drift")
