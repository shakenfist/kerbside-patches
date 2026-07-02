#!/bin/bash

# Auto-fix ansible-lint errors in kerbside patches using Claude Code.
#
# This script applies patches, runs ansible-lint via tox, and if errors
# are found, uses Claude Code to fix the patch files in _patches/.
#
# The key challenge is that lint errors reference files in the patched
# source tree, but fixes must be made to patch files in _patches/.
# The prompt guides Claude through this indirection.
#
# Usage:
#   tools/fix-lint-with-claude.sh [options] [project1 project2 ...]
#
# Options:
#   --no-push           Fix and commit but don't push
#   --no-commit         Fix but don't commit or push
#   --max-turns N       Maximum Claude turns (default: 50)
#   --interactive       Run Claude in interactive mode (default: headless)
#   --ci                CI mode: machine-readable output, no colors
#   --output-dir DIR    Directory for output files (default: temp dir)
#   --help              Show this help message
#
# If no projects specified, checks all kolla-ansible-* projects.
#
# Exit codes:
#   0 - Lint passed (no errors) or fixes were committed and pushed
#   1 - Lint errors found and could not be fixed
#   2 - Lint errors fixed but push failed
#   3 - Patches failed to apply (not a lint issue)

set -e

topdir=$(cd "$(dirname "$0")/.." && pwd)
cd "${topdir}"

# Default options
do_push=true
do_commit=true
max_turns=50
interactive=false
ci_mode=false
output_dir=""
projects=""

# Colors for output (disabled in CI mode)
setup_colors() {
    if [ "${ci_mode}" = true ]; then
        RED=''
        GREEN=''
        YELLOW=''
        BLUE=''
        NC=''
    else
        RED='\033[0;31m'
        GREEN='\033[0;32m'
        YELLOW='\033[1;33m'
        BLUE='\033[0;34m'
        NC='\033[0m'
    fi
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-push)
            do_push=false
            shift
            ;;
        --no-commit)
            do_commit=false
            do_push=false
            shift
            ;;
        --max-turns)
            max_turns="$2"
            shift 2
            ;;
        --interactive)
            interactive=true
            shift
            ;;
        --ci)
            ci_mode=true
            shift
            ;;
        --output-dir)
            output_dir="$2"
            shift 2
            ;;
        --help|-h)
            head -30 "$0" | tail -27
            exit 0
            ;;
        -*)
            echo "Unknown option: $1"
            exit 1
            ;;
        *)
            projects="${projects} $1"
            shift
            ;;
    esac
done

setup_colors

# Activate venv if available
if [ -e /srv/shakenfist/kerbside-patches-tools/bin/activate ]; then
    . /srv/shakenfist/kerbside-patches-tools/bin/activate
fi

# Create output directory
if [ -z "${output_dir}" ]; then
    output_dir=$(mktemp -d)
    cleanup_output=true
else
    mkdir -p "${output_dir}"
    cleanup_output=false
fi

cleanup() {
    if [ "${cleanup_output}" = true ]; then
        rm -rf "${output_dir}"
    fi
}
trap cleanup EXIT

# CI mode output helper
ci_output() {
    local key="$1"
    local value="$2"
    if [ "${ci_mode}" = true ]; then
        echo "${key}=${value}"
    fi
}

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Kerbside Patches Auto-Lint Fixer${NC}"
echo -e "${BLUE}========================================${NC}"
echo

# Step 1: Find kolla-ansible projects
echo -e "${YELLOW}Step 1: Finding kolla-ansible projects...${NC}"

if [ -z "${projects}" ]; then
    for dir in kolla-ansible*/; do
        dir="${dir%/}"
        if [ ! -e "${dir}/config.yaml" ]; then
            continue
        fi

        skip_rebase=$(yq -r '.skip_rebase // "false"' \
            "${dir}/config.yaml")
        if [ "${skip_rebase}" == "true" ]; then
            continue
        fi

        projects="${projects} ${dir}"
    done
fi

