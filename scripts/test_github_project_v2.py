from __future__ import annotations

import unittest

from scripts.github_project_v2 import (
    ProjectContext,
    build_value,
    resolve_field,
    resolve_iteration,
    resolve_item,
    resolve_option,
)


class TestProjectV2Resolution(unittest.TestCase):
    def setUp(self) -> None:
        self.context = ProjectContext(
            project_id="PVT_test",
            project_number=1,
            project_title="PASI",
            project_url="https://github.com/users/th3-st0v3/projects/1",
            fields=(
                {
                    "__typename": "ProjectV2Field",
                    "id": "PVTF_status",
                    "name": "Status",
                    "dataType": "SINGLE_SELECT",
                    "options": [
                        {"id": "opt_todo", "name": "Todo"},
                        {"id": "opt_done", "name": "Done"},
                    ],
                },
                {
                    "__typename": "ProjectV2SingleSelectField",
                    "id": "PVTF_phase",
                    "name": "Phase",
                    "dataType": "SINGLE_SELECT",
                    "options": [
                        {"id": "opt_p0", "name": "P0"},
                        {"id": "opt_p1", "name": "P1"},
                    ],
                },
                {
                    "__typename": "ProjectV2IterationField",
                    "id": "PVTF_iteration",
                    "name": "Iteration",
                    "dataType": "ITERATION",
                    "configuration": {
                        "iterations": [
                            {
                                "id": "it_p0",
                                "title": "P0",
                                "startDate": "2026-09-22",
                                "duration": 13,
                            }
                        ],
                        "completedIterations": [],
                    },
                },
                {
                    "__typename": "ProjectV2Field",
                    "id": "PVTF_start",
                    "name": "Start Date",
                    "dataType": "DATE",
                },
            ),
            items=(
                {
                    "id": "PVTI_1",
                    "content": {
                        "__typename": "Issue",
                        "number": 108,
                        "title": "P0",
                        "repository": {
                            "nameWithOwner": "th3-st0v3/PASI-Engineering-Workspace"
                        },
                    },
                },
            ),
        )

    def test_field_lookup_is_case_insensitive(self) -> None:
        self.assertEqual(resolve_field(self.context, "status")["id"], "PVTF_status")

    def test_single_select_resolves_option_id(self) -> None:
        field = resolve_field(self.context, "Phase")
        self.assertEqual(resolve_option(field, "p1"), "opt_p1")
        self.assertEqual(build_value(field, "auto", "p0"), {"singleSelectOptionId": "opt_p0"})

    def test_iteration_resolves_active_iteration_id(self) -> None:
        field = resolve_field(self.context, "Iteration")
        self.assertEqual(resolve_iteration(field, "P0"), "it_p0")
        self.assertEqual(build_value(field, "iteration", "P0"), {"iterationId": "it_p0"})

    def test_date_is_iso_validated(self) -> None:
        field = resolve_field(self.context, "Start Date")
        self.assertEqual(
            build_value(field, "auto", "2026-10-04"),
            {"date": "2026-10-04"},
        )
        with self.assertRaisesRegex(Exception, "YYYY-MM-DD"):
            build_value(field, "date", "10/04/2026")

    def test_number_and_text_are_supported(self) -> None:
        field = {
            "__typename": "ProjectV2Field",
            "id": "PVTF",
            "name": "Progress",
            "dataType": "NUMBER",
        }
        self.assertEqual(build_value(field, "number", "2.5"), {"number": 2.5})
        self.assertEqual(
            build_value({**field, "dataType": "TEXT"}, "text", "hello"),
            {"text": "hello"},
        )

    def test_item_lookup_requires_repository_and_issue_number(self) -> None:
        item = resolve_item(
            self.context,
            "th3-st0v3/PASI-Engineering-Workspace",
            108,
        )
        self.assertEqual(item["id"], "PVTI_1")


if __name__ == "__main__":
    unittest.main()
