#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

GRAPHQL_URL = "https://api.github.com/graphql"
API_VERSION = "2026-03-10"
OWNER = "riley-berg"
REPOSITORY = "riley-berg/PASI"
PROJECT_TITLE = "Educational Roadmap"
ISSUES = tuple(range(37, 48))

ROADMAP_PLAN: dict[int, tuple[str, str]] = {
    37: ("2026-10-05", "2027-08-22"),
    38: ("2026-10-05", "2027-05-31"),
    39: ("2027-08-23", "2029-05-31"),
    40: ("2027-08-23", "2029-01-31"),
    41: ("2027-08-23", "2029-01-31"),
    42: ("2027-08-23", "2029-01-31"),
    43: ("2027-08-23", "2029-01-31"),
    44: ("2027-08-23", "2029-01-31"),
    45: ("2026-10-05", "2029-05-31"),
    46: ("2029-01-01", "2029-05-31"),
    47: ("2026-10-05", "2029-05-31"),
}

FIELD_SPECS = {
    "Start Date": "DATE",
    "End Date": "DATE",
    "Duration (days)": "NUMBER",
    "Quarter": "TEXT",
    "Parent Group": "SINGLE_SELECT",
    "Child Group": "SINGLE_SELECT",
    "Group": "SINGLE_SELECT",
}

PARENT_GROUPS = (
    "Foundation",
    "Credit Optimization",
    "Major Branches",
    "Cross-Disciplinary",
    "Decision",
    "PASI Portfolio",
)

CHILD_GROUPS = (
    "Control Framework & Common Foundation",
    "CLEP & Credit-Elimination Strategy",
    "Blinn Engineering Academy Common Path",
    "Computer Science",
    "Computer Engineering",
    "Electrical Engineering",
    "Mechanical Engineering / Robotics",
    "Mechatronics / Automation",
    "AI / ML / Automation Layer",
    "Major Decision / ETAM Selection",
    "PASI Interdisciplinary Engineering Portfolio",
)

GROUP_BY_ISSUE: dict[int, tuple[str, str, str]] = {
    37: ("Foundation", "Control Framework & Common Foundation", "common-foundation"),
    38: ("Credit Optimization", "CLEP & Credit-Elimination Strategy", "credit-strategy"),
    39: ("Foundation", "Blinn Engineering Academy Common Path", "academy-common"),
    40: ("Major Branches", "Computer Science", "computer-science"),
    41: ("Major Branches", "Computer Engineering", "computer-engineering"),
    42: ("Major Branches", "Electrical Engineering", "electrical-engineering"),
    43: ("Major Branches", "Mechanical Engineering / Robotics", "mechanical-robotics"),
    44: ("Major Branches", "Mechatronics / Automation", "mechatronics-automation"),
    45: ("Cross-Disciplinary", "AI / ML / Automation Layer", "cross-disciplinary"),
    46: ("Decision", "Major Decision / ETAM Selection", "major-decision"),
    47: ("PASI Portfolio", "PASI Interdisciplinary Engineering Portfolio", "pasi-portfolio"),
}

DEPENDENCIES: dict[int, tuple[int, ...]] = {
    37: (),
    38: (),
    39: (37, 38),
    40: (39,),
    41: (39,),
    42: (39,),
    43: (39,),
    44: (39,),
    45: (39,),
    46: (39, 40, 41, 42, 43, 44),
    47: (45, 46),
}

LABEL_COLORS = {
    "roadmap": "ededed",
    "parent": "5319e7",
    "child": "1f6feb",
    "group": "8250df",
    "quarter": "bf8700",
    "start": "0969da",
    "end": "cf2222",
    "dependency": "6e7781",
}


class RoadmapError(RuntimeError):
    pass


def token() -> str:
    for name in ("PASI_PROJECTS_TOKEN", "PASI_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise RoadmapError("no GitHub token available")


def request_json(url: str, *, method: str = "GET", payload: bytes | None = None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token()}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "pasi-educational-roadmap-reconciler",
        "Content-Type": "application/json",
    }
    request = urllib.request.Request(url, method=method, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RoadmapError(f"GitHub API HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RoadmapError(f"GitHub API request failed: {exc}") from exc


def graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables}).encode()
    response = request_json(GRAPHQL_URL, method="POST", payload=payload)
    if response.get("errors"):
        raise RoadmapError(
            "GraphQL: " + "; ".join(str(e.get("message", e)) for e in response["errors"])
        )
    data = response.get("data")
    if not isinstance(data, dict):
        raise RoadmapError("GraphQL response had no data object")
    return data


