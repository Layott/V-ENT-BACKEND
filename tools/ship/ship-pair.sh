#!/usr/bin/env bash
# tools/ship/ship-pair.sh <slug> <branch> <be-title> <fe-title> <be-body-file> <fe-body-file>
#
# Commits the BE then FE worktree for one feature (V-ENT-BACKEND-<slug>,
# V-ENT-FRONTEND-<slug>), merges main in, pushes, opens PRs, merges both,
# backs the database up and deploys. A side with no worktree is skipped.
# Kept in the repo rather than a session scratchpad (inbox 539, CEO 24 Oct:
# "not by hand-built worktrees"), so every session ships the same way.
#
# SHIP_SESSION_URL, when set, adds the Claude-Session line to every commit
# and PR, as the attribution rules ask.
set -u
SLUG="$1"; BR="$2"; BET="$3"; FET="$4"; BEB="$5"; FEB="$6"
ROOT=/c/Users/Sweez/Desktop/LAYO/CLAUDE/V-ENT
BE="$ROOT/V-ENT-BACKEND-$SLUG"; FE="$ROOT/V-ENT-FRONTEND-$SLUG"
TRAILER=$'\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>'
PRFOOT=$'\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)'
if [ -n "${SHIP_SESSION_URL:-}" ]; then
  TRAILER="$TRAILER"$'\n'"Claude-Session: $SHIP_SESSION_URL"
  PRFOOT="$PRFOOT"$'\n\n'"$SHIP_SESSION_URL"
fi
# The hook rewrites the debt ledger after every commit. On its own that is not
# a change of ours, and a tree holding it refuses to merge main.
only_ledger() {
  local dirty; dirty=$(git status --porcelain | awk '{print $2}')
  [ "$dirty" = "tools/debt-ledger.json" ]
}
# Both sides onto the same main first, so each commit's hook judges a pair
# that agrees (24 Oct: a BE side ahead of its FE side read as an endpoint
# with no screen).
# A side with no worktree is judged by the hook against the main checkout, so
# that checkout must be current main (24 Oct: a BE-only fix was refused for 11
# "endpoints with no screen" because the FE checkout was 46 commits behind).
# Only fast-forwarded when it is on main and clean; anything else is somebody's
# work and is reported, never touched.
main_checkout_current() {
  local D="$1"
  git -C "$D" fetch -q origin
  if [ "$(git -C "$D" rev-parse --abbrev-ref HEAD)" != main ] || [ -n "$(git -C "$D" status --porcelain)" ]; then
    echo "$(basename "$D"): not on a clean main, the hook may judge against stale code"; return 0
  fi
  git -C "$D" merge -q --ff-only origin/main && echo "$(basename "$D"): main checkout brought to origin/main"
}
[ -d "$BE" ] || main_checkout_current "$ROOT/V-ENT-BACKEND"
[ -d "$FE" ] || main_checkout_current "$ROOT/V-ENT-FRONTEND"
[ -d "$BE" ] && { bash "$(dirname "$0")/refresh_onto_main.sh" "$BE" BE || exit 1; }
[ -d "$FE" ] && { bash "$(dirname "$0")/refresh_onto_main.sh" "$FE" FE || exit 1; }
for side in BE FE; do
  if [ "$side" = BE ]; then D="$BE"; T="$BET"; B="$BEB"; else D="$FE"; T="$FET"; B="$FEB"; fi
  [ -d "$D" ] || { echo "$side: no worktree, skipped"; continue; }
  cd "$D" || exit 1
  only_ledger && git checkout -q -- tools/debt-ledger.json
  git fetch -q origin
  if [ -z "$(git status --porcelain)" ] && [ "$(git rev-list --count origin/main..HEAD)" = 0 ]; then
    echo "$side nothing to ship"; continue
  fi
  if [ -n "$(git status --porcelain)" ]; then
    git add -A
    printf '%s\n\n%s%s\n' "$T" "$(cat "$B")" "$TRAILER" > "$ROOT/.commitmsg-$SLUG-$side"
    if ! git commit -q -F "$ROOT/.commitmsg-$SLUG-$side" > "$ROOT/.commitlog-$SLUG-$side" 2>&1; then
      echo "$side COMMIT REFUSED"; tail -30 "$ROOT/.commitlog-$SLUG-$side"; exit 1
    fi
    rm -f "$ROOT/.commitmsg-$SLUG-$side"
    echo "$side committed $(git rev-parse --short HEAD)"
  fi
  # Bring main in, so the PR merges: the known conflict files resolve the
  # same way every time (resolve_merge.py), the dictionaries are rebuilt.
  git fetch -q origin
  only_ledger && git checkout -q -- tools/debt-ledger.json
  if ! git merge-base --is-ancestor origin/main HEAD; then
    if ! git merge -q --no-edit origin/main > "$ROOT/.mergelog-$SLUG-$side" 2>&1; then
      python "$(dirname "$0")/resolve_merge.py" || { echo "$side MERGE NEEDS A PERSON"; git status --short | head; exit 1; }
    fi
    if [ "$side" = FE ]; then
      rm -f public/i18n/*.js; node scripts/split-dictionaries.mjs > /dev/null 2>&1; git add -A
    fi
    if git rev-parse -q --verify MERGE_HEAD > /dev/null; then
      MSGARGS=(--no-edit)
    elif [ -n "$(git diff --cached --name-only)" ]; then
      printf '%s

%s%s
' "chore(i18n): regenerate the dictionaries after merging main"         "split-dictionaries rewrites the generated files from the merged dictionaries.js." "$TRAILER" > "$ROOT/.commitmsg-$SLUG-$side-regen"
      MSGARGS=(-F "$ROOT/.commitmsg-$SLUG-$side-regen")
    else
      MSGARGS=()
    fi
    if [ ${#MSGARGS[@]} -gt 0 ]; then
      if ! git commit -q "${MSGARGS[@]}" > "$ROOT/.commitlog-$SLUG-$side-merge" 2>&1; then
        echo "$side MERGE COMMIT REFUSED"; tail -30 "$ROOT/.commitlog-$SLUG-$side-merge"; exit 1
      fi
      rm -f "$ROOT/.commitmsg-$SLUG-$side-regen"
    fi
    echo "$side main merged in $(git rev-parse --short HEAD)"
  fi
  git push -q -u origin "$BR" 2>&1 | grep -v "^remote:" | tail -2
  printf '%s%s\n' "$(cat "$B")" "$PRFOOT" > "$ROOT/.prbody-$SLUG-$side"
  URL=$(gh pr create --base main --head "$BR" --title "$T" --body-file "$ROOT/.prbody-$SLUG-$side" 2>&1 | tail -1)
  rm -f "$ROOT/.prbody-$SLUG-$side"
  echo "$side PR $URL"
  for i in 1 2 3 4 5 6; do
    st=$(gh pr view "$BR" --json mergeable -q .mergeable 2>/dev/null)
    [ "$st" = MERGEABLE ] && break; sleep 10
  done
  echo "$side $st"
  gh pr merge "$BR" --merge 2>&1 | tail -1
  echo "$side $(gh pr view "$BR" --json state -q .state)"
done
ssh -i ~/.ssh/vent_vps vent@162.35.101.16 "bash /srv/vent/backend/deploy/backup.sh" 2>&1 | tail -2
ssh -i ~/.ssh/vent_vps vent@162.35.101.16 "/srv/vent/deploy/deploy.sh" 2>&1 | tail -4
