#!/bin/bash

# Rebase helper that uses Claude Code to fix failing patches.
#
# This script is used by both the daily-rebase-checks.yml GitHub Actions
# workflow and for interactive command-line use.
#
# Usage:
#   _build/rebase-with-claude.sh [options] [project1 project2 ...]
#
# Options:
#   --bump-shas         Update source SHAs to latest upstream (like daily rebase)
#   --step-forward N    Step forward N commits from current SHA (for incremental)
#   --no-claude         Skip Claude Code, just test and report failures
#   --max-turns N       Maximum Claude turns (default: 50)
#   --interactive       Run Claude in interactive mode (default: headless)
#   --ci                CI mode: output machine-readable status, no colors
#   --output-dir DIR    Directory for output files (default: temp dir)
#   --help              Show this help message
#
# If no projects specified, tests all projects with config.yaml files.
#
# Exit codes:
#   0 - All patches applied successfully (or were fixed by Claude)
#   1 - Patches failed and could not be fixed
#   2 - Patches failed, --no-claude specified
#
# Examples:
#   # Test all patches on current branch (no SHA updates)
#   _build/rebase-with-claude.sh
#
#   # Test specific project
#   _build/rebase-with-claude.sh kolla-ansible-2025.1
#
#   # Full daily rebase (update SHAs, test, auto-fix)
#   _build/rebase-with-claude.sh --bump-shas
#
#   # Step forward 5 commits from current position (for manual incremental rebase)
#   _build/rebase-with-claude.sh --step-forward 5
#
#   # CI mode with output directory
#   _build/rebase-with-claude.sh --ci --output-dir /tmp/results

set -e

topdir=$(cd "$(dirname "$0")/.." && pwd)
cd "${topdir}"

# Default options
bump_shas=false
step_forward=""
use_claude=true
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
        --bump-shas)
            bump_shas=true
            shift
            ;;
        --step-forward)
            step_forward="$2"
            shift 2
            ;;
        --no-claude)
            use_claude=false
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
            head -45 "$0" | tail -42
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
echo -e "${BLUE}Kerbside Patches Rebase Helper${NC}"
echo -e "${BLUE}========================================${NC}"
echo

# Step 1: Optionally bump source SHAs
if [ "${bump_shas}" = true ]; then
    echo -e "${YELLOW}Step 1: Updating source SHAs to latest upstream...${NC}"
    ./_build/bump-source-shas.sh 1
    echo
    echo "SHA changes:"
    git diff --stat */config.yaml 2>/dev/null || true
    echo
elif [ -n "${step_forward}" ]; then
    echo -e "${YELLOW}Step 1: Stepping forward ${step_forward} commits...${NC}"
    ./_build/bump-source-shas.sh --forward "${step_forward}"
    echo
    echo "SHA changes:"
    git diff --stat */config.yaml 2>/dev/null || true
    echo
else
    echo -e "${YELLOW}Step 1: Skipping SHA updates (use --bump-shas or --step-forward N)${NC}"
    echo
fi

# Step 2: Test patch application
echo -e "${YELLOW}Step 2: Testing patch application...${NC}"
echo

set +e
if [ -n "${projects}" ]; then
    ./_build/test-patches-for-ci.sh ${projects} > "${output_dir}/patch-test-results.json" 2>&1
else
    ./_build/test-patches-for-ci.sh > "${output_dir}/patch-test-results.json" 2>&1
fi
test_exit_code=$?
set -e

echo "Test results:"
cat "${output_dir}/patch-test-results.json"
echo

if [ ${test_exit_code} -eq 0 ]; then
    echo -e "${GREEN}✓ All patches applied successfully!${NC}"
    ci_output "patches_failed" "false"
    ci_output "fix_succeeded" "true"
    exit 0
fi

echo -e "${RED}✗ Some patches failed to apply${NC}"
ci_output "patches_failed" "true"
echo

# Extract failure details
cat "${output_dir}/patch-test-results.json" | \
    ./_build/extract-patch-failures.py > "${output_dir}/patch-failures.txt"
echo "Failure details:"
cat "${output_dir}/patch-failures.txt"
echo

# Step 3: Analyze shared patches
echo -e "${YELLOW}Step 3: Analyzing shared patch usage...${NC}"
./_build/analyze-shared-patches.py "${output_dir}/patch-test-results.json" \
    > "${output_dir}/shared-patch-analysis.json"
echo "Shared patch analysis:"
cat "${output_dir}/shared-patch-analysis.json"
echo

# Step 4: Optionally run Claude Code
if [ "${use_claude}" = false ]; then
    echo -e "${YELLOW}Skipping Claude Code (--no-claude specified)${NC}"
    echo "Fix the patches manually, then run this script again to verify."
    ci_output "claude_available" "false"
    ci_output "fix_succeeded" "false"
    exit 2
fi