echo "Projects to check:${projects}"
echo

# Step 2: Apply patches and run linters to find errors
echo -e "${YELLOW}Step 2: Applying patches and running linters...${NC}"
echo

all_lint_errors=""
lint_failed=false
failed_projects=""

for project in ${projects}; do
    echo -e "${BLUE}--- ${project} ---${NC}"

    directory=$(yq -r .directory "${project}/config.yaml")

    # Clean previous source tree
    rm -rf src/

    # Apply patches without tests
    set +e
    ./_build/apply-patches-and-test.sh --skip-tests "${project}" \
        > "${output_dir}/apply-${project}.log" 2>&1
    apply_exit=$?
    set -e

    if [ ${apply_exit} -ne 0 ]; then
        echo -e "${YELLOW}Patches failed to apply for ${project}, skipping${NC}"
        continue
    fi

    # Run linters
    pushd "src/${directory}" > /dev/null

    if ! tox -a 2>/dev/null | grep -q linters; then
        echo "No linters environment, skipping"
        popd > /dev/null
        continue
    fi

    set +e
    tox -elinters > "${output_dir}/lint-${project}.txt" 2>&1
    lint_exit=$?
    set -e

    popd > /dev/null

    if [ ${lint_exit} -ne 0 ]; then
        echo -e "${RED}Lint errors in ${project}${NC}"
        lint_failed=true
        failed_projects="${failed_projects} ${project}"

        # Extract just the ansible-lint violations
        lint_errors=$(grep -E "^(ansible|roles|tests|etc).*:" \
            "${output_dir}/lint-${project}.txt" 2>/dev/null || true)
        if [ -z "${lint_errors}" ]; then
            # Fallback: get the last 50 lines of output
            lint_errors=$(tail -50 "${output_dir}/lint-${project}.txt")
        fi

        all_lint_errors="${all_lint_errors}

### Lint errors for ${project}

${lint_errors}"
    else
        echo -e "${GREEN}Linters passed for ${project}${NC}"
    fi

    # Clean up source tree
    rm -rf src/
done

echo

if [ "${lint_failed}" = false ]; then
    echo -e "${GREEN}All linters passed! No fixes needed.${NC}"
    ci_output "lint_passed" "true"
    exit 0
fi

echo -e "${RED}Lint errors found in:${failed_projects}${NC}"
ci_output "lint_passed" "false"
echo

# Step 3: Check Claude availability
echo -e "${YELLOW}Step 3: Checking Claude Code availability...${NC}"

if ! command -v claude &> /dev/null; then
    echo -e "${RED}Error: Claude Code CLI not found${NC}"
    ci_output "claude_available" "false"
    ci_output "fix_succeeded" "false"
    exit 1
fi

ci_output "claude_available" "true"
echo -e "${GREEN}Claude Code is available${NC}"
echo

# Step 4: Build prompt and run Claude
echo -e "${YELLOW}Step 4: Building prompt and running Claude Code...${NC}"
echo

# Collect ORDER files for failing projects to identify relevant patches
order_info=""
for project in ${failed_projects}; do
    if [ -e "${project}/ORDER" ]; then
        order_info="${order_info}

### ${project}/ORDER
$(cat "${project}/ORDER")"
    fi
done

cat > "${output_dir}/claude-prompt.txt" << 'PROMPT_HEADER'
The ansible-lint linter (run via tox -elinters) has found errors in
kolla-ansible files that are created or modified by patches in this
repository. Please fix the lint errors.

## Critical Context

This is a PATCH REPOSITORY. The lint errors reference files in the
patched source tree (e.g., ansible/roles/kerbside/tasks/config.yml),
but those files DO NOT exist in this repository. They are created by
applying patch files from the _patches/ directory.

**You must edit the PATCH FILES in _patches/, NOT the source files.**

## How Patches Work

- Patch files in _patches/ are standard git diff format
- Each line starting with + is an added line in the patched source
- To fix a lint error in a patched file, find the corresponding
  + line in the patch file and modify it there
