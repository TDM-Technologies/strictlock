# plan-gate: Structural Human-in-the-Loop for Agentic AI in Regulated Domains

*Part of [StrictLock](../README.md) — the fail-closed governance suite. The reference
implementation accompanying this paper is in this directory: [`plan-gate.py`](plan-gate.py).*

**Thesis.** Prose-governed agents fail open; structurally-governed agents fail closed.
A gate that physically blocks unapproved actions at the tool boundary — combined with
externalized memory — makes agentic AI safe for regulated work, and the same gates emit
compliance evidence as a byproduct.

---

## 1. Problem & context

A new generation of AI agents does not just produce text — it *takes actions*. It edits files,
runs shell commands, calls APIs, moves money, and writes to systems of record. The capability is
real and the leverage is enormous. So is the exposure: in a regulated domain, an action the
operator never approved is not a bad paragraph, it is a disclosed patient record, an
unauthorized transaction, a privileged document sent to the wrong party. The cost of a mistake
is no longer "regenerate the response." It is a reportable event.

How do teams govern these agents today? Overwhelmingly, with **prose**. The system prompt says
"always ask before deleting anything." The instructions say "never touch production." The policy
document says "escalate to a human for anything involving PHI." These are real attempts at
control, and they are better than nothing — but they share a fatal property: **they fail open.**

A control fails open when, the moment it stops working, the dangerous thing happens anyway.
Prose governance is exactly this. The instruction is advisory: the model must *choose* to honor
it on every single turn. Under context pressure, under momentum toward a goal, under an
ambiguous request, the instruction is the first thing to slip — and when it slips, nothing
catches the action. The agent does the thing. The guardrail was a suggestion, and suggestions
are not load-bearing.

Engineers have a name for the opposite posture. A **fail-closed** (fail-safe) system defaults to
the *safe* state when something goes wrong: the brake that engages when air pressure is lost, the
door that locks when the power dies, the valve that shuts when the signal drops. The safe state
is the default, and it takes a positive, authorized signal to leave it. You do not trust the
operator to remember to pull the brake; you build a brake that pulls itself.

The thesis of this paper is that agentic governance must move the same way: **out of the prose
and into a structural gate.** Instead of instructing the agent to ask permission, you place a
mechanism at the tool boundary — between the agent's intent and its effect — that physically
blocks any action falling outside an explicitly approved envelope, *before* the action runs. The
agent cannot talk its way past it, because the check executes in code the agent does not control.
When something is unapproved, ambiguous, or unexpected, the default is *deny*. The floor holds
even when the instructions are forgotten.

This is not a hypothesis. The patterns described here were extracted from a working system built
to run agents against a HIPAA-regulated product — and that origin is the one piece of local color
in this paper, not its frame. This is one system — one team, one domain — and a reader should
weight the evidence accordingly. But nothing about the *argument* depends on that scale or that
domain. A hospital IT team automating record reconciliation, a fintech wiring an agent into ledger
operations, a law firm letting an assistant touch a matter file, and a one-person company all
face the identical structural problem: an autonomous actor with real privileges and a governance
layer that, today, is mostly words. The fix applies to all of them.

The rest of this paper makes the case concretely. §2 describes the architecture the gate sits in
— externalized memory and a separation-of-duties agent model. §3 specifies the gate mechanism
itself and pays off the fail-open/fail-closed distinction. §4 traces how the gate matured, each
step forced by a real failure. §5 — the heart of the paper — is the honest catalog of how agents
actually break and which failures the gate *prevents* versus merely *detects*. §6 shows that the
audit trail you need for SOC 2 and ISO falls out of the gate as a byproduct. §7 is the short
version for anyone who wants to build one.

## 2. Architecture

The gate does not stand alone. It sits inside an architecture with two load-bearing patterns:
**externalized memory** and a **separation-of-duties agent model**. Both exist for governance
reasons, and both are prerequisites for the gate to mean anything.

### 2.1 Externalized memory (the shared blackboard)

An LLM agent is functionally stateless across sessions. Its working memory is the context
window: ephemeral, bounded, and gone the moment the session ends or crashes. If the only record
of "what was approved" and "what has been done" lives in that window, then governance is as
durable as a chat log — which is to say, not durable at all, and not auditable.

