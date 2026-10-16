#!/usr/bin/env bash
# commit-msg-gate — require every commit to name a plan (`plan: <slug>`), unless it
# carries an approved "ceremony" prefix (chore/docs/ci/build). The slug is not
# validated unless COMMIT_MSG_GATE_PLANS_DIR is set (see below).
#
# Part of StrictLock (https://github.com/TDM-Technologies/strictlock).
# Install as .git/hooks/commit-msg. Runs on every commit in the repo that goes
# through git's hook machinery (--no-verify skips it), whether made by a human
# or an agent — so the version-control trail names the plan for the change.
#
# A commit passes if EITHER:
#   (a) its first line starts with an approved prefix, e.g.
#         chore: ...   chore(scope): ...   docs: ...   ci: ...   build: ...
#   (b) its message BODY (the lines after the subject) contains a line, at line
#       start, matching:  plan: <slug>      (a `plan:` on the subject line does not count)
#
# By default the slug is not validated. With COMMIT_MSG_GATE_PLANS_DIR set, at
# least one body slug must resolve to <slug>.md in that directory.
#
# Configuration (environment):
#   COMMIT_MSG_GATE_BYPASS=on        skip the gate (single-use by convention: set inline per commit).
#   COMMIT_MSG_GATE_PLANS_DIR=<dir>  opt in to slug validation: `plan: <slug>` must resolve to
#                                    <dir>/<slug>.md (plan-gate's plan-file layout). If the
#                                    directory is set but missing/unreadable the gate FAILS CLOSED.
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

# (b) a `plan: <slug>` line in the message BODY (the lines after the subject line;
# a `plan:` on the subject line itself does not count).
BODY="$(printf '%s\n' "$FULL_MSG" | sed -n '2,$p')"
SLUGS="$(printf '%s\n' "$BODY" | sed -n -E 's/^plan:[[:space:]]+([A-Za-z0-9._-]+).*/\1/p')"

PLANS_DIR="${COMMIT_MSG_GATE_PLANS_DIR:-}"
if [[ -n "$SLUGS" ]]; then
  if [[ -z "$PLANS_DIR" ]]; then
    exit 0   # slug present; no plans dir configured, so it is not validated
  fi
  # Opt-in validation. Fail CLOSED if the directory is set but unusable.
  if [[ ! -d "$PLANS_DIR" || ! -r "$PLANS_DIR" || ! -x "$PLANS_DIR" ]]; then
    echo "commit-msg-gate: COMMIT_MSG_GATE_PLANS_DIR='$PLANS_DIR' is not a readable directory — rejecting (fail closed)." >&2
    exit 1
  fi
  while IFS= read -r slug; do
    [[ -z "$slug" || "$slug" == "." || "$slug" == ".." ]] && continue
    # Resolution rule: <slug>.md in the plans directory (plan-gate's layout).
    if [[ -f "$PLANS_DIR/$slug.md" && -r "$PLANS_DIR/$slug.md" ]]; then
      exit 0
    fi
  done <<< "$SLUGS"
  echo "commit-msg-gate: no plan file '<slug>.md' in '$PLANS_DIR' for: $(printf '%s' "$SLUGS" | tr '\n' ' ')" >&2
fi

cat >&2 <<'EOF'
---------------------------------------------------------------------
commit-msg-gate rejected this commit.

Your commit message must do ONE of:

  (a) Start the first line with an approved ceremony prefix:
        chore: ...        chore(scope): ...
        docs: ...         docs(scope): ...
        ci: ...           build: ...

  (b) Include a line in the message BODY (after the subject line) naming the plan:
        plan: add-logout-button
      (with COMMIT_MSG_GATE_PLANS_DIR set, <slug>.md must exist in that directory)

Emergency bypass: COMMIT_MSG_GATE_BYPASS=on git commit ...
---------------------------------------------------------------------
EOF
exit 1
