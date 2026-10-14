#!/usr/bin/env bash
# Create a crew project, or bring an existing one to the crew's settings
# (ADR 0030, plan Part 7a). Run by the Sponsor: every step needs admin, which
# quarters-claude and the crew's Apps don't hold (crew#566). A template copies
# files only, so the settings are set here.
#
#   scripts/new-project.sh <name> [--pages] [--existing] [--dry-run]
#   scripts/new-project.sh <name> --protect [--dry-run]
#
#   --pages      the project deploys to GitHub Pages: turn it on, from Actions
#   --existing   the repository exists already: change its settings only
#   --protect    once the skeleton has made CI green: require it on main
#   --dry-run    print what would run, change nothing
#
# Between the two: `crew onboard <name>`, `crew design <name>`, <name> in
# delivery.repos by pull request, and the skeleton Goal. `crew ready <name>`
# says when to protect.

set -euo pipefail

OWNER=mqucifer
TEMPLATE=project-template
CLAUDE=quarters-claude
APPS=(mqucifer-crew mqucifer-crew-approver)
SECRET=BOARD_APP_PRIVATE_KEY
# The template's CI job. Required only once the skeleton makes it green: the
# design's pull request names checks that nothing can pass yet.
CHECK=checks

name=""
pages=false
existing=false
protect=false
dry=false
for arg in "$@"; do
  case "$arg" in
    --pages) pages=true ;;
    --existing) existing=true ;;
    --protect) protect=true ;;
    --dry-run) dry=true ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) name="$arg" ;;
  esac
done
if [[ -z "$name" ]]; then
  sed -n '7,17p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
fi
repo="$OWNER/$name"

# Every step needs admin, so it runs as the Sponsor. A GH_TOKEN in the
# environment wins over `gh auth login`: run from Claude's session, it was
# quarters-claude's, and protection failed with a bare 403.
me=$(gh api user -q .login 2>/dev/null || echo "unknown")
if [[ "$me" != "$(gh api "orgs/$OWNER/memberships/$me" -q 'select(.role == "admin") | .user.login' 2>/dev/null)" ]]; then
  echo "gh is running as $me, who isn't an admin of $OWNER." >&2
  echo "Run it as the Sponsor: unset GH_TOKEN (env -u GH_TOKEN $0 $*) or use your own terminal." >&2
  [[ "$dry" == true ]] || exit 1
fi

run() {
  echo "+ $*"
  if [[ "$dry" == false ]]; then "$@"; fi
}

# 6. Branch protection: pull requests, one approval, and the template's checks.
if [[ "$protect" == true ]]; then
  protection=$(cat <<EOF
{
  "required_status_checks": {"strict": false, "contexts": ["$CHECK"]},
  "enforce_admins": true,
  "required_pull_request_reviews": {"required_approving_review_count": 1},
  "restrictions": null
}
EOF
)
  echo "+ gh api -X PUT repos/$repo/branches/main/protection (requires \`$CHECK\`, one approval)"
  if [[ "$dry" == false ]]; then
    gh api -X PUT "repos/$repo/branches/main/protection" --input - <<<"$protection" >/dev/null
  fi
  echo "Done. \`crew ready $name\` should now say level 2."
  exit 0
fi

# 1. The repository, from the template.
if [[ "$existing" == false ]]; then
  run gh repo create "$repo" --public --template "$OWNER/$TEMPLATE"
  if [[ "$dry" == false ]]; then
    # GitHub copies the template's files after the repository exists.
    for _ in $(seq 30); do
      gh api "repos/$repo/branches/main" >/dev/null 2>&1 && break
      sleep 2
    done
  fi
fi

# The settings the crew's code relies on (readiness, level 1).
run gh repo edit "$repo" --enable-issues --enable-squash-merge --delete-branch-on-merge
run gh api -X PUT "repos/$repo/actions/permissions" -F enabled=true -f allowed_actions=all

# 2. Access: Claude with Write, the crew's Apps, the board workflow's secret.
run gh api -X PUT "repos/$repo/collaborators/$CLAUDE" -f permission=push
repo_id=$(gh api "repos/$repo" -q .id 2>/dev/null) || repo_id="<id once created>"
for app in "${APPS[@]}"; do
  if ! line=$(gh api /user/installations \
    -q ".installations[] | select(.app_slug == \"$app\") | \"\(.id) \(.repository_selection)\"" \
    2>/dev/null) || [[ -z "$line" ]]; then
    echo "! couldn't read $app's installation: check it can reach $repo" >&2
    continue
  fi
  read -r id selection <<<"$line"
  if [[ "$selection" == "selected" ]]; then
    run gh api -X PUT "/user/installations/$id/repositories/$repo_id"
  else
    echo "  $app has every repository"
  fi
done
if ! visibility=$(gh api "orgs/$OWNER/actions/secrets/$SECRET" -q .visibility 2>/dev/null); then
  echo "! couldn't read $SECRET's access: check it reaches $repo" >&2
elif [[ "$visibility" == "selected" ]]; then
  run gh api -X PUT "orgs/$OWNER/actions/secrets/$SECRET/repositories/$repo_id"
else
  echo "  $SECRET reaches $visibility repositories"
fi

# 7. The deploy target.
if [[ "$pages" == true ]]; then
  if gh api "repos/$repo/pages" >/dev/null 2>&1; then
    run gh api -X PUT "repos/$repo/pages" -f build_type=workflow
  else
    run gh api -X POST "repos/$repo/pages" -f build_type=workflow
  fi
fi

echo
echo "Done. Claude accepts the invitation for $CLAUDE. Next:"
echo "  crew ready $name"
echo "  crew onboard $name     # the Product Owner's interview; you merge the record"
echo "  crew design $name      # the Architect's design; you merge it"
echo "  add $name to delivery.repos, by pull request"
echo "  once the skeleton makes CI green: scripts/new-project.sh $name --protect"