The fix is the **shared blackboard**: a single, on-disk state file that serves as the source of
truth for cross-session, cross-agent state. Working memory is *externalized* to durable storage
rather than confined to a context window. Any agent picking up the work reads the blackboard to
learn where things stand; any agent making progress writes back to it. Writes are atomic
(write-to-temp-then-rename) so a crash mid-write cannot corrupt the record.

For governance this is the difference between an opinion and an artifact. State that lives
outside the model can be inspected, version-controlled, diffed, and replayed. An approval
recorded on the blackboard is evidence; an approval that existed only in a since-evicted context
window is a memory of a memory. Externalized state is what lets the gate's decisions become a
durable audit trail (§6) instead of vanishing with the session.

### 2.2 Separation of duties (the three-role model)

The second pattern is **separation of duties** applied to agents — the same control a bank uses
when the person who initiates a wire is not the person who approves it. Three roles, with
deliberately non-overlapping authority:

| Role | Authority | Hard constraint |
|---|---|---|
| **Architect / Planner** | Proposes the plan: the work breakdown, the specs, the exact diffs and commands it intends. | Does **not** execute. Stops and asks before any modification reaches the working system. |
| **Foreman / Executor** | Carries out an *approved* plan: runs the commands, writes the files, runs the tests. | Makes no judgment calls and improvises nothing. Pure execution of what was approved; reports results back. |
| **Reviewer (human)** | Makes every decision: approves plans, modifies scope, gates advancement. | The sole authority that can authorize an action. No silent failures are tolerated. |

The point of splitting these is least privilege and the elimination of a single actor who can
both decide and act. The entity that *proposes* is not the entity that *executes*, and neither
is the entity that *authorizes*. A planner that goes rogue produces only a proposal — it has no
hands. An executor that goes rogue can only run steps that were already approved — it has no
imagination. The human is the only one who can say yes, and saying yes is the only thing the
human is on the hook for.

In the system this was extracted from, the planner and executor happened to be different AI
agents (and could even be different models), and the reviewer was the operator. But the roles
are what matter, not the implementation. You can collapse planner and executor into one agent and
still get most of the benefit — *provided* the boundary between "propose" and "act" is enforced
structurally rather than by the agent's good intentions. That enforcement is the gate, and it is
the subject of §3.

## 3. Core mechanism — structural consent

The core mechanism is a three-phase protocol — **PLAN → CONFIRM → EXECUTE** — and the entire
argument of this paper rests on *how* that protocol is enforced.

- **PLAN.** The agent states, explicitly and in advance, what it intends to do: which files it
  will modify, which commands it will run. This becomes a concrete, inspectable artifact — an
  approved plan — not a vague intention.
- **CONFIRM.** A human reads the plan and responds: yes, no, or modify. Approval is positive and
  specific. Nothing is approved by default, and approval names an envelope — *these* files,
  *these* commands — not a blanket "go ahead."
- **EXECUTE.** The agent may act on confirmed items and *only* confirmed items. An action that is
  not in the approved plan does not run.

Stated that way, this looks like ordinary good practice — the kind of thing a careful prompt
already asks for. The difference, and the whole point, is the enforcement.

### 3.1 A pre-action gate, not a prompt courtesy

In the prose approach, PLAN → CONFIRM → EXECUTE is an *instruction*: text in a system prompt
asking the model to please behave this way. The model is simultaneously the actor and the thing
deciding whether to honor the rule. That is the fail-open posture from §1 — the agent can simply
not do the asking, and when it doesn't, the action still happens.

The structural approach moves the rule out of the prompt and into a **pre-action hook at the
tool boundary**. Every consequential tool call — every file write, every command — is
intercepted *before it executes* and checked against the approved plan. If the action is in the
plan, it proceeds. If it is not, it is denied, and the agent is told why. The check runs in
ordinary code, outside the model's control. The agent cannot reason its way past it, cannot
"decide" the rule doesn't apply this time, cannot forget it under momentum. The rule is no longer
something the agent is asked to follow; it is a condition of the action happening at all.

This is the difference between asking a driver to brake and installing a brake that engages
itself. The agent's cooperation is no longer load-bearing.

### 3.2 Consent over completion

