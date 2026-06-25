# Compliance mapping

**How StrictLock's gates emit audit evidence — and which recognized control each
mechanism maps to.**

This is the suite-wide expansion of [`plan-gate/paper.md`](plan-gate/paper.md) §6. It
covers the shipped modules — [`plan-gate`](plan-gate/),
[`commit-msg-gate`](commit-msg-gate/), [`memory-cap`](memory-cap/),
[`externalized-memory`](externalized-memory/), and the generated-sink integrity gates
([`generated-sink-commit-gate`](generated-sink-commit-gate/) /
[`generated-sink-prepush-gate`](generated-sink-prepush-gate/)) — and maps each gate
mechanism to the control it evidences and to what an auditor can actually **trace**.

## Read this first: what this document is, and is not

This is a **mapping, not a certification.** It shows where audit evidence already lives
in mechanisms that ship in this repo — it does not assert that any framework's
requirements are met, that controls are operating effectively, or that a real
compliance program exists for your deployment. The real compliance framework (control
narratives, scope, period-of-time evidence, an auditor's opinion) is a separate
artifact, maintained separately, and nothing here substitutes for it.

The claim is narrow and load-bearing, and it is the one the paper makes:

> **You do not build these controls to pass an audit; you build them to keep the agent
> safe, and the audit evidence is what they emit while doing that job.**

Every control below maps to a mechanism that **really exists in a shipped module** — no
invented controls, no aspirational rows. Where a mechanism only *detects* rather than
*prevents*, or only *supports* a control rather than satisfying it, this document says
so. Overclaiming compliance is itself a fail-open posture; this document fails closed.

A note on framework citations: the control identifiers (SOC 2 Trust Services Criteria
`CC6`–`CC9`, ISO 9001 clause numbers, ISO/IEC 42001) name *where the evidence is
relevant*. They are pointers into the published criteria, not a statement that the
criterion is satisfied. An auditor decides that — these mechanisms give them something
concrete to look at.

## How to read the mapping

Each row answers three questions:

1. **Mechanism** — what the gate actually does, and which shipped module it lives in.
2. **Evidence it emits** — the artifact the mechanism produces *as a byproduct* of doing
   its job. Nobody wrote an "audit logging feature"; the trail is the exhaust of the gate
   keeping the agent inside an approved envelope.
3. **What an auditor can trace** — the concrete path from an observed event back to its
   authorization, which is what most controls are really asking you to demonstrate.

The mappings are framed against **your agent harness** generically. The modules are
built and worked-through against [Claude Code](https://docs.claude.com/en/docs/claude-code)'s
`PreToolUse` / `commit-msg` hook conventions as the concrete example, but the mechanism
is harness-agnostic — adapt the stdin/stdout JSON contract (or the git-hook shape) to
whatever runtime you point your agents at, and the same evidence falls out. Claude Code
is the worked example, not the only supported target.

---

## The mapping

### A. Exact-path, exact-command authorization → SOC 2 CC6 (logical access / least privilege)

**Mechanism** ([`plan-gate`](plan-gate/)). Before an agent edits a file or runs a
command, a pre-action hook reads the single active plan and decides allow/deny. An
`Edit`/`Write` target must *exactly* match an `allowed_paths` entry — a directory entry
authorizes **nothing** inside it; each file is enumerated. A `Bash`/`PowerShell` command
must *start with* an `allowed_commands` prefix (or be a known read-only command). The
envelope is approved per unit of work, then retired. This is least privilege by
construction: the agent can touch only what an approved plan names, scoped to the session,
and nothing broader.

**Evidence it emits.** A per-plan record of exactly which files and commands each agent
was permitted to use, for one specific unit of work, with explicit start and retirement.
Authorization is a structured artifact, not a standing grant nobody reviews.

**What an auditor can trace.** For any change the agent made, *which named files and
commands were authorized, by which plan, for which unit of work* — and confirmation that
the grant was scoped and then retired rather than left standing. Over-broad grants surface
as warnings (a listed directory authorizes nothing and the gate says so), so the absence
of silent wildcard access is itself observable.

**Maps to:** **SOC 2 CC6** (logical access controls / least privilege). Relevant also to
**ISO/IEC 42001** (controlled operation of the AI system).

### B. Append-only decision log → SOC 2 CC7 (system monitoring / security logging)

**Mechanism** ([`plan-gate`](plan-gate/)). When a decision-log directory is configured
(`PLAN_GATE_LOG_DIR`), the gate appends every denial — and the design intent is the full
allow/deny record — with timestamp, target, command, and the governing plan. The log is
the *exhaust* of the gate doing its job; no separate logging feature was built. Durability
of that record across stateless sessions is what [`externalized-memory`](externalized-memory/)
provides for the surrounding state (see row E): decisions stop being ephemeral chat and
become an on-disk trace.

**Evidence it emits.** A continuous, append-only monitoring trail of system activity and
policy-violation *attempts* — every time the agent tried to step outside its envelope and
was stopped.

**What an auditor can trace.** A timeline of authorized and attempted-unauthorized actions:
*what was attempted, when, against what target, under which plan, and whether it was allowed
or denied.* Denied actions are first-class evidence — they show the control engaging, not
just nothing-bad-happened.

> **Honest scope.** The shipped gate appends *denials* by default; capturing the full
> allow-and-deny stream, and protecting the log against tampering (append-only storage,
> off-host shipping, integrity sealing), are deployment responsibilities the module does
> not perform for you. The mechanism produces the trail; making it tamper-*evident* in your
> environment is your control to operate.

### C. PLAN → CONFIRM → EXECUTE + staging review → SOC 2 CC8 / ISO 9001 §8.5

**Mechanism** ([`plan-gate`](plan-gate/), with [`commit-msg-gate`](commit-msg-gate/) at
the version-control boundary). No change reaches the working system without an explicit,
recorded human approval of a specific plan. The agent **proposes** (PLAN); a human reads
it and approves an envelope of specific files and commands (CONFIRM); the agent may act on
confirmed items and *only* confirmed items (EXECUTE). The earliest form of the control
staged all agent output to a review directory before it merged — *nothing the agent
produces reaches the real system unreviewed.* [`commit-msg-gate`](commit-msg-gate/) closes
the loop at commit time: a `commit-msg` git hook rejects any commit that does not either
reference an approved plan (`plan: <slug>`) or carry an approved ceremony prefix
(`chore:`/`docs:`/`ci:`/`build:`), so the version-control history itself links every
functional change back to its authorization.

**Evidence it emits.** A documented approve → act → record cycle for every change, plus a
git history in which each functional commit names the plan that authorized it.

**What an auditor can trace.** *Any modification back to the approval that authorized it* —
from a committed change, to the plan referenced in its commit message, to the approved
allowlist that scoped it, to the human confirmation that opened the envelope. The chain is
mechanical, not reconstructed after the fact.

**Maps to:** **SOC 2 CC8** (change management); **ISO 9001 §8.5** (controlled production /
service provision).

### D. The approved plan as a documented artifact → ISO 9001 §7.5 (documented information)

**Mechanism** ([`plan-gate`](plan-gate/); state side: [`externalized-memory`](externalized-memory/)).
Each unit of work is a versioned, inspectable plan file — YAML frontmatter stating scope,
the exact `allowed_paths`, and the exact `allowed_commands` — that the gate reads as the
authorization of record. It is not a vague intention in a chat window; it is a file that
can be version-controlled, diffed, and retained. The shared blackboard
([`externalized-memory`](externalized-memory/)) holds the surrounding cross-session state —
what was approved and what has been done — as an on-disk artifact written atomically, so it
too is documented information rather than a memory of a memory.

**Evidence it emits.** Versioned, retained documentation of intended work, its scope, and
its authorization — and a durable state record of the work's progress.

**What an auditor can trace.** *The intended scope of a unit of work, who authorized it, and
the state of record at any handoff* — by reading the plan file and the blackboard, both of
which are real, diffable artifacts under version control, not testimony.

**Maps to:** **ISO 9001 §7.5** (documented information). Relevant also to **SOC 2 CC8** (the
plan as the change-management record).

### E. Externalized, atomically-written state with a staleness tripwire → durable, auditable record (supports CC7 / ISO 9001 §7.5)

**Mechanism** ([`externalized-memory`](externalized-memory/)). One on-disk state file per
project is the single source of truth across stateless agent sessions: any session reads it
first and writes it last. Writes go through an atomic write-to-temp-then-`os.replace`, so a
crash mid-write can never corrupt or half-write the record — the rename either happens or it
does not. The file records the git ref it was written against, giving every session a
session-opening **staleness tripwire** so it distrusts a cached or stale read before acting
on it. Distinct files for distinct jobs (state / mailbox / archive) keep the record readable
at a glance.

**Evidence it emits.** A durable, version-controlled record of cross-session state — what was
approved, what is in flight, what is done — that survives session death and cannot be silently
corrupted, with the git anchor making "is this current?" an observable question rather than a
hope.

**What an auditor can trace.** *The state of the work at any point, and that the record was
written durably against a known git ref* — so a reviewer can replay how state moved and confirm
the record was not a transient chat artifact. Detail offloaded to the archive remains
append-only history.

**Maps to:** **supports SOC 2 CC7** (durable, reviewable record of system state) and **ISO 9001
§7.5** (documented information, durably retained). This is a *supporting* substrate — it makes
the other controls' evidence durable and auditable; it is not itself an access or
change-management control.

### F. Hard-stop protocol + human-in-the-loop reviewer → ISO/IEC 42001 / ISO 9001 §10.2

**Mechanism** (governing posture across the suite; enforced by [`plan-gate`](plan-gate/)).
Any unauthorized tool execution or unexpected system behavior triggers an immediate halt for
manual review rather than an attempt to recover and proceed — surprise resolves toward stopping,
not improvisation. A human is the **sole authorizing authority**: the entity that proposes is not
the entity that executes, and neither is the entity that authorizes. When the system is uncertain,
unapproved, or surprised, the action does not happen — fail closed, by construction. A
worktree-anchored hard guard blocks *every* tool call if no authorized path resolves under the
current working tree, so a plan misaligned with where the session is actually running fails closed
loudly on the first action.

**Evidence it emits.** Demonstrated human oversight of the AI system and a defined escalation path
for anomalous behavior — every authorization carries a human signature, and every surprise produces
a halt rather than an unreviewed action.

**What an auditor can trace.** *That a human authorized each envelope, and that anomalies halted for
review* — the human approval is in the record (rows C/D), and a denied/halted action in the log (row
B) evidences the escalation path engaging.

**Maps to:** **ISO/IEC 42001** (human oversight of the AI system); **ISO 9001 §10.2** (nonconformity
& corrective action — the halt-for-review on unexpected behavior).

### G. Per-failure-mode register → ISO 9001 §6.1 (risk-based thinking) / SOC 2 CC9 (risk assessment)

**Mechanism** ([`plan-gate/paper.md`](plan-gate/paper.md) §5). Known failure modes are tracked in a
living register with the mitigation each forced and an **honest prevents-vs-detects status** — including
the gaps the suite only *detects* or *operationally mitigates* rather than truly fixes (e.g. write
truncation: detect-only; cross-filesystem cache lag: operational mitigation, not a fix). The register
is tied to observed incidents in a real running system, not a hypothetical threat model.

**Evidence it emits.** A live risk register linking observed incidents to their controls and to an honest
assessment of how completely each is addressed.

**What an auditor can trace.** *Which risks were identified, what mitigates each, and the residual gap* —
a documented risk-based-thinking trail with the honesty (open gaps named as gaps) that makes a risk
register credible.

**Maps to:** **ISO 9001 §6.1** (risk-based thinking); **SOC 2 CC9** (risk assessment).

### H. Structural cap on auto-loaded memory, with a per-gate bypass → controlled operation / configuration integrity (ISO/IEC 42001; supports CC6/CC7)

**Mechanism** ([`memory-cap`](memory-cap/)). A pre-action hook refuses any write to the configured
memory-*index* file that would introduce an over-length index entry (default 200 chars per `- ` line),
denying at the boundary before the bad write lands — the same fail-closed posture as the rest of the
suite, applied to a different resource (bounded per-session context cost). Its bypass variable is
*distinct* (`MEMORY_CAP_BYPASS`), deliberately **not** shared with `plan-gate` or `commit-msg-gate`, so
a narrow, deliberate escape from one cap can never silently disable the others. A bad path-matching regex
falls back to the default rather than silently disabling the cap — a misconfiguration cannot quietly turn
the control off.

**Evidence it emits.** A structurally-enforced operational bound (rather than a convention nobody enforces),
plus a configuration design in which disabling one gate cannot silently disable another and a misconfiguration
fails *toward* the control being on.

**What an auditor can trace.** *That a stated operational limit is enforced in code rather than by convention,
and that the controls' bypasses are independent* — the per-gate bypass isolation and the fail-to-default regex
behavior are observable in the configuration.

**Maps to:** **ISO/IEC 42001** (controlled operation of the AI system); *supports* **SOC 2 CC6/CC7** at the
configuration-integrity level (independent bypasses; fail-closed misconfiguration). This is a resource-control
and config-integrity mechanism, not a primary access or change-management control.

### I. Byte-exact generated-artifact integrity at the commit/push boundary → SOC 2 CC7/CC8 / ISO 9001 §8.5

**Mechanism** ([`generated-sink-commit-gate`](generated-sink-commit-gate/) at commit time,
[`generated-sink-prepush-gate`](generated-sink-prepush-gate/) as the push-time backstop). A
checked-in *generated* artifact — a manifest, index, schema, OpenAPI spec, or generated
README — is supposed to be a pure function of its sources, but nothing stops a hand-edit or a
stale commit from letting it drift. These gates re-run the configured generator in `--check`
mode and **refuse** (loud non-zero exit, no working-tree mutation, no auto-stage) unless the
committed artifact is a *byte-exact* regeneration. The commit gate fires only when a staged
change touches a configured source; the push gate validates the terminal artifact on *every*
push, so a stale sink that slipped past commit time (via `--no-verify` or a one-shot bypass)
is still caught before it leaves the machine. Both fail closed on a missing, misconfigured, or
erroring generator — a generator that cannot run is a block, never a silent allow — and each
gate honors only its own uniquely-named, single-use, logged bypass, so escaping one never
weakens the other.

**Evidence it emits.** Proof that a derived artifact of record matches its source by
construction at the moment it enters version control, plus — when a decision-log directory is
configured — a per-decision append-only trail of each freshness check and every refusal (the
same exhaust pattern as row B).

**What an auditor can trace.** *That every committed/pushed generated artifact provably
corresponds to the sources it claims to derive from* — a drifted or hand-edited artifact cannot
enter the history unobserved — and, from the log, *when a stale artifact was caught and
blocked.* The bypass isolation and fail-closed-on-misconfiguration posture are observable in
the configuration.

**Maps to:** **SOC 2 CC8** (change management — a generated artifact cannot drift from its
authorized source) and **CC7** (the decision log); **ISO 9001 §8.5** (control of production /
preservation of conformity of outputs). Like [`memory-cap`](memory-cap/) (row H), the
independent bypasses and fail-toward-on misconfiguration also *support* configuration integrity.

---

## Two things to notice

**First, every "evidence" column is exhaust.** Nobody wrote an "audit logging feature" or a
"least-privilege module." The evidence is what the gates produce while simply doing their job of
keeping the agent inside an approved envelope. The failure-handling machinery and the compliance
machinery are the same machinery.

**Second, the same handful of mechanisms satisfies criteria across three different frameworks at
once** — a security framework (SOC 2), a quality framework (ISO 9001), and an AI-management framework
(ISO/IEC 42001) — because all three are, at bottom, asking the same question:

> *Can you show that this system only did what it was authorized to do, and prove it after the fact?*

A fail-closed gate answers **yes by construction**. This document is a mapping, not a full closure log;
its only job is to show **where the evidence already lives** so that, when a real compliance program
needs it, it is already being produced.

---

## License

Apache-2.0. Copyright 2026 TDM Technologies LLC. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