- Line counts in diff headers (e.g., @@ -0,0 +1,74 @@) do NOT need
  updating if you only change content within existing + lines
  (not adding or removing lines)

## Lint Errors Found

PROMPT_HEADER

echo "${all_lint_errors}" >> "${output_dir}/claude-prompt.txt"

cat >> "${output_dir}/claude-prompt.txt" << 'PROMPT_MIDDLE'

## Relevant ORDER Files (patches applied for failing projects)

PROMPT_MIDDLE

echo "${order_info}" >> "${output_dir}/claude-prompt.txt"

cat >> "${output_dir}/claude-prompt.txt" << 'PROMPT_FOOTER'

## Common Lint Fixes

### fqcn[action-core] - Use fully qualified collection names
Bare module names must use ansible.builtin.* prefix:
- file: -> ansible.builtin.file:
- template: -> ansible.builtin.template:
- command: -> ansible.builtin.command:
- copy: -> ansible.builtin.copy:
- import_role: -> ansible.builtin.import_role:
- import_tasks: -> ansible.builtin.import_tasks:
- include_tasks: -> ansible.builtin.include_tasks:
- include_role: -> ansible.builtin.include_role:
- meta: -> ansible.builtin.meta:
- fail: -> ansible.builtin.fail:
- wait_for: -> ansible.builtin.wait_for:
- set_fact: -> ansible.builtin.set_fact:
- debug: -> ansible.builtin.debug:
- assert: -> ansible.builtin.assert:
- shell: -> ansible.builtin.shell:
- stat: -> ansible.builtin.stat:
- lineinfile: -> ansible.builtin.lineinfile:
- blockinfile: -> ansible.builtin.blockinfile:
- service: -> ansible.builtin.service:
- uri: -> ansible.builtin.uri:
- get_url: -> ansible.builtin.get_url:
- apt: -> ansible.builtin.apt:
- pip: -> ansible.builtin.pip:
- group: -> ansible.builtin.group:
- user: -> ansible.builtin.user:
- cron: -> ansible.builtin.cron:

In patch files, these appear as lines starting with +:
  +  file:       ->  +  ansible.builtin.file:
  +  template:   ->  +  ansible.builtin.template:

IMPORTANT: Only change module names used as task actions (YAML keys
at the task level). Do NOT change module names used in other contexts
like parameter values (e.g., state: file should NOT be changed).

### name[missing] - All tasks need a name attribute
Add a descriptive name: attribute to tasks that lack one.

### yaml[line-length] - Lines too long
Break long lines. In YAML, use > or | for multiline strings.

## Your Task

1. Read CLAUDE.md to understand this repository's patch system
2. For each lint error, identify which patch file in _patches/
   creates the offending line (use the ORDER files above to narrow
   down which patches are relevant)
3. Edit the patch file(s) to fix the lint errors
4. Use replace_all when a fix applies to many instances of the
   same pattern in a single patch file
5. Verify by running:
   ./_build/test-apply.sh --skip-tests <project>
   for each failing project
6. Only stage changes with git add - do NOT commit
PROMPT_FOOTER

echo "Prompt prepared ($(wc -c < "${output_dir}/claude-prompt.txt") bytes)"
echo

# Run Claude Code
if [ "${interactive}" = true ]; then
    echo "Prompt file: ${output_dir}/claude-prompt.txt"
    echo
    cat "${output_dir}/claude-prompt.txt"
    echo
    echo "Run 'claude' and paste the prompt above."
    exit 1
