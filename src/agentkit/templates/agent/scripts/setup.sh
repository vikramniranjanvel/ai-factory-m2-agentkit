#!/usr/bin/env bash
# One-off setup of this project in GitHub and AWS. Safe to run again: each step checks before it changes anything.
#
#   scripts/setup.sh                      # create the repo and environments, set the variable, show the registry entry
#   PLATFORM_DIR=../platform scripts/setup.sh   # also write registry/<project>.json into a platform checkout
#
# Needs: git, gh (logged in, may create repositories), aws (credentials for the account).
set -euo pipefail

PROJECT="__SLUG__"
REPO="__REPO__"
ENVIRONMENTS="dev test prod"
CREDENTIAL="__CREDENTIAL__"
PLATFORM_DIR="${PLATFORM_DIR:-}"

step() { printf '\n== %s\n' "$*"; }

for tool in git gh aws; do
  command -v "$tool" >/dev/null || { echo "error: $tool is not installed" >&2; exit 1; }
done
cd "$(dirname "$0")/.."

OWNER="$(gh api user --jq .login)"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
echo "owner $OWNER, repository $REPO, AWS account $ACCOUNT"

step "1. GitHub repository"
if gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
  echo "$OWNER/$REPO exists"
else
  [ -d .git ] || git init -q -b main
  git add -A
  git diff --cached --quiet || git commit -q -m "Scaffold the agent with agentkit init"
  gh repo create "$OWNER/$REPO" --private --source . --remote origin --push
fi
REPO_ID="$(gh api "repos/$OWNER/$REPO" --jq .id)"

step "2. GitHub environments ($ENVIRONMENTS); prod deploys only from main"
for env in $ENVIRONMENTS; do
  gh api -X PUT "repos/$OWNER/$REPO/environments/$env" --silent
  echo "environment $env"
done
gh api -X PUT "repos/$OWNER/$REPO/environments/prod" --silent \
  --input - <<<'{"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}'
gh api "repos/$OWNER/$REPO/environments/prod/deployment-branch-policies" --jq '.branch_policies[].name' | grep -qx main ||
  gh api -X POST "repos/$OWNER/$REPO/environments/prod/deployment-branch-policies" -f name=main --silent
echo "prod is restricted to main"

step "3. Variable AWS_ACCOUNT_ID"
gh variable set AWS_ACCOUNT_ID --repo "$OWNER/$REPO" --body "$ACCOUNT"

step "4. Platform registry entry"
ENTRY="$(printf '{\n  "project": "%s",\n  "repository": { "name": "%s", "id": "%s" },\n  "targets": ["dev", "test", "prod"]\n}\n' \
  "$PROJECT" "$REPO" "$REPO_ID")"
if [ -n "$PLATFORM_DIR" ]; then
  printf '%s' "$ENTRY" >"$PLATFORM_DIR/registry/$PROJECT.json"
  echo "wrote $PLATFORM_DIR/registry/$PROJECT.json: commit it in the platform repo and merge; its deploy workflow"
  echo "then creates this project's deploy roles and settings records, which only this repository can assume."
else
  echo "Add this as registry/$PROJECT.json in the platform repo, then merge it:"
  printf '%s' "$ENTRY"
fi

step "5. Model API key ($CREDENTIAL), one per environment (Enter skips)"
for env in $ENVIRONMENTS; do
  [ -n "$CREDENTIAL" ] || { echo "the model needs no API key"; break; }
  name="/agentkit/$env/projects/$PROJECT/credentials/$CREDENTIAL"
  if aws ssm get-parameter --name "$name" >/dev/null 2>&1; then
    echo "$name exists"
    continue
  fi
  read -rs -p "API key for $env: " key
  echo
  if [ -n "$key" ]; then
    aws ssm put-parameter --name "$name" --type SecureString --value "$key" >/dev/null
    echo "stored $name"
  else
    echo "skipped $env; put $name before deploying there"
  fi
  key=""
done

step "Done"
echo "Next: merge the registry entry, then push a branch to deploy to dev."