def rest(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    body = json.dumps(payload).encode() if payload is not None else None
    return request_json(f"https://api.github.com{path}", method=method, payload=body)


PROJECTS_QUERY = """
query UserProjects($login: String!) {
  user(login: $login) {
    projectsV2(first: 100, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes { id number title url }
    }
  }
}
"""

PROJECT_QUERY = """
query Project($login: String!, $number: Int!, $fieldAfter: String, $itemAfter: String) {
  user(login: $login) {
    projectV2(number: $number) {
      id
      number
      title
      url
      fields(first: 100, after: $fieldAfter) {
        pageInfo { hasNextPage endCursor }
        nodes {
          __typename
          ... on ProjectV2Field { id name dataType }
          ... on ProjectV2IterationField { id name dataType }
          ... on ProjectV2SingleSelectField {
            id
            name
            dataType
            options { id name color description }
          }
          ... on ProjectV2MultiSelectField { id name dataType }
        }
      }
      items(first: 100, after: $itemAfter) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          content {
            __typename
            ... on Issue {
              number
              title
              repository { nameWithOwner }
            }
            ... on PullRequest {
              number
              title
              repository { nameWithOwner }
            }
          }
          startDate: fieldValueByName(name: "Start Date") {
            __typename
            ... on ProjectV2ItemFieldDateValue { date }
          }
          endDate: fieldValueByName(name: "End Date") {
            __typename
            ... on ProjectV2ItemFieldDateValue { date }
          }
          duration: fieldValueByName(name: "Duration (days)") {
            __typename
            ... on ProjectV2ItemFieldNumberValue { number }
          }
          quarter: fieldValueByName(name: "Quarter") {
            __typename
            ... on ProjectV2ItemFieldTextValue { text }
          }
          parentGroup: fieldValueByName(name: "Parent Group") {
            __typename
            ... on ProjectV2ItemFieldSingleSelectValue { name optionId }
          }
          childGroup: fieldValueByName(name: "Child Group") {
            __typename
            ... on ProjectV2ItemFieldSingleSelectValue { name optionId }
          }
          group: fieldValueByName(name: "Group") {
            __typename
            ... on ProjectV2ItemFieldSingleSelectValue { name optionId }
          }
        }
      }
    }
  }
}
"""

ISSUE_ID_QUERY = """
query Issue($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) { id }
  }
}
"""

ADD_ITEM = """
mutation AddItem($projectId: ID!, $contentId: ID!) {
  addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
    item { id }
  }
}
"""

CREATE_FIELD = """
mutation CreateField(
  $projectId: ID!
  $name: String!
  $dataType: ProjectV2CustomFieldType!
  $singleSelectOptions: [ProjectV2SingleSelectFieldOptionInput!]
) {
  createProjectV2Field(input: {
    projectId: $projectId
    name: $name
    dataType: $dataType
    singleSelectOptions: $singleSelectOptions
  }) {
    projectV2Field {
      __typename
      ... on ProjectV2Field { id name dataType }
      ... on ProjectV2IterationField { id name dataType }
      ... on ProjectV2SingleSelectField {
        id
        name
        dataType
        options { id name }
      }
      ... on ProjectV2MultiSelectField { id name dataType }
    }
  }
}
"""

UPDATE_FIELD = """
mutation UpdateField(
  $fieldId: ID!
  $singleSelectOptions: [ProjectV2SingleSelectFieldOptionInput!]
) {
  updateProjectV2Field(input: {
    fieldId: $fieldId
    singleSelectOptions: $singleSelectOptions
  }) {
    projectV2Field {
      __typename
      ... on ProjectV2SingleSelectField {
        id
        name
        dataType
        options { id name }
      }
    }
  }
}
"""

SET_FIELD = """
mutation SetField(
  $projectId: ID!
  $itemId: ID!
  $fieldId: ID!
  $value: ProjectV2FieldValue!
) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $projectId
    itemId: $itemId
    fieldId: $fieldId
    value: $value
  }) {
    projectV2Item { id }
  }
}
"""


def choose_project() -> dict[str, Any]:
    data = graphql(PROJECTS_QUERY, {"login": OWNER})
    projects = ((data.get("user") or {}).get("projectsV2") or {}).get("nodes") or []
    matches = [p for p in projects if str(p.get("title", "")) == PROJECT_TITLE]
    if len(matches) != 1:
        candidates = ", ".join(f"{p.get('number')}:{p.get('title')}" for p in projects[:20])
        raise RoadmapError(
            f"expected exactly one {PROJECT_TITLE!r} project for {OWNER}; candidates: {candidates}"
        )
    selected = matches[0]
    print(f"Selected Project #{selected['number']}: {selected['title']} ({selected['url']})")
    return selected


def fetch_project(number: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    field_after = None
    item_after = None
    fields: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    meta: dict[str, Any] | None = None

    while True:
        data = graphql(
            PROJECT_QUERY,
            {
                "login": OWNER,
                "number": number,
                "fieldAfter": field_after,
                "itemAfter": item_after,
            },
        )
        project = ((data.get("user") or {}).get("projectV2"))
        if not project:
            raise RoadmapError(f"Project #{number} not found for {OWNER}")
        meta = meta or project
        fields.extend(x for x in (project.get("fields", {}).get("nodes") or []) if x)
        items.extend(x for x in (project.get("items", {}).get("nodes") or []) if x)

        fp = project.get("fields", {}).get("pageInfo") or {}
        ip = project.get("items", {}).get("pageInfo") or {}
        field_after = fp.get("endCursor") if fp.get("hasNextPage") else None
        item_after = ip.get("endCursor") if ip.get("hasNextPage") else None
        if field_after is None and item_after is None:
            break

    assert meta is not None
    return meta, fields, items


def item_key(item: dict[str, Any]) -> tuple[str, int] | None:
    content = item.get("content") or {}
    repo = str(((content.get("repository") or {}).get("nameWithOwner") or "")).strip()
    issue_number = content.get("number")
    if not repo or not isinstance(issue_number, int):
        return None
    return repo.casefold(), issue_number


def issue_node_id(number: int) -> str:
    data = graphql(
        ISSUE_ID_QUERY,
        {"owner": OWNER, "repo": REPOSITORY.split("/", 1)[1], "number": number},
    )
    issue = ((data.get("repository") or {}).get("issue"))
    if not issue or not issue.get("id"):
        raise RoadmapError(f"{REPOSITORY}#{number} not found")
    return str(issue["id"])


def add_item(project_id: str, number: int) -> str:
    data = graphql(
        ADD_ITEM,
        {"projectId": project_id, "contentId": issue_node_id(number)},
    )
    item = ((data.get("addProjectV2ItemById") or {}).get("item") or {})
    if not item.get("id"):
        raise RoadmapError(f"failed to add {REPOSITORY}#{number} to {PROJECT_TITLE}")
    return str(item["id"])


def create_field(
    project_id: str,
    name: str,
    data_type: str,
    options: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    data = graphql(
        CREATE_FIELD,
        {
            "projectId": project_id,
            "name": name,
            "dataType": data_type,
            "singleSelectOptions": options if data_type == "SINGLE_SELECT" else None,
        },
    )
    field = (data.get("createProjectV2Field") or {}).get("projectV2Field") or {}
    if not field.get("id"):
        raise RoadmapError(f"failed to create project field {name!r}")
    print(f"Created {data_type} field {name!r}: {field['id']}")
    return field


def desired_options(names: tuple[str, ...], prefix: str) -> list[dict[str, str]]:
    return [
        {
            "name": name,
            "color": "GRAY",
            "description": f"Educational Roadmap {prefix}: {name}",
        }
        for name in names
    ]


def ensure_single_select_options(field: dict[str, Any], names: tuple[str, ...], prefix: str) -> dict[str, Any]:
    existing = {
        str(option.get("name", "")).casefold(): option
        for option in (field.get("options") or [])
        if option
    }
    options: list[dict[str, str]] = []
    for option in desired_options(names, prefix):
        prior = existing.get(option["name"].casefold())
        if prior and prior.get("id"):
            option["id"] = str(prior["id"])
            option["color"] = str(prior.get("color") or "GRAY").upper()
            option["description"] = str(prior.get("description") or option["description"])
        options.append(option)

    data = graphql(
        UPDATE_FIELD,
        {"fieldId": field["id"], "singleSelectOptions": options},
    )
    updated = (data.get("updateProjectV2Field") or {}).get("projectV2Field") or {}
    if not updated.get("id"):
        raise RoadmapError(f"failed to configure options for field {field.get('name')!r}")
    return updated


def ensure_fields(project_id: str, fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_name = {str(f.get("name", "")).casefold(): f for f in fields}
    select_specs = {
        "Parent Group": (PARENT_GROUPS, "parent group"),
        "Child Group": (CHILD_GROUPS, "child group"),
        "Group": (CHILD_GROUPS, "group"),
    }
    ensured: dict[str, dict[str, Any]] = {}
    for name, data_type in FIELD_SPECS.items():
        field = by_name.get(name.casefold())
        if field is None:
            field = create_field(
                project_id,
                name,
                data_type,
                desired_options(*select_specs[name]) if name in select_specs else None,
            )
        actual = str(field.get("dataType", "")).upper()
        if actual != data_type:
            raise RoadmapError(
                f"project field {name!r} exists as {actual}, expected {data_type}"
            )
        if name in select_specs:
            options, prefix = select_specs[name]
            field = ensure_single_select_options(field, options, prefix)
        ensured[name] = field
    return ensured


def quarter_label(start: dt.date, end: dt.date) -> str:
    start_q = ((start.month - 1) // 3) + 1
    end_q = ((end.month - 1) // 3) + 1
    start_key = (start.year, start_q)
    end_key = (end.year, end_q)
    if start_key == end_key:
        return f"Q{start_q} {start.year}"
    return f"Q{start_q} {start.year} - Q{end_q} {end.year}"


def roadmap_values(number: int) -> tuple[str, str, int, str]:
    start_text, end_text = ROADMAP_PLAN[number]
    start = dt.date.fromisoformat(start_text)
    end = dt.date.fromisoformat(end_text)
    if end < start:
        raise RoadmapError(f"issue #{number} has an end before its start")
    duration = (end - start).days + 1
    return start.isoformat(), end.isoformat(), duration, quarter_label(start, end)


def validate_dependencies() -> None:
    if set(DEPENDENCIES) != set(ISSUES):
        raise RoadmapError(
            f"dependency map must cover exactly the roadmap issues; got {sorted(DEPENDENCIES)}"
        )
    for number, prerequisites in DEPENDENCIES.items():
        if number in prerequisites:
            raise RoadmapError(f"issue #{number} cannot depend on itself")
        for prerequisite in prerequisites:
            if prerequisite not in GROUP_BY_ISSUE:
                raise RoadmapError(
                    f"issue #{number} depends on non-roadmap issue #{prerequisite}"
                )


def roadmap_group(number: int) -> tuple[str, str, str]:
    try:
        return GROUP_BY_ISSUE[number]
    except KeyError as exc:
        raise RoadmapError(f"no group mapping for issue #{number}") from exc


def option_id(field: dict[str, Any], name: str) -> str:
    wanted = name.casefold()
    for option in field.get("options") or []:
        if str(option.get("name", "")).casefold() == wanted and option.get("id"):
            return str(option["id"])
    raise RoadmapError(f"option {name!r} not found in field {field.get('name')!r}")


def label_slug(value: str) -> str:
    chars = [char if char.isalnum() else "-" for char in value.casefold()]
    slug = "".join(chars).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug


def dependency_labels(number: int) -> list[tuple[str, str, str]]:
    labels: list[tuple[str, str, str]] = []
    for prerequisite in DEPENDENCIES[number]:
        parent, child, _ = roadmap_group(prerequisite)
        labels.append(
            (
                f"roadmap-depends-on:{prerequisite}",
                LABEL_COLORS["dependency"],
                (
                    f"Educational Roadmap dependency: #{prerequisite} "
                    f"({parent} / {child})"
                ),
            )
        )
    return labels


def roadmap_labels(number: int) -> list[tuple[str, str, str]]:
    start, end, _, quarter = roadmap_values(number)
    parent, child, group = roadmap_group(number)
    return [
        ("roadmap", LABEL_COLORS["roadmap"], "Educational Roadmap issue"),
        (
            f"roadmap-parent:{label_slug(parent)}",
            LABEL_COLORS["parent"],
            f"Educational Roadmap parent group: {parent}",
        ),
        (
            f"roadmap-child:{group}",
            LABEL_COLORS["child"],
            f"Educational Roadmap child group: {child}",
        ),
        (
            f"roadmap-group:{group}",
            LABEL_COLORS["group"],
            f"Educational Roadmap group: {child}",
        ),
        (
            f"roadmap-quarter:{label_slug(quarter)}",
            LABEL_COLORS["quarter"],
            f"Educational Roadmap quarter: {quarter}",
        ),
        (
            f"roadmap-start:{start}",
            LABEL_COLORS["start"],
            f"Educational Roadmap start date: {start}",
        ),
        (
            f"roadmap-end:{end}",
            LABEL_COLORS["end"],
            f"Educational Roadmap end date: {end}",
        ),
        *dependency_labels(number),
    ]


def ensure_label(name: str, color: str, description: str) -> None:
    encoded = urllib.parse.quote(name, safe="")
    path = f"/repos/{REPOSITORY}/labels/{encoded}"
    try:
        rest(path)
    except RoadmapError as exc:
        if "HTTP 404" not in str(exc):
            raise
        rest(
            f"/repos/{REPOSITORY}/labels",
            method="POST",
            payload={"name": name, "color": color, "description": description},
        )
        return
    rest(
        path,
        method="PATCH",
        payload={"new_name": name, "color": color, "description": description},
    )


def sync_issue_labels(number: int) -> None:
    expected = roadmap_labels(number)
    for name, color, description in expected:
        ensure_label(name, color, description)

    current = rest(f"/repos/{REPOSITORY}/issues/{number}/labels?per_page=100")
    current_names = {str(label.get("name", "")) for label in (current or [])}
    managed = lambda value: value == "roadmap" or value.startswith("roadmap-")
    preserved = sorted(name for name in current_names if not managed(name))
    desired = preserved + sorted(name for name, _, _ in expected)
    rest(
        f"/repos/{REPOSITORY}/issues/{number}/labels",
        method="PUT",
        payload={"labels": desired},
    )

    verified = rest(f"/repos/{REPOSITORY}/issues/{number}/labels?per_page=100")
    verified_names = {str(label.get("name", "")) for label in (verified or [])}
    missing = [name for name, _, _ in expected if name not in verified_names]
    if missing:
        raise RoadmapError(f"issue #{number} label verification failed; missing: {missing}")


def set_field(project_id: str, item_id: str, field: dict[str, Any], value: dict[str, Any]) -> None:
    graphql(
        SET_FIELD,
        {
            "projectId": project_id,
            "itemId": item_id,
            "fieldId": field["id"],
            "value": value,
        },
    )


def reconcile(dry_run: bool) -> None:
    validate_dependencies()
    project = choose_project()
    project_id = str(project["id"])
    project_number = int(project["number"])

    _, fields, items = fetch_project(project_number)
    existing = {
        key: item
        for item in items
        if (key := item_key(item)) is not None and key[0] == REPOSITORY.casefold()
    }

    missing = [n for n in ISSUES if (REPOSITORY.casefold(), n) not in existing]
    print(f"Educational Roadmap currently contains {len(existing)} PASI issue items.")
    print(f"Missing roadmap items: {missing or 'none'}")

    if dry_run:
        for n in ISSUES:
            start, end, duration, quarter = roadmap_values(n)
            parent, child, group = roadmap_group(n)
            print(
                f"#{n}: {start} -> {end} ({duration} days; {quarter}; "
                f"{parent} / {child}; group={group})"
            )
        return

    for n in missing:
        add_item(project_id, n)

    _, fields, _ = fetch_project(project_number)
    fields_by_name = ensure_fields(project_id, fields)

    _, _, items = fetch_project(project_number)
    canonical = {
        item_key(item): item
        for item in items
        if item_key(item) is not None and item_key(item)[0] == REPOSITORY.casefold()
    }

    for n in ISSUES:
        item = canonical.get((REPOSITORY.casefold(), n))
        if not item:
            raise RoadmapError(f"project item missing after reconciliation: {REPOSITORY}#{n}")

        start, end, duration, quarter = roadmap_values(n)
        parent, child, group = roadmap_group(n)

        set_field(project_id, str(item["id"]), fields_by_name["Start Date"], {"date": start})
        set_field(project_id, str(item["id"]), fields_by_name["End Date"], {"date": end})
        set_field(
            project_id,
            str(item["id"]),
            fields_by_name["Duration (days)"],
            {"number": duration},
        )
        set_field(project_id, str(item["id"]), fields_by_name["Quarter"], {"text": quarter})
        set_field(
            project_id,
            str(item["id"]),
            fields_by_name["Parent Group"],
            {"singleSelectOptionId": option_id(fields_by_name["Parent Group"], parent)},
        )
        set_field(
            project_id,
            str(item["id"]),
            fields_by_name["Child Group"],
            {"singleSelectOptionId": option_id(fields_by_name["Child Group"], child)},
        )
        set_field(
            project_id,
            str(item["id"]),
            fields_by_name["Group"],
            {"singleSelectOptionId": option_id(fields_by_name["Group"], child)},
        )
        sync_issue_labels(n)

    _, final_fields, final_items = fetch_project(project_number)
    final_fields_by_name = {
        str(f.get("name", "")).casefold(): f for f in final_fields
    }

    for field_name, expected_type in FIELD_SPECS.items():
        field = final_fields_by_name.get(field_name.casefold())
        if not field or str(field.get("dataType", "")).upper() != expected_type:
            raise RoadmapError(f"final verification failed for field {field_name!r}")

        if expected_type == "SINGLE_SELECT":
            expected_options = PARENT_GROUPS if field_name == "Parent Group" else CHILD_GROUPS
            actual_options = {
                str(option.get("name", "")).casefold()
                for option in (field.get("options") or [])
            }
            missing_options = [
                name for name in expected_options if name.casefold() not in actual_options
            ]
            if missing_options:
                raise RoadmapError(
                    f"final verification missing {field_name!r} options: {missing_options}"
                )

    verified = 0
    for n in ISSUES:
        item = next(
            (
                candidate
                for candidate in final_items
                if item_key(candidate) == (REPOSITORY.casefold(), n)
            ),
            None,
        )
        if not item:
            raise RoadmapError(f"final verification missing {REPOSITORY}#{n}")

        start, end, duration, quarter = roadmap_values(n)
        parent, child, group = roadmap_group(n)
        actual = (
            (item.get("startDate") or {}).get("date"),
            (item.get("endDate") or {}).get("date"),
            (item.get("duration") or {}).get("number"),
            (item.get("quarter") or {}).get("text"),
            (item.get("parentGroup") or {}).get("name"),
            (item.get("childGroup") or {}).get("name"),
            (item.get("group") or {}).get("name"),
        )
        expected = (start, end, float(duration), quarter, parent, child, child)
        if actual != expected:
            raise RoadmapError(
                f"verification mismatch for {REPOSITORY}#{n}: got {actual!r}, expected {expected!r}"
            )

        expected_labels = {name for name, _, _ in roadmap_labels(n)}
        actual_labels = {
            str(label.get("name", ""))
            for label in (rest(f"/repos/{REPOSITORY}/issues/{n}/labels?per_page=100") or [])
        }
        if not expected_labels.issubset(actual_labels):
            missing_labels = sorted(expected_labels - actual_labels)
            raise RoadmapError(
                f"issue label verification failed for {REPOSITORY}#{n}: {missing_labels}"
            )
        dependency_labels_expected = {
            name for name, _, _ in dependency_labels(n)
        }
        dependency_labels_actual = {
            name for name in actual_labels if name.startswith("roadmap-depends-on:")
        }
        if dependency_labels_actual != dependency_labels_expected:
            raise RoadmapError(
                f"dependency verification failed for {REPOSITORY}#{n}: "
                f"got {sorted(dependency_labels_actual)}, expected {sorted(dependency_labels_expected)}"
            )
        verified += 1

    print("SYNC PASS")
    print(f"Project: {project['url']}")
    print(f"Project items verified: {verified}/{len(ISSUES)}")
    print(
        "Fields: Start Date [DATE], End Date [DATE], Duration (days) [NUMBER], "
        "Quarter [TEXT], Parent Group [SINGLE_SELECT], Child Group [SINGLE_SELECT], "
        "Group [SINGLE_SELECT]"
    )
    print("Issue labels verified: roadmap + parent + child + group + quarter + start + end + dependencies")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        reconcile(args.dry_run)
    except (RoadmapError, ValueError) as exc:
        print(f"Educational Roadmap reconciliation failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
