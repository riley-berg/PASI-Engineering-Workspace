from __future__ import annotations

import unittest

from scripts.educational_roadmap_project_v2 import (
    CHILD_GROUPS,
    DEPENDENCIES,
    FIELD_SPECS,
    GROUP_BY_ISSUE,
    GROUP_OPTIONS,
    PARENT_GROUPS,
    ROADMAP_PLAN,
    roadmap_labels,
    roadmap_values,
)


class TestEducationalRoadmapReconciliation(unittest.TestCase):
    def test_field_contract_contains_schedule_and_hierarchy(self) -> None:
        self.assertEqual(
            FIELD_SPECS,
            {
                "Start Date": "DATE",
                "End Date": "DATE",
                "Duration (days)": "NUMBER",
                "Quarter": "TEXT",
                "Parent Group": "SINGLE_SELECT",
                "Child Group": "SINGLE_SELECT",
                "Group": "SINGLE_SELECT",
            },
        )

    def test_every_roadmap_issue_has_exactly_one_parent_child_group(self) -> None:
        self.assertEqual(set(GROUP_BY_ISSUE), set(range(37, 48)))
        for number, (parent, child, group) in GROUP_BY_ISSUE.items():
            self.assertIn(parent, PARENT_GROUPS)
            self.assertIn(child, CHILD_GROUPS)
            self.assertIn(group, GROUP_OPTIONS)

    def test_common_work_shares_one_all_paths_group_and_major_strategies_are_separate(self) -> None:
        common = [37, 38, 39, 45, 47]
        branches = [40, 41, 42, 43, 44]
        self.assertTrue(all(GROUP_BY_ISSUE[n][0] == "All Paths" for n in common))
        self.assertTrue(
            all(GROUP_BY_ISSUE[n][2] == "All Paths — Shared Strategy" for n in common)
        )
        self.assertEqual(
            [GROUP_BY_ISSUE[n][0] for n in branches],
            [
                "Computer Science",
                "Computer Engineering",
                "Electrical Engineering",
                "Mechanical Engineering / Robotics",
                "Mechatronics / Automation",
            ],
        )
        self.assertTrue(
            all(GROUP_BY_ISSUE[n][1] == "Optimized Major Strategy" for n in branches)
        )
        self.assertEqual(len({GROUP_BY_ISSUE[n][2] for n in branches}), 5)

    def test_dates_and_quarter_are_deterministic(self) -> None:
        start, end, duration, quarter = roadmap_values(37)
        self.assertEqual((start, end), ROADMAP_PLAN[37])
        self.assertEqual(duration, 322)
        self.assertEqual(quarter, "Q4 2026 - Q3 2027")

    def test_dependency_graph_matches_roadmap_hierarchy(self) -> None:
        self.assertEqual(DEPENDENCIES[37], ())
        self.assertEqual(DEPENDENCIES[38], ())
        self.assertEqual(DEPENDENCIES[39], (37, 38))
        for number in (40, 41, 42, 43, 44, 45):
            self.assertEqual(DEPENDENCIES[number], (39,))
        self.assertEqual(DEPENDENCIES[46], (39, 40, 41, 42, 43, 44))
        self.assertEqual(DEPENDENCIES[47], (45, 46))
        for number in (37, 38, 39, 45, 47):
            self.assertEqual(GROUP_BY_ISSUE[number][0], "All Paths")
            self.assertEqual(GROUP_BY_ISSUE[number][2], "All Paths — Shared Strategy")
        for number in (40, 41, 42, 43, 44):
            self.assertEqual(GROUP_BY_ISSUE[number][1], "Optimized Major Strategy")

        for number, prerequisites in DEPENDENCIES.items():
            parent, child, _ = GROUP_BY_ISSUE[number]
            self.assertIn(parent, PARENT_GROUPS)
            self.assertIn(child, CHILD_GROUPS)
            for prerequisite in prerequisites:
                self.assertIn(prerequisite, GROUP_BY_ISSUE)
                self.assertNotEqual(number, prerequisite)

    def test_dependency_labels_are_explicit(self) -> None:
        labels = {name for name, _, _ in roadmap_labels(46)}
        self.assertIn("roadmap-depends-on:39", labels)
        self.assertIn("roadmap-depends-on:40", labels)
        self.assertIn("roadmap-depends-on:44", labels)

    def test_issue_labels_mirror_the_roadmap_metadata(self) -> None:
        labels = {name for name, _, _ in roadmap_labels(40)}
        self.assertIn("roadmap", labels)
        self.assertIn("roadmap-parent:computer-science", labels)
        self.assertIn("roadmap-child:optimized-major-strategy", labels)
        self.assertIn("roadmap-group:computer-science-optimized", labels)
        shared_labels = {name for name, _, _ in roadmap_labels(37)}
        self.assertIn("roadmap-group:all-paths-shared-strategy", shared_labels)
        self.assertIn("roadmap-quarter:q3-2027-q1-2029", labels)
        self.assertIn("roadmap-start:2027-08-23", labels)
        self.assertIn("roadmap-end:2029-01-31", labels)

    def test_child_label_stays_within_github_label_name_budget(self) -> None:
        for number in range(37, 48):
            for name, _, _ in roadmap_labels(number):
                self.assertLessEqual(len(name), 50, f"{number}: {name}")


if __name__ == "__main__":
    unittest.main()
