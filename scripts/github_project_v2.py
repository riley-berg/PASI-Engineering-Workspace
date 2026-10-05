#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

GRAPHQL_URL = "https://api.github.com/graphql"
API_VERSION = "2026-03-10"
CANONICAL_REPO = "riley-berg/PASI"
ALLOWED_ISSUES = [1, *range(19, 36)]
PROJECT_TITLE_HINT = "PASI"


class ProjectV2Error(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectContext:
    project_id: str
    number: int
    title: str
    url: str
    fields: tuple[dict[str, Any], ...]
    items: tuple[dict[str, Any], ...]


def token() -> str:
    for name in ("PASI_PROJECTS_TOKEN", "PASI_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise ProjectV2Error("no GitHub token available")


def request_json(url: str, *, method: str = "GET", payload: bytes | None = None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token()}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "pasi-project-reconciler",
        "Content-Type": "application/json",
    }
    request = urllib.request.Request(url, method=method, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise ProjectV2Error(f"GitHub API HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProjectV2Error(f"GitHub API request failed: {exc}") from exc


def graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables}).encode()
    response = request_json(GRAPHQL_URL, method="POST", payload=payload)
    if response.get("errors"):
        raise ProjectV2Error(
            "GraphQL: " + "; ".join(str(e.get("message", e)) for e in response["errors"])
        )
    data = response.get("data")
    if not isinstance(data, dict):
        raise ProjectV2Error("GraphQL response had no data object")
    return data


PROJECTS_QUERY = """
query UserProjects($login: String!) {
  user(login: $login) {
    projectsV2(first: 100, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        id
        number
        title
        url
        updatedAt
      }
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
          ... on ProjectV2Field {
            id
            name
            dataType
          }
          ... on ProjectV2IterationField {
            id
            name
            configuration {
              iterations { id title startDate duration }
              completedIterations { id title startDate duration }
            }
          }
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
              state
            }
            ... on PullRequest {
              number
              title
              repository { nameWithOwner }
              state
            }
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
mutation Add($projectId: ID!, $contentId: ID!) {
  addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
    item { id }
  }
}
"""

DELETE_ITEM = """
mutation Delete($projectId: ID!, $itemId: ID!) {
  deleteProjectV2Item(input: {projectId: $projectId, itemId: $itemId}) {
    deletedItemId
  }
}
"""

SET_FIELD = """
mutation SetField($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
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


def choose_project(login: str, title_hint: str = PROJECT_TITLE_HINT) -> dict[str, Any]:
    data = graphql(PROJECTS_QUERY, {"login": login})
    projects = ((data.get("user") or {}).get("projectsV2") or {}).get("nodes") or []
    if not projects:
        raise ProjectV2Error(f"no Projects v2 found for {login}")

    ranked = []
    for project in projects:
        title = str(project.get("title", ""))
        lower = title.casefold()
        score = 0
        if title_hint.casefold() in lower:
            score += 100
        if "engineering workspace" in lower:
            score -= 100
        if "roadmap" in lower:
            score += 10
        ranked.append((score, str(project.get("updatedAt", "")), project))

    ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
    score, _, selected = ranked[0]
    if score < 100:
        raise ProjectV2Error(
            "could not uniquely identify the new PASI Project; candidates: "
            + ", ".join(f"{p.get('number')}:{p.get('title')}" for _, _, p in ranked[:10])
        )
    print(f"Selected Project #{selected['number']}: {selected['title']} ({selected['url']})")
    return selected


def fetch_project(login: str, number: int) -> ProjectContext:
    field_after = None
    item_after = None
    fields: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    meta: dict[str, Any] | None = None

    while meta is None or field_after is not None or item_after is not None:
        data = graphql(
            PROJECT_QUERY,
            {
                "login": login,
                "number": number,
                "fieldAfter": field_after,
                "itemAfter": item_after,
            },
        )
        project = ((data.get("user") or {}).get("projectV2"))
        if not project:
            raise ProjectV2Error(f"Project #{number} not found for {login}")

        if meta is None:
            meta = project

        fields.extend(x for x in (project.get("fields", {}).get("nodes") or []) if x)
        items.extend(x for x in (project.get("items", {}).get("nodes") or []) if x)

        fp = project.get("fields", {}).get("pageInfo") or {}
        ip = project.get("items", {}).get("pageInfo") or {}
        field_after = fp.get("endCursor") if fp.get("hasNextPage") else None
        item_after = ip.get("endCursor") if ip.get("hasNextPage") else None

        if field_after is None and item_after is None:
            break

    assert meta is not None
    return ProjectContext(
        project_id=str(meta["id"]),
        number=int(meta["number"]),
        title=str(meta["title"]),
        url=str(meta["url"]),
        fields=tuple(fields),
        items=tuple(items),
    )


def item_key(item: dict[str, Any]) -> tuple[str, int] | None:
    content = item.get("content") or {}
    repo = str(((content.get("repository") or {}).get("nameWithOwner") or "")).strip()
    number = content.get("number")
    if not repo or not isinstance(number, int):
        return None
    return repo.casefold(), number


def issue_node_id(number: int) -> str:
    owner, repo = CANONICAL_REPO.split("/", 1)
    data = graphql(ISSUE_ID_QUERY, {"owner": owner, "repo": repo, "number": number})
    issue = ((data.get("repository") or {}).get("issue"))
    if not issue or not issue.get("id"):
        raise ProjectV2Error(f"{CANONICAL_REPO}#{number} not found")
    return str(issue["id"])


def add_item(project: ProjectContext, number: int) -> None:
    data = graphql(ADD_ITEM, {"projectId": project.project_id, "contentId": issue_node_id(number)})
    if not ((data.get("addProjectV2ItemById") or {}).get("item") or {}).get("id"):
        raise ProjectV2Error(f"failed to add {CANONICAL_REPO}#{number}")


def delete_item(project: ProjectContext, item_id: str) -> None:
    data = graphql(DELETE_ITEM, {"projectId": project.project_id, "itemId": item_id})
    if not (data.get("deleteProjectV2Item") or {}).get("deletedItemId"):
        raise ProjectV2Error(f"failed to remove Project item {item_id}")


def ensure_date_field(project: ProjectContext, name: str) -> dict[str, Any]:
    wanted = name.casefold()
    for field in project.fields:
        if str(field.get("name", "")).casefold() == wanted:
            if str(field.get("dataType", "")).upper() != "DATE":
                raise ProjectV2Error(
                    f"Project field {field.get('name')} exists but is not DATE"
                )
            return field

    owner = "riley-berg"
    url = f"https://api.github.com/users/{owner}/projectsV2/{project.number}/fields"
    payload = json.dumps({"name": name, "data_type": "date"}).encode()
    created = request_json(url, method="POST", payload=payload)
    print(f"Created DATE field {name!r}: {created.get('id')}")
    refreshed = fetch_project(owner, project.number)
    for field in refreshed.fields:
        if str(field.get("name", "")).casefold() == wanted:
            if str(field.get("dataType", "")).upper() != "DATE":
                raise ProjectV2Error(f"new field {name} was not DATE")
            return field
    raise ProjectV2Error(f"DATE field {name!r} was not visible after creation")


def set_date(project: ProjectContext, item_id: str, field: dict[str, Any], value: str) -> None:
    dt.date.fromisoformat(value)
    graphql(
        SET_FIELD,
        {
            "projectId": project.project_id,
            "itemId": item_id,
            "fieldId": field["id"],
            "value": {"date": value},
        },
    )


DATE_RANGES = {
    19: ("2026-10-05", "2026-10-18"),
    20: ("2026-10-19", "2026-11-08"),
    21: ("2026-11-09", "2026-11-29"),
    22: ("2026-11-30", "2026-12-20"),
    23: ("2026-12-21", "2027-01-10"),
    24: ("2027-01-11", "2027-01-31"),
    25: ("2027-02-01", "2027-02-21"),
    26: ("2027-02-22", "2027-03-14"),
    27: ("2027-03-15", "2027-04-11"),
    28: ("2027-04-12", "2027-05-02"),
    29: ("2027-05-03", "2027-06-13"),
    30: ("2027-06-14", "2027-07-11"),
    31: ("2027-07-12", "2027-08-08"),
    32: ("2027-08-09", "2027-09-05"),
    33: ("2027-09-06", "2027-09-26"),
    34: ("2027-09-27", "2027-10-17"),
    35: ("2027-10-18", "2027-11-14"),
}


def reconcile(login: str, dry_run: bool) -> None:
    selected = choose_project(login)
    project = fetch_project(login, int(selected["number"]))

    allowed = {(CANONICAL_REPO.casefold(), n) for n in ALLOWED_ISSUES}
    existing = {key: item for item in project.items if (key := item_key(item)) is not None}

    remove = [item for key, item in existing.items() if key not in allowed]
    add = [n for n in ALLOWED_ISSUES if (CANONICAL_REPO.casefold(), n) not in existing]

    print(f"Project contains {len(project.items)} items.")
    print(f"Will remove {len(remove)} non-canonical items and add {len(add)} canonical items.")

    if dry_run:
        for item in remove:
            content = item.get("content") or {}
            print("REMOVE", item["id"], ((content.get("repository") or {}).get("nameWithOwner")), content.get("number"), content.get("title"))
        for n in add:
            print("ADD", f"{CANONICAL_REPO}#{n}")
        return

    for item in remove:
        delete_item(project, str(item["id"]))

    # Re-fetch after deletions so item IDs/fields are current.
    project = fetch_project(login, project.number)

    for n in add:
        add_item(project, n)

    # Re-fetch once more so all canonical items have stable IDs.
    project = fetch_project(login, project.number)
    start_field = ensure_date_field(project, "Start date")
    project = fetch_project(login, project.number)
    target_field = ensure_date_field(project, "Target date")

    canonical = {
        item_key(item): item
        for item in project.items
        if item_key(item) is not None and item_key(item)[0] == CANONICAL_REPO.casefold()
    }

    for n, (start, end) in DATE_RANGES.items():
        item = canonical.get((CANONICAL_REPO.casefold(), n))
        if not item:
            raise ProjectV2Error(f"canonical Project item missing after sync: {CANONICAL_REPO}#{n}")
        set_date(project, str(item["id"]), start_field, start)
        set_date(project, str(item["id"]), target_field, end)

    final = fetch_project(login, project.number)
    final_keys = sorted(key for item in final.items if (key := item_key(item)) is not None)
    expected = sorted(allowed)
    if final_keys != expected:
        raise ProjectV2Error(
            f"Project reconciliation verification failed. Expected {len(expected)} items, found {len(final_keys)}."
        )

    print("SYNC PASS")
    print(f"Project: {final.url}")
    print(f"Items: {len(final.items)}")
    print("Allowed: riley-berg/PASI#1 and #19-#35")
    print("Date fields: Start date [DATE], Target date [DATE]")
    print("Roadmap dates populated for T1-T17.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", default="riley-berg")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        reconcile(args.login, args.dry_run)
    except (ProjectV2Error, ValueError) as exc:
        print(f"PASI project reconciliation failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
