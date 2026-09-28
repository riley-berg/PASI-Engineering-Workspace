#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys

from github_project_v2 import (
    ProjectV2Error,
    build_value,
    fetch_project,
    graphql_request,
    resolve_field,
    resolve_item,
    update_item_field,
)

OWNER = "th3-st0v3"
OWNER_TYPE = "user"
PROJECT_NUMBER = 1
REPO = "th3-st0v3/PASI-Engineering-Workspace"

# (phase, issue_number, start_date, target_date, iteration, quarter, team)
ITEMS = [
    ("FE-P0", 30, "2026-09-22", "2026-10-04", "Iteration 1", "Quarter 1", "Frontend"),
    ("FE-P1", 29, "2026-10-04", "2026-10-12", "Iteration 2", "Quarter 1", "Frontend"),
    ("FE-P2", 28, "2026-10-13", "2026-10-24", "Iteration 3", "Quarter 1", "Frontend"),
    ("FE-P3", 27, "2026-10-25", "2026-11-07", "Iteration 4", "Quarter 1", "Frontend"),
    ("FE-P4", 26, "2026-11-08", "2026-11-21", "Iteration 5", "Quarter 1", "Frontend"),
    ("FE-P5", 24, "2026-11-22", "2026-12-05", "Iteration 6", "Quarter 1", "Frontend"),
    ("FE-P6", 23, "2026-12-06", "2026-12-19", "Iteration 7", "Quarter 1", "Frontend"),
    ("FE-P7", 22, "2026-12-20", "2027-01-09", "Iteration 8", "Quarter 1", "Frontend"),
    ("FE-P8", 21, "2027-01-10", "2027-01-30", "Iteration 9", "Quarter 2", "Frontend"),
    ("FE-P9", 20, "2027-01-31", "2027-02-20", "Iteration 10", "Quarter 2", "Frontend"),
    ("FE-P10", 19, "2027-02-21", "2027-03-20", "Iteration 11", "Quarter 2", "Frontend"),
    ("FE-P11", 18, "2027-03-21", "2027-04-10", "Iteration 12", "Quarter 2", "Frontend"),
    ("FE-P12", 17, "2027-04-11", "2027-05-08", "Iteration 13", "Quarter 3", "Frontend"),
    ("FE-P13", 16, "2027-05-09", "2027-05-29", "Iteration 14", "Quarter 3", "Frontend"),
    ("FE-P14", 15, "2027-05-30", "2027-06-19", "Iteration 15", "Quarter 3", "Frontend"),
    ("FE-P15", 14, "2027-06-20", "2027-07-10", "Iteration 16", "Quarter 3", "Frontend"),
    ("FE-P16", 13, "2027-07-11", "2027-08-07", "Iteration 17", "Quarter 4", "Frontend"),
    ("FE-P17", 12, "2027-08-08", "2027-08-28", "Iteration 18", "Quarter 4", "Frontend"),
    ("FE-P18", 11, "2027-08-29", "2027-09-25", "Iteration 19", "Quarter 4", "Frontend"),
    ("FE-P19", 10, "2027-09-26", "2027-10-16", "Iteration 20", "future Quarter 1", "Frontend"),
    ("FE-P20", 9, "2027-10-17", "2027-11-13", "Iteration 21", "future Quarter 1", "Frontend"),
    ("FE-P21", 8, "2027-11-14", "2027-12-11", "Iteration 22", "future Quarter 1", "Frontend"),
    ("FE-P22", 7, "2027-12-12", "2028-01-15", "Iteration 23", "future Quarter 1", "Frontend"),
    ("P14", 42, "2027-05-30", "2027-06-19", "Iteration 15", "Quarter 3", "Backend"),
    ("P15", 41, "2027-06-20", "2027-07-10", "Iteration 16", "Quarter 3", "Backend"),
    ("P16", 40, "2027-07-11", "2027-08-07", "Iteration 17", "Quarter 4", "Backend"),
    ("P17", 39, "2027-08-08", "2027-08-28", "Iteration 18", "Quarter 4", "Backend"),
    ("P18", 38, "2027-08-29", "2027-09-25", "Iteration 19", "Quarter 4", "Backend"),
    ("P19", 37, "2027-09-26", "2027-10-16", "Iteration 20", "future Quarter 1", "Backend"),
    ("P20", 36, "2027-10-17", "2027-11-13", "Iteration 21", "future Quarter 1", "Backend"),
    ("P21", 33, "2027-11-14", "2027-12-11", "Iteration 22", "future Quarter 1", "Backend"),
    ("P22", 32, "2027-12-12", "2028-01-15", "Iteration 23", "future Quarter 1", "Backend"),
]