The governing principle behind the gate is what the source system's security policy
calls **"Consent over Completion."** An autonomous system has a natural bias toward
*finishing the task* — that is what it was pointed at. Left unchecked, that bias is exactly the
momentum that runs over the guardrail. The policy inverts the default: the system is built to
**stop and request human consent rather than act autonomously to complete the work.** An agent
that cannot obtain approval does *nothing*. Doing nothing is the safe state, and the system
defaults to it.

The same policy defines a **Hard Stop Protocol**: any unauthorized tool execution or unexpected
system behavior triggers an immediate halt for manual review, rather than an attempt to recover
and proceed. Surprise resolves toward stopping, not toward improvisation. Combined with the
pre-action gate, this is a genuinely fail-closed posture — when the system is uncertain,
unapproved, or surprised, the action does not happen.

### 3.3 Why this is the spine

Two systems can describe their governance with the identical sentence — "the agent asks before
it acts" — and be worlds apart. In one, that sentence is a hope pinned on the agent's behavior;
it fails open the first time the agent is busy. In the other, it is a property of the
infrastructure; it fails closed by construction. Everything in §5 — the catalog of how these
agents actually break — is evidence for a single claim: when the enforcement is structural, the
failures that would have shipped under prose governance get *stopped at the gate* instead.

## 4. Evolution

The gate did not arrive fully formed. It matured through four stages, and — this is the part
worth dwelling on — *each stage was forced by a specific failure*, not designed up front. The
arc runs from "control where output lands" to "control what action runs, against an explicitly
enumerated envelope."

**Stage 1 — the staging gate.** The earliest control was a destination rule: all
agent-generated content had to write to a dedicated *staging directory* (a "Draft" folder), never
directly into the working tree. A human then reviewed the staged output and merged it. The
principle it established is the one everything else builds on — *nothing the agent produces reaches
the real system unreviewed.* But it was coarse. It gated the *destination*, not the *action*, and
it relied on the agent actually writing to the staging area. An agent that wrote somewhere else,
for whatever reason, was outside the control entirely.

**Stage 2 — the audit log.** Next came an append-only log recording every action with a
timestamp, the action taken, and a human-approval flag. This did not block anything new; what it
added was *durability of the record*. Decisions stopped being ephemeral. This is the seed of
"compliance as byproduct" (§6): once every action leaves a durable, structured trace, the audit
trail is no longer a separate feature you have to build.

**Stage 3 — structural consent everywhere.** PLAN → CONFIRM → EXECUTE was promoted from a
per-task convention — something each skill was *supposed* to do — into a shared protocol baseline
that was *enforced* rather than reminded. This is the conceptual jump from §3: the rule moved out
of the individual prompts and into the shared infrastructure. It closed the gap where one
forgetful skill could skip the asking.

**Stage 4 — the plan-gate proper.** The mature form is a pre-action hook keyed on an approved
plan's **allowlist**: an explicit enumeration of the exact files the agent may modify and the
exact commands it may run. Not a directory it may work *in* — the specific files. Not a tool it
may use — the specific command prefixes. The agent cannot act outside the enumerated envelope,
and the envelope is approved per unit of work, then retired.

Two refinements inside Stage 4 are worth calling out because each was paid for by a real
incident:

