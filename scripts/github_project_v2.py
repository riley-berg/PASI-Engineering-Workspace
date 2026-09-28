#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

GRAPHQL_URL = "https://api.github.com/graphql"
API_VERSION = "2022-11-28"

PROJECT_FRAGMENT = """
fragment ProjectDetails on ProjectV2 {
  id
  number
  title
  url
  fields(first: 100, after: $fieldAfter) {
    pageInfo { hasNextPage endCursor }
    nodes {
      __typename
      id
      name
      dataType
      ... on ProjectV2SingleSelectField {
        options { id name }
      }
      ... on ProjectV2IterationField {
        configuration {
          iterations {
            id
            title
            startDate
            duration
          }
          completedIterations {
            id
            title
            startDate
            duration
          }
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
        }
        ... on PullRequest {
          number
          title
          repository { nameWithOwner }
        }
      }
    }
  }
}
"""

PROJECT_QUERY_USER = """
query ProjectDetails($login: String!, $number: Int!, $fieldAfter: String, $itemAfter: String) {
  user(login: $login) {
    projectV2(number: $number) {
      __PROJECT_FRAGMENT__
    }
  }
}
""".replace("__PROJECT_FRAGMENT__", PROJECT_FRAGMENT)

PROJECT_QUERY_ORGANIZATION = """
query ProjectDetails($login: String!, $number: Int!, $fieldAfter: String, $itemAfter: String) {
  organization(login: $login) {
    projectV2(number: $number) {
      __PROJECT_FRAGMENT__
    }
  }
}
""".replace("__PROJECT_FRAGMENT__", PROJECT_FRAGMENT)

PROJECT_QUERY_REPOSITORY = """
query ProjectDetails($owner: String!, $repo: String!, $number: Int!, $fieldAfter: String, $itemAfter: String) {
  repository(owner: $owner, name: $repo) {
    projectV2(number: $number) {
      __PROJECT_FRAGMENT__
    }
  }
}
""".replace("__PROJECT_FRAGMENT__", PROJECT_FRAGMENT)

UPDATE_ITEM_FIELD_MUTATION = """
mutation UpdateProjectItemField($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
  updateProjectV2ItemFieldValue(
    input: {
      projectId: $projectId
      itemId: $itemId
      fieldId: $fieldId
      value: $value
    }
  ) {
    projectV2Item { id }
  }
}
"""