# Check Claude availability
if ! command -v claude &> /dev/null; then
    echo -e "${RED}Error: Claude Code CLI not found${NC}"
    echo "Install with: npm install -g @anthropic-ai/claude-code"
    echo "Then authenticate with: claude login"
    ci_output "claude_available" "false"
    ci_output "fix_succeeded" "false"
    exit 1
fi

ci_output "claude_available" "true"

echo -e "${YELLOW}Step 4: Running Claude Code to attempt patch fixes...${NC}"
echo

# Build the prompt
cat > "${output_dir}/claude-prompt.txt" << 'PROMPT_EOF'
A patch has failed to apply during the rebase of the kerbside-patches
repository. Please fix the failing patch.

## Context

This repository maintains patches against OpenStack components
(kolla, kolla-ansible, nova). The rebase process has:
1. Updated source SHAs in config.yaml files to latest upstream (if --bump-shas)
2. Attempted to apply patches with the new upstream code

## Failure Details

PROMPT_EOF

# Append failure details
cat "${output_dir}/patch-failures.txt" >> "${output_dir}/claude-prompt.txt"

cat >> "${output_dir}/claude-prompt.txt" << 'PROMPT_EOF'

## Shared Patch Analysis

PROMPT_EOF

# Append the shared patch analysis
cat "${output_dir}/shared-patch-analysis.json" >> "${output_dir}/claude-prompt.txt"

cat >> "${output_dir}/claude-prompt.txt" << 'PROMPT_EOF'

## Your Task

1. Read CLAUDE.md to understand how patches work in this repo
2. Read the shared patch analysis above to determine the fix strategy
3. Read the failing patch file from _patches/
4. Examine the upstream source code in src/ to understand what changed
5. Apply the recommended strategy:

   **If strategy is "modify_in_place":**
   - Update the patch file directly so it applies cleanly
   - Follow the guidelines in CLAUDE.md about editing diff headers

   **If strategy is "create_copy":**
   - Create a NEW patch file with the suggested_name from the analysis
   - Update the ORDER file for the failing project to use the new patch
   - Leave the original patch unchanged for other releases

6. Test your fix: ./_build/test-apply.sh --skip-tests <project>
7. If successful, stage your changes (do not commit - the user will review)

## Release Name Mappings

When creating release-specific patches, use codenames in filenames:
- 2024.1 = caracal
- 2024.2 = dalmatian
- 2025.1 = epoxy
- 2025.2 = flamingo
- 2026.1/master = gazpacho

## Important Notes

- Only modify files in _patches/ directory and */ORDER files
- When editing patches, update BOTH the diff content AND the
  @@ header line counts in a single edit
- The patch may need line number adjustments if upstream added/removed lines
- Test your changes before staging
- Do NOT commit - just stage the changes with git add
PROMPT_EOF

echo "Prompt prepared. Starting Claude Code..."
echo

# Run Claude Code
if [ "${interactive}" = true ]; then
    # Interactive mode - let user work with Claude
    echo "Prompt file: ${output_dir}/claude-prompt.txt"
    echo
    cat "${output_dir}/claude-prompt.txt"
    echo
    echo "Run 'claude' and paste the prompt above to fix patches interactively."
    exit 1
else
    # Headless mode
    claude -p "$(cat "${output_dir}/claude-prompt.txt")" \
        --dangerously-skip-permissions \
        --max-turns "${max_turns}" \
        --output-format text || true
fi

echo
echo -e "${YELLOW}Step 5: Verifying fix...${NC}"

# Clean up source directories
rm -rf src/

# Re-run patch tests
set +e
if [ -n "${projects}" ]; then
    ./_build/test-patches-for-ci.sh ${projects} > "${output_dir}/patch-verify-results.json" 2>&1
else
    ./_build/test-patches-for-ci.sh > "${output_dir}/patch-verify-results.json" 2>&1
fi
verify_exit_code=$?
set -e

echo "Verification results:"
cat "${output_dir}/patch-verify-results.json"
echo

if [ ${verify_exit_code} -eq 0 ]; then
    echo -e "${GREEN}✓ Claude's fix was successful!${NC}"
    ci_output "fix_succeeded" "true"
    echo
    echo "Changes staged by Claude:"
    git status --short _patches/ */ORDER 2>/dev/null || true
    echo
    if [ "${ci_mode}" = false ]; then
        echo "Review the changes, then commit when ready:"
        echo "  git diff --staged"
        echo "  git commit"
    fi
    exit 0
else
    echo -e "${RED}✗ Claude's fix did not resolve all issues${NC}"
    ci_output "fix_succeeded" "false"
    echo
    if [ "${ci_mode}" = false ]; then
        echo "You may need to:"
        echo "  1. Run this script again with --interactive for more control"
        echo "  2. Fix the remaining issues manually"
        echo "  3. Check the verification output above for details"
    fi
    exit 1
fi