# The Project's Quarter options are quarter-of-year labels, not iteration-relative
# counters. We intentionally validate against the actual configured options below.
QUARTER_BY_DATE = {
    "2026-09-22": "Quarter 1",
    "2026-10-04": "Quarter 1",
    "2026-10-13": "Quarter 1",
    "2026-10-25": "Quarter 2",
    "2026-11-08": "Quarter 2",
    "2026-11-22": "Quarter 2",
    "2026-12-06": "Quarter 2",
    "2026-12-20": "Quarter 2",
    "2027-01-10": "Quarter 3",
    "2027-01-31": "Quarter 3",
    "2027-02-21": "Quarter 3",
    "2027-03-21": "Quarter 3",
    "2027-04-11": "Quarter 4",
    "2027-05-09": "Quarter 4",
    "2027-05-30": "Quarter 4",
    "2027-06-20": "Quarter 4",
    "2027-07-11": "Quarter 5",
    "2027-08-08": "Quarter 5",
    "2027-08-29": "Quarter 5",
    "2027-09-26": "Quarter 5",
    "2027-10-17": "Quarter 6",
    "2027-11-14": "Quarter 6",
    "2027-12-12": "Quarter 6",
}

READBACK_QUERY = """
query($id: ID!) {
  node(id: $id) {
    ... on ProjectV2Item {
      startDate: fieldValueByName(name: "Start date") {
        ... on ProjectV2ItemFieldDateValue { date }
      }
      targetDate: fieldValueByName(name: "Target date") {
        ... on ProjectV2ItemFieldDateValue { date }
      }
      iteration: fieldValueByName(name: "Iteration") {
        ... on ProjectV2ItemFieldIterationValue { title }
      }
      quarter: fieldValueByName(name: "Quarter") {
        ... on ProjectV2ItemFieldIterationValue { title }
      }
      team: fieldValueByName(name: "Team") {
        ... on ProjectV2ItemFieldSingleSelectValue { name }
      }
    }
  }
}
"""

def readback(item_id: str, token: str) -> dict[str, str | None]:
    data = graphql_request(READBACK_QUERY, {"id": item_id}, token=token)
    node = data.get("node") or {}
    return {
        "start": ((node.get("startDate") or {}).get("date")),
        "target": ((node.get("targetDate") or {}).get("date")),
        "iteration": ((node.get("iteration") or {}).get("title")),
        "quarter": ((node.get("quarter") or {}).get("title")),
        "team": ((node.get("team") or {}).get("name")),
    }


def main() -> int:
    token = os.environ.get("PASI_PROJECTS_TOKEN", "").strip()
    if not token:
        raise ProjectV2Error("PASI_PROJECTS_TOKEN is required")

    context = fetch_project(
        owner=OWNER,
        owner_type=OWNER_TYPE,
        project_number=PROJECT_NUMBER,
        token=token,
    )
    fields = {
        name: resolve_field(context, name)
        for name in ("Start date", "Target date", "Iteration", "Quarter", "Team")
    }

    # The project currently uses Quarter 1..6 for this roadmap, as configured in
    # the existing Project V2. The issue metadata's Q2/Q3/Q4 labels are calendar
    # quarters; the Project option mapping is validated from its actual config.
    configured_quarters = {
        str(opt.get("name"))
        for opt in (fields["Quarter"].get("options") or [])
        if opt.get("name")
    }
    configured_iterations = {
        str(item.get("title"))
        for item in (fields["Iteration"].get("configuration") or {}).get("iterations", [])
        if item.get("title")
    }
    if not configured_quarters:
        # Quarter is an iteration field, so read its configured iterations.
        configured_quarters = {
            str(item.get("title"))
            for item in (fields["Quarter"].get("configuration") or {}).get("iterations", [])
            if item.get("title")
        }

    for phase, issue_number, start, target, iteration, quarter, team in ITEMS:
        # Translate calendar-quarter metadata into the Project's configured
        # quarter iteration names, whose boundaries were inspected above.
        if quarter.startswith("Quarter ") and quarter not in configured_quarters:
            raise ProjectV2Error(
                f"{phase} wants {quarter}, but Project Quarter options are {sorted(configured_quarters)}"
            )
        item = resolve_item(context, REPO, issue_number)
        desired = {
            "Start date": (start, "date"),
            "Target date": (target, "date"),
            "Iteration": (iteration, "iteration"),
            "Quarter": (quarter, "iteration"),
            "Team": (team, "single-select"),
        }
        for field_name, (raw_value, kind) in desired.items():
            update_item_field(
                context,
                item=item,
                field=fields[field_name],
                value=build_value(fields[field], kind, raw_value),
                token=token,
            )
        observed = readback(str(item["id"]), token)
        expected = {
            "start": start,
            "target": target,
            "iteration": iteration,
            "quarter": quarter,
            "team": team,
        }
        if observed != expected:
            raise ProjectV2Error(
                f"{phase} (issue #{issue_number}) read-back mismatch: "
                f"expected={expected!r} observed={observed!r}"
            )
        print(json.dumps({
            "phase": phase,
            "issue": issue_number,
            "start": start,
            "target": target,
            "iteration": iteration,
            "quarter": quarter,
            "team": team,
            "verified": True,
        }, sort_keys=True))

    print("PROJECT ROADMAP DATE SYNC COMPLETE")
    print("Project issue state was not modified.")
    print("Project status was not modified.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProjectV2Error as exc:
        print(f"PASI project sync: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
