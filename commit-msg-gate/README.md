# commit-msg-gate

**Require every commit to link back to an approved plan.**

A [StrictLock](../README.md) module. `commit-msg-gate` is a tiny `commit-msg` git hook that
enforces the version-control half of the governance loop: a commit must either reference an
approved plan or be an explicitly-labelled "ceremony" commit. The result is a git history
where every functional change traces back to an authorization — the change-management
evidence [`plan-gate`](../plan-gate/) describes in its paper (§6), applied at the commit.

## What it does

A commit message passes if **either**:

- **(a)** its first line starts with an approved *ceremony* prefix — by default
  `chore:`, `docs:`, `ci:`, `build:` (with optional `(scope)`) — for changes that don't need
  a plan link; **or**
- **(b)** its body contains a line matching `plan: <slug>`, naming the approved plan the
  change was made under.

Otherwise the commit is rejected with a message explaining both options.

## Install

```bash
cp commit-msg-gate.sh /path/to/your/repo/.git/hooks/commit-msg
chmod +x /path/to/your/repo/.git/hooks/commit-msg
```

It applies to **every** commit in that repo — human or agent.

## Configuration (environment)

| Variable | Meaning |
|---|---|
| `COMMIT_MSG_GATE_BYPASS` | `on` skips the gate for one commit. Use deliberately: `COMMIT_MSG_GATE_BYPASS=on git commit ...` |
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

## Requirements

`bash`, `grep`, `sed` — present on any standard Linux/macOS install and via Git for Windows.

## License

Apache-2.0. Copyright 2026 TDM Technologies LLC. See [../LICENSE](../LICENSE).
