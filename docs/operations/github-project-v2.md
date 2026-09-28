# GitHub Projects v2 automation

PASI Engineering Workspace owns its roadmap in GitHub Projects v2. This repository contains a small GraphQL v4 client for resolving the internal IDs GitHub requires before a ProjectV2 item field can be updated.

## Authentication

Set a token in one of these environment variables:

- PASI_PROJECTS_TOKEN
- PASI_GITHUB_TOKEN
- GH_TOKEN
- GITHUB_TOKEN

For local administration, prefer a token with the minimum repository/project permissions required. The workflow .github/workflows/github-project-v2.yml uses PASI_PROJECTS_TOKEN when configured and otherwise falls back to the workflow token.

## Inspect the canonical Project

The current roadmap project is user-owned at project number 1:

    export PASI_PROJECTS_TOKEN="..."
    python scripts/github_project_v2.py       --owner th3-st0v3       --owner-type user       --project-number 1       inspect

The inspection resolves:

- the ProjectV2 node ID
- every field node ID and field type
- single-select option IDs
- active and completed iteration IDs
- ProjectV2 item node IDs for Issues and Pull Requests

## Update a field

Resolve the issue/PR by repository + number and the field by its human-readable name:

    python scripts/github_project_v2.py       --owner th3-st0v3       --owner-type user       --project-number 1       set       --item-repo th3-st0v3/PASI-Engineering-Workspace       --item-number 108       --field "Start Date"       --kind date       --value 2026-10-04

Single-select and iteration fields take their display names; the tool resolves the corresponding option or iteration ID before sending the mutation.

## GitHub Actions

Use Actions -> github-project-v2 -> Run workflow to perform a controlled update. The workflow is manual by design so field mappings cannot silently change roadmap state on an arbitrary event.

For unattended board synchronization, create a repository secret named PASI_PROJECTS_TOKEN with the required Projects write access and keep the field names/options in the workflow invocation or a repository configuration file.

## API contract

The client uses POST https://api.github.com/graphql with Authorization: Bearer ... and the updateProjectV2ItemFieldValue mutation. GitHub currently supports date, iteration, single-select, multi-select, text, and number values for that mutation. Assignees, labels, milestones, and repository are issue/PR properties and must be managed through their issue/PR mutations.
