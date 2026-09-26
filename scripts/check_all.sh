#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

trap 'status=$?; printf "\nVALIDATION FAILED (exit %s)\n" "$status" >&2; exit "$status"' ERR

bash -n "$SCRIPT_DIR/check_all.sh"

if [[ -f "$REPO_ROOT/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    mapfile -d '' TRACKED_FILES < <(git ls-files -z)
else
    mapfile -d '' TRACKED_FILES < <(find . -type d \( -name .git -o -name .venv -o -name venv -o -name __pycache__ \) -prune -o -type f -print0 | sort -z)
fi

PYTHON_FILES=()
MARKDOWN_FILES=()
SHELL_FILES=()
JSON_FILES=()

for file in "${TRACKED_FILES[@]}"; do
    case "$file" in
        *.py) PYTHON_FILES+=("./$file") ;;
        *.md) MARKDOWN_FILES+=("./$file") ;;
        *.sh) SHELL_FILES+=("./$file") ;;
        *.json) JSON_FILES+=("./$file") ;;
    esac
done

printf '=== PASI ENGINEERING WORKSPACE AUTHORITATIVE TEST ===\n'
printf 'Python files: %d\n' "${#PYTHON_FILES[@]}"
printf 'Markdown files: %d\n' "${#MARKDOWN_FILES[@]}"
printf 'Shell files: %d\n' "${#SHELL_FILES[@]}"
printf 'JSON files: %d\n' "${#JSON_FILES[@]}"

printf '\n==> Dependency consistency\n'
python -m pip check

printf '\n==> Python tests: warnings are failures\n'
python -W error -m pytest -q --junitxml=.runtime/pytest.xml

python - <<'PY'
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

path = Path(".runtime/pytest.xml")
root = ET.parse(path).getroot()
failures = int(root.attrib.get("failures", "0"))
errors = int(root.attrib.get("errors", "0"))
print(f"Pytest result: {failures} failures, {errors} errors")
if failures or errors:
    raise SystemExit(1)
PY

printf '\n==> Pylance-compatible static analysis (Pyright)\n'
report="$(mktemp)"
cleanup() {
    rm -f "$report"
}
trap cleanup EXIT

npx --yes pyright@1.1.411 --outputjson "${PYTHON_FILES[@]}" >"$report" || {
    cat "$report"
    exit 1
}

python - "$report" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
diagnostics = payload.get("generalDiagnostics", [])
counts = {"error": 0, "warning": 0, "information": 0}
for diagnostic in diagnostics:
    severity = diagnostic.get("severity")
    if severity in counts:
        counts[severity] += 1

print(
    "Pylance-compatible diagnostics: "
    f"{counts['error']} errors, "
    f"{counts['warning']} warnings, "
    f"{counts['information']} informations"
)
if counts != {"error": 0, "warning": 0, "information": 0}:
    raise SystemExit(1)
PY

printf '\n==> Markdown lint\n'
npx --yes markdownlint-cli2@0.23.2 \
    "${MARKDOWN_FILES[@]}"

printf '\n==> Shell syntax\n'
for file in "${SHELL_FILES[@]}"; do
    bash -n "$file"
done

printf '\n==> JSON syntax\n'
python - "${JSON_FILES[@]}" <<'PY'
import json
import sys
from pathlib import Path

paths = [Path(value) for value in sys.argv[1:] if value]
for path in paths:
    with path.open(encoding="utf-8") as handle:
        json.load(handle)
print(f"Validated {len(paths)} JSON files")
PY

printf '\nALL AUTHORITATIVE VALIDATION PASSED: 0 errors, 0 warnings, 0 failed tests\n'