else
    claude -p "$(cat "${output_dir}/claude-prompt.txt")" \
        --dangerously-skip-permissions \
        --max-turns "${max_turns}" \
        --model opus \
        --output-format json > "${output_dir}/claude-output.json" || true

    if [ -f "${output_dir}/claude-output.json" ]; then
        jq -r '.result // empty' "${output_dir}/claude-output.json"

        num_turns=$(jq -r '.num_turns // "unknown"' \
            "${output_dir}/claude-output.json")
        duration_ms=$(jq -r '.duration_ms // "unknown"' \
            "${output_dir}/claude-output.json")
        cost_usd=$(jq -r '.total_cost_usd // "unknown"' \
            "${output_dir}/claude-output.json")

        echo
        echo -e "${BLUE}Claude execution stats:${NC}"
        echo "  Turns: ${num_turns} / ${max_turns}"
        echo "  Duration: ${duration_ms}ms"
        echo "  Cost: \$${cost_usd}"

        ci_output "claude_turns" "${num_turns}"
        ci_output "claude_duration_ms" "${duration_ms}"
        ci_output "claude_cost_usd" "${cost_usd}"
    fi
fi

echo
echo -e "${YELLOW}Step 5: Verifying fix...${NC}"

# Re-run linters for the failing projects
verify_failed=false
for project in ${failed_projects}; do
    echo -e "${BLUE}--- Verifying ${project} ---${NC}"

    directory=$(yq -r .directory "${project}/config.yaml")
    rm -rf src/

    set +e
    ./_build/apply-patches-and-test.sh --skip-tests "${project}" \
        > /dev/null 2>&1
    apply_exit=$?
    set -e

    if [ ${apply_exit} -ne 0 ]; then
        echo -e "${RED}Patches no longer apply for ${project}!${NC}"
        verify_failed=true
        continue
    fi

    pushd "src/${directory}" > /dev/null
    set +e
    tox -elinters > "${output_dir}/lint-verify-${project}.txt" 2>&1
    verify_exit=$?
    set -e
    popd > /dev/null

    rm -rf src/

    if [ ${verify_exit} -ne 0 ]; then
        echo -e "${RED}Lint errors remain in ${project}:${NC}"
        tail -20 "${output_dir}/lint-verify-${project}.txt"
        verify_failed=true
    else
        echo -e "${GREEN}Linters pass for ${project}${NC}"
    fi
done

if [ "${verify_failed}" = true ]; then
    echo
    echo -e "${RED}Verification failed - not all lint errors fixed${NC}"
    ci_output "fix_succeeded" "false"
    exit 1
fi

echo
echo -e "${GREEN}All lint errors fixed!${NC}"
ci_output "fix_succeeded" "true"

# Step 6: Commit and push
if [ "${do_commit}" = false ]; then
    echo -e "${YELLOW}Skipping commit (--no-commit)${NC}"
    git status --short _patches/
    exit 0
fi

echo -e "${YELLOW}Step 6: Committing fixes...${NC}"

git add _patches/ ./*/ORDER

if git diff --staged --quiet; then
    echo "No changes to commit"
else
    git commit -m "$(cat <<'EOF'
Fix ansible-lint errors in kolla-ansible patches.

Automated fix of ansible-lint violations (e.g., fqcn[action-core],
name[missing]) in patch files.

Assisted-By: Claude Code

Signed-off-by: Claude Code run by Shakenfist Bot <bot@shakenfist.com>
EOF
)"
    echo -e "${GREEN}Changes committed${NC}"
fi

if [ "${do_push}" = false ]; then
    echo -e "${YELLOW}Skipping push (--no-push)${NC}"
    exit 0
fi

echo -e "${YELLOW}Step 7: Pushing to remote...${NC}"

if [ -n "${GITHUB_HEAD_REF}" ]; then
    current_branch="${GITHUB_HEAD_REF}"
else
    current_branch=$(git rev-parse --abbrev-ref HEAD)
fi

echo "Pushing to branch: ${current_branch}"

set +e
git push origin "HEAD:${current_branch}"
push_exit_code=$?
set -e

if [ ${push_exit_code} -ne 0 ]; then
    echo -e "${RED}Push failed${NC}"
    ci_output "push_succeeded" "false"
    exit 2
fi

echo -e "${GREEN}Pushed to origin/${current_branch}${NC}"
ci_output "push_succeeded" "true"
echo
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Lint fixes committed and pushed!${NC}"
echo -e "${GREEN}========================================${NC}"