class ProjectV2Error(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectContext:
    project_id: str
    project_number: int
    project_title: str
    project_url: str
    fields: tuple[dict[str, Any], ...]
    items: tuple[dict[str, Any], ...]


def token_from_environment() -> str:
    for name in ("PASI_PROJECTS_TOKEN", "PASI_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise ProjectV2Error(
        "set PASI_PROJECTS_TOKEN, PASI_GITHUB_TOKEN, GH_TOKEN, or GITHUB_TOKEN"
    )


def graphql_request(query: str, variables: dict[str, Any], *, token: str) -> dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        GRAPHQL_URL,
        method="POST",
        data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "pasi-engineering-workspace-project-v2",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise ProjectV2Error(f"GitHub GraphQL HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProjectV2Error(f"GitHub GraphQL request failed: {exc}") from exc

    errors = body.get("errors")
    if errors:
        messages = "; ".join(str(error.get("message", error)) for error in errors)
        raise ProjectV2Error(f"GitHub GraphQL error: {messages}")
    data = body.get("data")
    if not isinstance(data, dict):
        raise ProjectV2Error("GitHub GraphQL response did not contain an object in data")
    return data


def _project_from_data(data: dict[str, Any], owner_type: str) -> dict[str, Any] | None:
    if owner_type == "user":
        return ((data.get("user") or {}).get("projectV2"))
    if owner_type == "organization":
        return ((data.get("organization") or {}).get("projectV2"))
    return ((data.get("repository") or {}).get("projectV2"))


def fetch_project(
    *,
    owner: str,
    owner_type: str,
    project_number: int,
    token: str,
) -> ProjectContext:
    owner_type = owner_type.lower().strip()
    field_cursor: str | None = None
    item_cursor: str | None = None
    fields: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    project_meta: dict[str, Any] | None = None

    while project_meta is None or field_cursor is not None or item_cursor is not None:
        variables: dict[str, Any] = {
            "number": project_number,
            "fieldAfter": field_cursor,
            "itemAfter": item_cursor,
        }
        if owner_type == "repository":
            if "/" not in owner:
                raise ProjectV2Error("repository owner must be owner/name")
            repo_owner, repo_name = owner.split("/", 1)
            variables.update({"owner": repo_owner, "repo": repo_name})
            query = PROJECT_QUERY_REPOSITORY
        elif owner_type == "user":
            variables["login"] = owner
            query = PROJECT_QUERY_USER
        elif owner_type == "organization":
            variables["login"] = owner
            query = PROJECT_QUERY_ORGANIZATION
        else:
            raise ProjectV2Error("owner type must be user, organization, or repository")

        data = graphql_request(query, variables, token=token)
        project = _project_from_data(data, owner_type)
        if not project:
            raise ProjectV2Error(
                f"project {project_number} was not found for {owner_type} {owner}"
            )

        if project_meta is None:
            project_meta = {
                "id": project["id"],
                "number": int(project["number"]),
                "title": project["title"],
                "url": project["url"],
            }

        fields.extend(node for node in (project.get("fields", {}).get("nodes") or []) if node)
        items.extend(node for node in (project.get("items", {}).get("nodes") or []) if node)

        field_page = project.get("fields", {}).get("pageInfo") or {}
        item_page = project.get("items", {}).get("pageInfo") or {}
        field_cursor = field_page.get("endCursor") if field_page.get("hasNextPage") else None
        item_cursor = item_page.get("endCursor") if item_page.get("hasNextPage") else None

        if field_cursor is None and item_cursor is None:
            break

    return ProjectContext(
        project_id=str(project_meta["id"]),
        project_number=int(project_meta["number"]),
        project_title=str(project_meta["title"]),
        project_url=str(project_meta["url"]),
        fields=tuple(fields),
        items=tuple(items),
    )


def resolve_field(context: ProjectContext, name: str) -> dict[str, Any]:
    wanted = name.strip().casefold()
    matches = [
        field for field in context.fields
        if str(field.get("name", "")).strip().casefold() == wanted
    ]
    if not matches:
        available = ", ".join(
            str(field.get("name", "")) for field in context.fields if field.get("name")
        )
        raise ProjectV2Error(f"field {name!r} not found; available fields: {available}")
    if len(matches) > 1:
        raise ProjectV2Error(f"field {name!r} matched more than one project field")
    return matches[0]


def resolve_item(context: ProjectContext, repository: str, number: int) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    wanted_repo = repository.strip().casefold()
    for item in context.items:
        content = item.get("content") or {}
        repo = ((content.get("repository") or {}).get("nameWithOwner") or "").strip().casefold()
        raw_number = content.get("number")
        if repo == wanted_repo and isinstance(raw_number, int) and raw_number == number:
            matches.append(item)
    if not matches:
        raise ProjectV2Error(
            f"issue/PR {repository}#{number} is not present in the project"
        )
    if len(matches) > 1:
        raise ProjectV2Error(f"issue/PR {repository}#{number} matched multiple items")
    return matches[0]


def resolve_option(field: dict[str, Any], name: str) -> str:
    wanted = name.strip().casefold()
    for option in field.get("options") or []:
        if str(option.get("name", "")).strip().casefold() == wanted:
            return str(option["id"])
    available = ", ".join(
        str(option.get("name", "")) for option in field.get("options") or []
    )
    raise ProjectV2Error(
        f"single-select option {name!r} not found; available options: {available}"
    )


def resolve_iteration(field: dict[str, Any], title: str) -> str:
    configuration = field.get("configuration") or {}
    wanted = title.strip().casefold()
    all_iterations = list(configuration.get("iterations") or []) + list(
        configuration.get("completedIterations") or []
    )
    matches = [
        iteration
        for iteration in all_iterations
        if str(iteration.get("title", "")).strip().casefold() == wanted
    ]
    if not matches:
        available = ", ".join(
            str(iteration.get("title", "")) for iteration in all_iterations
        )
        raise ProjectV2Error(
            f"iteration {title!r} not found; available iterations: {available}"
        )
    if len(matches) > 1:
        raise ProjectV2Error(f"iteration {title!r} matched multiple iterations")
    return str(matches[0]["id"])


def build_value(field: dict[str, Any], kind: str, raw_value: str) -> dict[str, Any]:
    data_type = str(field.get("dataType", "")).upper()
    kind = kind.strip().lower()
    if kind == "auto":
        mapping = {
            "DATE": "date",
            "ITERATION": "iteration",
            "SINGLE_SELECT": "single-select",
            "NUMBER": "number",
            "TEXT": "text",
        }
        kind = mapping.get(data_type, "")
        if not kind:
            raise ProjectV2Error(
                f"cannot auto-select a writable kind for field type {data_type or 'unknown'}"
            )

    if kind == "date":
        try:
            datetime.date.fromisoformat(raw_value)
        except ValueError as exc:
            raise ProjectV2Error("date value must be YYYY-MM-DD") from exc
        return {"date": raw_value}
    if kind == "iteration":
        return {"iterationId": resolve_iteration(field, raw_value)}
    if kind == "single-select":
        return {"singleSelectOptionId": resolve_option(field, raw_value)}
    if kind == "number":
        try:
            return {"number": float(raw_value)}
        except ValueError as exc:
            raise ProjectV2Error("number value must be numeric") from exc
    if kind == "text":
        return {"text": raw_value}
    raise ProjectV2Error(
        "kind must be auto, date, iteration, single-select, number, or text"
    )


def update_item_field(
    context: ProjectContext,
    *,
    item: dict[str, Any],
    field: dict[str, Any],
    value: dict[str, Any],
    token: str,
) -> str:
    variables = {
        "projectId": context.project_id,
        "itemId": str(item["id"]),
        "fieldId": str(field["id"]),
        "value": value,
    }
    data = graphql_request(UPDATE_ITEM_FIELD_MUTATION, variables, token=token)
    result = data.get("updateProjectV2ItemFieldValue") or {}
    project_item = result.get("projectV2Item") or {}
    result_id = project_item.get("id")
    if not result_id:
        raise ProjectV2Error("GraphQL mutation returned no projectV2Item id")
    return str(result_id)


def print_context(context: ProjectContext) -> None:
    print(json.dumps(
        {
            "project": {
                "id": context.project_id,
                "number": context.project_number,
                "title": context.project_title,
                "url": context.project_url,
            },
            "fields": [
                {
                    "id": field.get("id"),
                    "name": field.get("name"),
                    "type": field.get("__typename"),
                    "dataType": field.get("dataType"),
                    "options": field.get("options") or None,
                    "iterations": [
                        {
                            "id": iteration.get("id"),
                            "title": iteration.get("title"),
                            "startDate": iteration.get("startDate"),
                            "duration": iteration.get("duration"),
                        }
                        for iteration in (
                            list((field.get("configuration") or {}).get("iterations") or [])
                            + list((field.get("configuration") or {}).get("completedIterations") or [])
                        )
                    ] or None,
                }
                for field in context.fields
            ],
            "items": [
                {
                    "id": item.get("id"),
                    "type": (item.get("content") or {}).get("__typename"),
                    "number": (item.get("content") or {}).get("number"),
                    "title": (item.get("content") or {}).get("title"),
                    "repository": (
                        ((item.get("content") or {}).get("repository") or {}).get("nameWithOwner")
                    ),
                }
                for item in context.items
            ],
        },
        indent=2,
        sort_keys=True,
    ))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve and update GitHub Projects v2 fields through GraphQL v4."
    )
    parser.add_argument(
        "--owner",
        default="th3-st0v3",
        help="Project owner login, or owner/name for repository projects.",
    )
    parser.add_argument(
        "--owner-type",
        choices=("user", "organization", "repository"),
        default="user",
    )
    parser.add_argument("--project-number", type=int, default=1)
    parser.add_argument(
        "--token-env",
        default="",
        help="Environment variable containing the GitHub token.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "inspect",
        help="Print project, field, option, iteration, and item IDs.",
    )

    update = sub.add_parser(
        "set",
        help="Update one writable ProjectV2 field value.",
    )
    update.add_argument("--item-repo", required=True, help="Repository containing the issue/PR.")
    update.add_argument("--item-number", required=True, type=int)
    update.add_argument("--field", required=True)
    update.add_argument(
        "--kind",
        default="auto",
        choices=("auto", "date", "iteration", "single-select", "number", "text"),
    )
    update.add_argument("--value", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.token_env:
            token = os.environ.get(args.token_env, "").strip()
            if not token:
                raise ProjectV2Error(f"environment variable {args.token_env} is empty")
        else:
            token = token_from_environment()

        context = fetch_project(
            owner=args.owner,
            owner_type=args.owner_type,
            project_number=args.project_number,
            token=token,
        )

        if args.command == "inspect":
            print_context(context)
            return 0

        item = resolve_item(context, args.item_repo, args.item_number)
        field = resolve_field(context, args.field)
        value = build_value(field, args.kind, args.value)
        result_id = update_item_field(
            context,
            item=item,
            field=field,
            value=value,
            token=token,
        )
        print(json.dumps(
            {
                "updated": True,
                "project_id": context.project_id,
                "item_id": item["id"],
                "field_id": field["id"],
                "field": field["name"],
                "field_type": field["__typename"],
                "value": value,
                "result_item_id": result_id,
            },
            indent=2,
            sort_keys=True,
        ))
        return 0
    except ProjectV2Error as exc:
        print(f"PASI project-v2: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