- **Worktree-anchored path resolution.** An agent working in an isolated copy of a repository
  once wrote to the *main* repository instead, because its plan listed main-repo paths and the
  write "succeeded" — at the wrong target. The fix anchors all path resolution at the session's
  actual working root, with a **hard guard** that blocks *every* tool call if not one authorized
  path resolves under the current working tree. A plan that is misaligned with where the session
  is actually running fails closed, loudly, on the first action — instead of silently writing to
  the wrong place. (This failure is §5's "silent drift / wrong-target writes.")
- **Exact-match authorization.** Listing a *directory* as authorized was, at first, assumed to
  authorize everything inside it. It does not: authorization matches files by *exact* path, and a
  directory entry expands to nothing. The gate emits a warning when a plan lists a directory, to
  surface the mistake immediately rather than as a confusing denial later. Broad grants are a
  fail-open invitation; the gate forces explicit enumeration. (This is §5's "over-broad
  authorization.")

The throughline: every increase in the gate's strictness was the scar tissue of a failure that
slipped through the previous version. That is also why §5 is the heart of the paper — the
governance is only as credible as its contact with the ways agents actually break.

## 5. Failure modes & mitigations

This is the heart of the paper. Governance is only credible if it survives contact with the
ways agents actually break. Below are the failure modes observed in a real, running system, the
mitigation each forced, and — honestly — whether the mitigation *prevents* the failure or only
*detects* it.

| Failure mode | What happened | Mitigation | Prevents or detects? |
|---|---|---|---|
| **Momentum Bias** | The agent barrels from intent to action without a stop, producing changes the human never approved ("invisible agent" anxiety). | PLAN → CONFIRM → EXECUTE made *structural*: a pre-action gate blocks any tool call outside an approved plan. | **Prevents.** |
| **Prose governance ignored** | Instructions like "ask before deleting" are skipped under context pressure. | Move the rule out of the prompt and into a hook the model cannot talk its way past. | **Prevents.** |
| **Silent drift / wrong-target writes** | An agent working in a git *worktree* wrote to the *main* repo because its plan listed main-repo paths. The write "succeeded" — at the wrong target. | Anchor path resolution at the session's worktree root; a hard guard blocks every tool call if no authorized path resolves under the current worktree. | **Prevents.** |
| **Over-broad authorization** | Listing a *directory* as authorized was assumed to authorize everything inside it. | Authorization matches files by *exact* path; directory entries do not expand. The gate warns when a directory is listed. | **Prevents** (forces explicit enumeration). |
| **Write truncation** | A file write was silently truncated mid-content; the agent believed it had written the whole file. | A post-write checksum/verify step warns when output length looks wrong. Root cause unresolved. | **Detects only** — honest gap. |
| **Cache / sync lag** | A cross-filesystem cache (virtiofs over a synced folder) served stale file contents after an edit, so the agent read pre-edit state — and signaled nothing, proceeding on the stale read with full confidence. The root cause (host-side writes not invalidating the guest cache) took five debugging sessions to isolate, *because* the failure is silent. | Fresh session/mount per handoff; longer term, route all file I/O through a single executor. | **Mitigates operationally**, not a true fix. |
| **Auth-loop unreliability** | An OAuth flow was reliable enough for interactive use but flaky under unattended automation. | Assessed and documented; an alternative auth path is kept as the automation primary. | **Detects + routes around.** |

### Two live war stories (from the session that produced this draft)

Both happened *while writing this paper* — which is the strongest possible evidence that the
enforcement is structural rather than advisory.

**1. The gate blocked its own author's agent.** Mid-session, the assistant tried to save a
routine note to its own memory directory. The gate denied it: no approved plan authorized that
path. Nothing about the action was dangerous — it was the agent's own scratchpad. That is the
point. A structural gate does not reason about intent or benignity; it blocks anything outside
the approved envelope, including the operator's own trusted tooling. Prose governance would have
waved a "harmless" write through; the gate did not. The fix was the correct one — *widen the
approved plan to name the path explicitly* — not "trust the agent because it meant well."

**2. A tightly-scoped bypass missed by one path.** An always-allow carve-out had been added so
agent memory could be written without ceremony — but it was scoped to one specific memory
directory (a project-specific path). A *different* memory directory, one character of path apart,
was not covered, so writes there were denied unexpectedly. The lesson cuts both ways: tight
bypass scoping keeps the blast radius small (good) but produces "why didn't it work *here*?"
surprises (cost). The resolution was to add an explicit authorization for the specific path —
**not** to broaden the bypass to "all memory directories." Choosing the narrow, surprising fix
over the broad, convenient one is the discipline the whole system is about.

### Compliance falls out of the failure handling

Note what the mitigations produce as a side effect: every denied action is appended to a
structured log; every gate decision (allow and deny) is recorded with timestamp, target, and the
governing plan. Nobody wrote an "audit logging feature" — the audit trail is the *exhaust* of the
gate doing its job. That exhaust is exactly what SOC 2 system-monitoring and change-management
criteria ask you to evidence (see §6). The failure-handling machinery and the compliance
machinery are the same machinery.

## 6. Compliance mapping (condensed)

§5 ended on the observation that the failure-handling machinery and the compliance machinery are
the same machinery. This section makes that concrete. The claim is narrow and important: **you do
not build these controls to pass an audit; you build them to keep the agent safe, and the audit
evidence is what they emit while doing that job.** Below is the one-page mapping from gate
mechanism to the recognized control it satisfies. (This work is measured against a broader
internal compliance-framework backlog, maintained separately.)

| Gate mechanism | Evidence it emits (as a byproduct) | Maps to |
|---|---|---|
| **Allowlist-scoped, exact-path authorization** — an agent may touch only the specific files and run only the specific commands an approved plan enumerates; least privilege by construction. | Per-plan record of exactly what each agent was permitted to access, scoped per unit of work and then retired. | **SOC 2 CC6** (logical access / least privilege); **ISO/IEC 42001** (controlled operation of the AI system) |
| **Append-only decision log** — every gate decision, *allow and deny*, recorded with timestamp, target, command, and the governing plan. | A continuous, tamper-evident monitoring trail of system activity and policy-violation attempts. | **SOC 2 CC7** (system monitoring / security logging); **ISO/IEC 42001** (AI system logging & monitoring) |
| **PLAN → CONFIRM → EXECUTE + staging review** — no change reaches the working system without an explicit, recorded human approval of a specific plan. | Documented approve→act→record cycle for every change; an auditor can trace any modification back to the approval that authorized it. | **SOC 2 CC8** (change management); **ISO 9001 §8.5** (controlled production / service provision) |
| **The approved plan as a documented artifact** — each unit of work is a versioned, inspectable plan stating scope, paths, and commands. | Versioned, retained documentation of intended work and its authorization. | **ISO 9001 §7.5** (documented information) |
| **Hard-stop protocol + human-in-the-loop reviewer** — unexpected behavior halts for review; a human is the sole authorizing authority. | Demonstrated human oversight and a defined escalation path for anomalous AI behavior. | **ISO/IEC 42001** (human oversight of AI); **ISO 9001 §10.2** (nonconformity & corrective action) |
| **Per-failure-mode register** (the §5 table) — known failure modes tracked with mitigation and an honest prevents-vs-detects status. | A live risk register tied to observed incidents and their controls. | **ISO 9001 §6.1** (risk-based thinking); **SOC 2 CC9** (risk assessment) |

Two things to notice. First, every row's middle column is *exhaust* — nobody wrote an "audit
logging feature" or a "least-privilege module"; the evidence is what the gate produces while
simply doing its job of keeping the agent inside an approved envelope. Second, the same handful of
mechanisms satisfies criteria across three different frameworks at once — a security framework
(SOC 2), a quality framework (ISO 9001), and an AI-management framework (ISO/IEC 42001) — because
all three are, at bottom, asking the same question: *can you show that this system only did what
it was authorized to do, and prove it after the fact?* A fail-closed gate answers yes by
construction. This is a mapping, not a full closure log; the point is only to show where the
evidence already lives.

## 7. What we'd tell others

If you take one thing from this paper, take this: **do not ask an agent to behave — build a
floor it cannot fall through.** Every governance approach that lives in prose is a request the
agent can decline, and under enough pressure it eventually will. The only governance you can rely
on is the kind that runs in code the agent does not control and that defaults to *deny* when it is
unsure.

Concretely, four moves carry almost all the weight:

1. **Put the rule at the tool boundary, not in the prompt.** A pre-action hook that checks every
   consequential call against an approved plan is a small amount of code and it changes your risk
   posture immediately. Start there.
2. **Fail closed.** When an action is unapproved, ambiguous, or surprising, the default is to
   block it and stop for a human. Bias the whole system toward consent over completion.
3. **Enumerate; never grant broadly.** Authorize the exact files and the exact commands, not the
   directory or the tool. Broad grants are fail-open invitations wearing the costume of
   convenience.
4. **Let the floor be your audit trail.** Don't build a separate "compliance feature." Log every
   gate decision and you will find you have already produced most of what SOC 2 and ISO ask you
   to evidence (§6).

None of this requires a large team or a greenfield system. The gate described here was extracted
from a working product, and its strictest features are scar tissue from real failures (§5) — which
means you can adopt it incrementally, letting your own failures tell you where to tighten next.

A sanitized reference implementation of the gate is included alongside this paper in this
directory (`plan-gate.py`), and the whole project is public at
**https://github.com/TDM-Technologies/strictlock** (the `plan-gate/` module). The project is
intended to be **open-core**: the mechanism is meant to be read, copied,
and adapted to whatever regulated work you are pointing your agents at. Build the floor. Let it
hold.
