# commit-msg-gate

**Require every commit to link back to a plan.**

A [StrictLock](../README.md) module. `commit-msg-gate` is a tiny `commit-msg` git hook that
enforces the version-control half of the governance loop: a commit must either name a plan
or be an explicitly-labelled "ceremony" commit. The result is a git history
where every functional change names the plan it was made under — the change-management
evidence [`plan-gate`](../plan-gate/) describes in its paper (§6), applied at the commit.
The hook checks that a `plan: <slug>` line is present in the message body. By default it does not
check the slug against your plans directory (whether the named plan was approved is for review to
confirm); set `COMMIT_MSG_GATE_PLANS_DIR` to make the slug resolve to a real plan file.

## What it does

A commit message passes if **either**:

- **(a)** its first line starts with an approved *ceremony* prefix — by default
  `chore:`, `docs:`, `ci:`, `build:` (with optional `(scope)`) — for changes that don't need
  a plan link; **or**
- **(b)** its message **body** (the lines after the subject line) contains a line matching
  `plan: <slug>` (at the start of a line), naming the plan the change was made under. A
  `plan:` on the subject line itself does not count.

Otherwise the commit is rejected with a message explaining both options.

## Install

```bash
cp commit-msg-gate.sh /path/to/your/repo/.git/hooks/commit-msg
chmod +x /path/to/your/repo/.git/hooks/commit-msg
```

It runs on every commit in that repo that goes through git's hook machinery — human or
agent (`--no-verify` skips it).

## Configuration (environment)

| Variable | Meaning |
|---|---|
| `COMMIT_MSG_GATE_BYPASS` | `on` skips the gate. Single-use by convention: set it inline per commit (`COMMIT_MSG_GATE_BYPASS=on git commit ...`). Each use is announced on stderr; the hook writes no durable record of it. |
| `COMMIT_MSG_GATE_PLANS_DIR` | Opt in to slug validation. When set, at least one `plan: <slug>` line in the body must resolve to `<slug>.md` in this directory (the same `<name>.md` layout [`plan-gate`](../plan-gate/SCHEMA.md) uses for `PLAN_GATE_PLANS_DIR`; the file must be a regular, readable file — its `status` is not inspected, so an archived plan still resolves). If the directory is set but missing or unreadable the gate **fails closed** (rejects). Ceremony-prefix commits need no plan and are unaffected. Unset (default): the slug is not validated. |
| `COMMIT_MSG_GATE_PREFIXES` | Override the approved first-line prefix regex (`grep -E` syntax). Default: `chore`/`docs`/`ci`/`build` with optional `(scope)`. |

### Examples

```bash
# Passes — references a plan:
git commit -m "$(printf 'feat: add logout button\n\nplan: add-logout-button\n')"

# Passes — ceremony prefix:
git commit -m "docs: clarify the README quickstart"

# Rejected — functional change with no plan link and no ceremony prefix:
git commit -m "feat: quietly change auth behavior"

# Custom convention (allow a `wip:` prefix too):
COMMIT_MSG_GATE_PREFIXES='^(wip:|chore:|docs:)' git commit -m "wip: scratch"
```

## Catching `--no-verify` at the merge boundary (CI)

A local hook can be skipped (`git commit --no-verify`) or never installed. To check the messages
of every commit a PR adds, copy [`examples/ci-commit-msg-check.yml`](examples/ci-commit-msg-check.yml)
into your repo's `.github/workflows/`. It checks out the PR head with full history and runs the
same `commit-msg-gate.sh` over each commit message in `origin/<base>..HEAD` (merge commits skipped,
`COMMIT_MSG_GATE_BYPASS` ignored). It also rejects empty-message commits (which the gate itself
passes through to git, and which `--no-verify --allow-empty-message` gets past git) and fails
closed if the base ref cannot be resolved. Pair it with `COMMIT_MSG_GATE_PLANS_DIR` (the plans must then be
checked in to the repo CI sees). This is a copyable example — it is not enabled in StrictLock's
own CI, whose commits don't follow the gate's defaults.

## Requirements

`bash`, `grep`, `sed` — present on any standard Linux/macOS install and via Git for Windows.

## License

Apache-2.0. Copyright 2026 TDM Technologies LLC. See [../LICENSE](../LICENSE).
