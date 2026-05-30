#!/usr/bin/env bash
# commit-msg-gate — require every commit to reference an approved plan, unless it
# carries an approved "ceremony" prefix (chore/docs/ci/build).
#
# Part of StrictLock (https://github.com/TDM-Technologies/strictlock).
# Install as .git/hooks/commit-msg. Applies to every commit in the repo,
# whether made by a human or an agent — so the version-control trail always
# links back to the authorization for the change.
#
# A commit passes if EITHER:
#   (a) its first line starts with an approved prefix, e.g.
#         chore: ...   chore(scope): ...   docs: ...   ci: ...   build: ...
#   (b) its body contains a line matching:  plan: <slug>
#
# Configuration (environment):
#   COMMIT_MSG_GATE_BYPASS=on        skip the gate for one commit (deliberate).
#   COMMIT_MSG_GATE_PREFIXES=<regex> override the approved first-line prefix
#                                    regex (grep -E syntax). See DEFAULT below.

set -u

MSG_FILE="${1:-}"
if [[ -z "$MSG_FILE" || ! -f "$MSG_FILE" ]]; then
  echo "commit-msg-gate: no message file received (arg was '$MSG_FILE')." >&2
  exit 1
fi

if [[ "${COMMIT_MSG_GATE_BYPASS:-}" == "on" ]]; then
  echo "commit-msg-gate: COMMIT_MSG_GATE_BYPASS=on — skipping gate." >&2
  exit 0
fi

# Approved "ceremony" first-line prefixes (no plan link required). Override with
# COMMIT_MSG_GATE_PREFIXES to fit your own convention.
DEFAULT_PREFIXES='^(chore(\([^)]+\))?:|docs(\([^)]+\))?:|ci(\([^)]+\))?:|build(\([^)]+\))?:)'
PREFIX_RE="${COMMIT_MSG_GATE_PREFIXES:-$DEFAULT_PREFIXES}"

# Strip git's comment lines (added by the commit template) before evaluating.
FULL_MSG="$(grep -v '^[[:space:]]*#' "$MSG_FILE")"
FIRST_LINE="$(printf '%s\n' "$FULL_MSG" | sed -n '1p')"

# Empty / whitespace-only messages: let git's own handling reject them.
if [[ -z "${FIRST_LINE// /}" ]]; then
  exit 0
fi

# (a) approved ceremony prefix on the first line.
if printf '%s' "$FIRST_LINE" | grep -qE "$PREFIX_RE"; then
  exit 0
fi

# (b) a plan reference anywhere in the message body.
if printf '%s' "$FULL_MSG" | grep -qE '^plan:[[:space:]]+[A-Za-z0-9._-]+'; then
  exit 0
fi

cat >&2 <<'EOF'
---------------------------------------------------------------------
commit-msg-gate rejected this commit.

Your commit message must do ONE of:

  (a) Start the first line with an approved ceremony prefix:
        chore: ...        chore(scope): ...
        docs: ...         docs(scope): ...
        ci: ...           build: ...

  (b) Include a line referencing the approved plan:
        plan: add-logout-button
      (plan files live in your plan-gate plans directory)

Emergency bypass: COMMIT_MSG_GATE_BYPASS=on git commit ...
---------------------------------------------------------------------
EOF
exit 1
