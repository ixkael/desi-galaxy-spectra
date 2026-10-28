#!/bin/bash
# Pre-push hygiene: nothing private may be tracked in the public tree.
#
# Usage: tools/hygiene_grep.sh [REPO_DIR]     (default: the repository this script is in)
#
# Checked over the files git tracks (or would track: untracked files that are not ignored):
#   1. file NAMES: CLAUDE.md, DESIGN.md, FIX_LOG.md, CODE_REVIEW.md, FIX_REVIEW.md and settings.toml
#      at any depth, and anything under a .claude directory.  A tracked file named exactly
#      settings.toml is forbidden; settings.example-isambard.toml is fine.  Names are tested
#      against the list of file names, never grepped as text (.gitignore legitimately names them).
#   2. file CONTENTS (text files only): personal paths, the NAS layout, scratch directories, one
#      GitHub account name and private keys.
#   3. optional extra patterns: if DGS_HYGIENE_EXTRA names a file, each non-empty line of it is a
#      case-insensitive extended regular expression that must not match any content.  Keep that
#      file outside the repository (it is the place for private host names and the like).  A set
#      variable that names an unreadable file is an error.
#
# Exit status 0 if clean, 1 and the matches on stdout otherwise (also 1 if a file cannot be read),
# 2 if the directory is not a git work tree.  The built-in patterns carry a bracketed character
# (for example b[l]) so that this file does not match itself.
set -u

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

if ! git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "hygiene_grep: $ROOT is not a git work tree" >&2
    exit 2
fi

status=0

# existing files only (a file deleted in the work tree but still in the index is skipped)
list=$(mktemp)
err=$(mktemp)
trap 'rm -f "$list" "$err"' EXIT
git -C "$ROOT" ls-files -z --cached --others --exclude-standard |
    while IFS= read -r -d '' f; do
        [ -f "$ROOT/$f" ] && printf '%s\0' "$f"
    done > "$list"

files=()
while IFS= read -r -d '' f; do
    files+=("$f")
done < "$list"

# 1. forbidden file names
names=$(tr '\0' '\n' < "$list" | grep -E '(^|/)(CLAUDE\.md|DESIGN\.md|FIX_LOG\.md|CODE_REVIEW\.md|FIX_REVIEW\.md|settings\.toml)$|(^|/)\.claude/' || true)
if [ -n "$names" ]; then
    echo "hygiene_grep: forbidden file name(s) tracked:"
    echo "$names"
    status=1
fi

# scan LABEL PATTERN [grep flag]: grep exit 1 is "no match", 0 is a hit, 2 or more is an error
scan() {
    local label="$1" pattern="$2" icase="${3:-}" out rc
    [ "${#files[@]}" -gt 0 ] || return 0
    out=$(cd "$ROOT" && grep -HnIE $icase -e "$pattern" "${files[@]}" 2>"$err")
    rc=$?
    if [ "$rc" -ge 2 ]; then
        echo "hygiene_grep: error: grep failed (exit $rc) for $label:"
        cat "$err"
        status=1
    fi
    if [ "$rc" -eq 0 ] && [ -n "$out" ]; then
        echo "hygiene_grep: $label matches:"
        echo "$out"
        status=1
    fi
}

# 2. forbidden content
patterns=(
    '/home/b[l]'
    '/Users/ble[i]sted'
    'NAS/F[o]ur'
    'claude-scrat[c]h'
    'ixkael.u[6]'
    'BEGIN .* PRIVATE K[E]Y'
)
for p in "${patterns[@]}"; do
    scan "pattern '$p'" "$p"
done

# 3. extra patterns from a file outside the repository
if [ -n "${DGS_HYGIENE_EXTRA:-}" ]; then
    if [ ! -r "$DGS_HYGIENE_EXTRA" ] || [ ! -f "$DGS_HYGIENE_EXTRA" ]; then
        echo "hygiene_grep: error: DGS_HYGIENE_EXTRA is set but unreadable: $DGS_HYGIENE_EXTRA"
        status=1
    else
        while IFS= read -r p || [ -n "$p" ]; do
            [ -n "$p" ] || continue
            scan "extra pattern (DGS_HYGIENE_EXTRA)" "$p" -i
        done < "$DGS_HYGIENE_EXTRA"
    fi
fi

if [ "$status" -eq 0 ]; then
    echo "hygiene_grep: clean (${#files[@]} files checked)"
fi
exit "$status"
