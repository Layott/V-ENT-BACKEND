#!/usr/bin/env bash
# refresh_onto_main.sh <worktree> [FE]
# Move a worktree whose branch has no commits of its own onto current main,
# carrying its uncommitted work, WITHOUT a commit (so no hook runs half way).
# The hook pairs a BE commit with the FE worktree on the same branch and the
# other way round; both sides must stand on the same main before either
# commits, or one side's new endpoint has no caller on the other.
# The work is kept as a patch in V-ENT/tasks/wip/ship before anything is reset.
set -u
D="$1"; SIDE="${2:-BE}"
S="$(cd "$(dirname "$0")" && pwd)"
# Patches and logs live outside every repo, so a refresh never leaves files to commit.
W="$(cd "$S/../../.." && pwd)/tasks/wip/ship"; mkdir -p "$W"
cd "$D" || exit 1
git fetch -q origin
if ! git merge-base --is-ancestor HEAD origin/main; then
  echo "$(basename "$D"): has commits of its own, left for the merge after commit"; exit 0
fi
dirty=$(git status --porcelain | awk '{print $2}')
[ "$dirty" = "tools/debt-ledger.json" ] && git checkout -q -- tools/debt-ledger.json
if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ]; then
  echo "$(basename "$D"): already on main"; exit 0
fi
P="$W/refresh-$(basename "$D").patch"
git add -A
git diff --cached --binary > "$P"
added=$(git diff --cached --name-only --diff-filter=A)
git reset -q
git checkout -q -- .
for f in $added; do rm -f -- "$f"; done
git merge -q --ff-only origin/main || { echo "$(basename "$D"): fast-forward failed; work is in $P"; exit 1; }
if [ -s "$P" ]; then
  if ! git apply --3way --index "$P" 2> "$W/refresh-$(basename "$D").log"; then
    if [ -n "$(git diff --name-only --diff-filter=U)" ]; then
      python "$S/resolve_merge.py" || { echo "$(basename "$D"): conflicts need a person; work is in $P"; exit 1; }
    else
      echo "$(basename "$D"): the patch did not apply; work is in $P"; tail -5 "$W/refresh-$(basename "$D").log"; exit 1
    fi
  fi
fi
if [ "$SIDE" = FE ]; then
  rm -f public/i18n/*.js; node scripts/split-dictionaries.mjs > /dev/null 2>&1
fi
git add -A
echo "$(basename "$D"): on main $(git rev-parse --short HEAD), work re-applied"
