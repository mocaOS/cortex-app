# Regenerative Software

## A repository translation and operating playbook for coding agents

**Version:** 2.21.0

**Purpose:** Make useful software evolution cheaper and more reliable by preserving product knowledge, establishing changeable boundaries, and verifying that implementations can evolve or be replaced without losing essential behavior or state.  
**Inspiration:** Chad Fowler's regenerative software concept.

**This revision:** makes optional-value projection preserve empty versus null/missing values, separates actual producer behavior from schema-tolerance probes, binds failure metadata to the phase that produced it, and distinguishes consumed stage inputs from later knowledge addenda. It also requires checking actual writer scope and preflighting ancillary execution resources before claiming offline verification. Earlier selected-case, candidate-retention, stage-receipt, scope, completion and retained-evidence rejudgment rules remain in force.

This file is a portable execution brief. It contains the principles, discovery method, design requirements, implementation workflow, evaluation strategy, and completion criteria needed to apply the approach. It requires no particular language, framework, agent harness, deployment platform, or repository layout.

The desired result is a working product that becomes easier to change correctly. Deliver code improvements where justified, executable behavioral knowledge, useful documentation, trustworthy evidence, and a repeatable replacement procedure. A report alone is insufficient in an implementation engagement; a large rewrite is unnecessary.

This is an engineering method, not a promise that any repository can be made fully regenerable at reasonable cost. Apply it to valuable boundaries, make remaining coupling visible, and earn broader claims through evidence. The compiler metaphor describes a workflow; natural-language intent and generated implementations still require judgment.

**The central question:** If a chosen implementation disappeared, could another engineer or agent reconstruct an acceptable replacement from the surviving knowledge, verify it independently, and introduce it without surprising its consumers?

**The economic test:** Does the next useful change require less rediscovery, coordination, and verification effort without degrading the product? Replaceability earns its cost through useful evolution, not through the number of implementations generated.

---

## Contents

1. [Activate this playbook](#1-activate-this-playbook)
2. [Define the target](#2-define-the-target)
3. [Establish the engagement and durable workspace](#3-establish-the-engagement-and-durable-workspace)
4. [Coordinate the lead agent and subagents](#4-coordinate-the-lead-agent-and-subagents)
5. [Discover the actual product](#5-discover-the-actual-product)
6. [Extract and validate intent](#6-extract-and-validate-intent)
7. [Define the architectural target and contracts](#7-define-the-architectural-target-and-contracts)
8. [Match pace and rigor to architectural position](#8-match-pace-and-rigor-to-architectural-position)
9. [Build evaluations that survive replacement](#9-build-evaluations-that-survive-replacement)
10. [Evaluate probabilistic and agentic behavior](#10-evaluate-probabilistic-and-agentic-behavior)
11. [Make documentation part of the product contract](#11-make-documentation-part-of-the-product-contract)
12. [Preserve provenance and operational evidence](#12-preserve-provenance-and-operational-evidence)
13. [Make state and migrations survive replacement](#13-make-state-and-migrations-survive-replacement)
14. [Run the regeneration pipeline](#14-run-the-regeneration-pipeline)
15. [Exercise replacement and reconstruction](#15-exercise-replacement-and-reconstruction)
16. [Retire implementations and compact the system](#16-retire-implementations-and-compact-the-system)
17. [Keep the feedback loop operating](#17-keep-the-feedback-loop-operating)
18. [Adapt to different repository and product types](#18-adapt-to-different-repository-and-product-types)
19. [Use the implementation templates](#19-use-the-implementation-templates)
20. [Walk through a worked example](#20-walk-through-a-worked-example)
21. [Recognize failure modes](#21-recognize-failure-modes)
22. [Finish with evidence and a usable handoff](#22-finish-with-evidence-and-a-usable-handoff)

---

## 1. Activate this playbook

Place this file in a repository root or another location the agent can read. Use this instruction:

```text
Read REGENERATIVE-SOFTWARE.md completely and apply it to this product.
Follow the instruction hierarchy and the repository's applicable rules.

Start by reading the repository's own instructions and inspecting the current
working state. Discover the product, its contracts, data, consumers, tests,
evaluations, documentation, and operational evidence. Use focused subagents
when permitted and useful; coordinate their scope and writes.

Discover how the intended coding harnesses load project instructions. Install
the seven-primitive operating contract in the canonical session entry point,
with scoped guidance and working pointers for the supported entry paths.
Improve that instruction structure as each slice teaches you something useful;
verify a fresh session can use it without this conversation.

Execute the requested translation in bounded slices. Start with one valuable,
tractable capability; extract its durable knowledge; strengthen its behavioral
evaluations; make the justified implementation and documentation improvements;
and exercise replacement in isolation when the prerequisites are available.
For a repository-wide request, inventory the capabilities, prioritize by product
value and change obstacles, and continue through the agreed scope rather than
stopping after the first slice. Record the expected benefit for the next likely
change and test that benefit when the change is in scope.

Reuse the project's existing tooling and documentation structure. Preserve
unrelated work. Keep established behavior unless an intentional behavior change
is explicitly in scope. Record unresolved claims instead of inventing answers.

Leave reproducible verification evidence, the exact completion state, remaining
obstacles, and the next actionable slice. Follow this playbook's default scope
where I have not supplied a more specific one.
```

### 1.1 Default operating scope

When the request is simply to apply this file:

- **Mode:** `translate` — discover, extract knowledge, implement coherent improvement slices, and verify them locally.
- **Product scope:** the current repository and explicitly supplied companion repositories. Inspect referenced public documentation when relevant. Discover external dependencies without assuming permission to modify them.
- **Change scope:** begin with one business capability or independently changeable concern. Use a one-slice pilot when no wider objective is given; an explicit repository-wide or product-wide request establishes a multi-slice campaign. Keep each slice bounded and name necessary dependency changes before expanding it.
- **Execution environment:** local development or an explicitly designated disposable test environment. Deployment follows the project's existing release process and the engagement's authority.
- **Compatibility:** preserve supported consumers and persisted state. Record proposed changes to promises separately from changes behind them.
- **Generation budget:** begin with one candidate and diagnose each failure before another repair. Continue while the diagnosis supports concrete progress within the actual engagement budget. Repeated failures without new evidence trigger a revised strategy, a prerequisite task, or a precise blocker; neither an arbitrary retry count nor endless retries defines completion.
- **Completion target:** verified improvements covering the requested scope, an operational session-instruction hierarchy where edits are permitted, and a replayable handoff. A first successful slice is a checkpoint in a wider campaign. Claim instruction adoption, replacement or reconstruction only to the extent each was actually demonstrated.

Optional modes:

| Mode | Deliverable |
|---|---|
| `assess` | Evidence-backed product map, contradictions, replacement obstacles, and prioritized next slice; no implementation expected |
| `translate` | Verified improvement slices across the requested scope, plus the durable knowledge needed to preserve them |
| `regenerate <unit>` | A replacement for a unit whose intent, architectural target, and evaluation gate are ready |
| `rehearse <unit>` | An isolated experiment testing whether the implementation can be reconstructed from durable artifacts |
| `compact <scope>` | Verified removal or consolidation of obsolete implementation paths, interfaces, configuration, tests, or concepts |

The same workflow supports all modes. Select applicable phases using Section 1.4. Record exclusions that affect a claimed result; unrelated techniques do not need a checklist of exemptions.

### 1.2 Read once, execute from a small packet

On initial adoption, read the complete playbook before choosing a strategy; continue past truncated tool output. Then work from the execution loop below and a small repository-specific packet that links to canonical artifacts. On resume, check the playbook version, repository instructions, changed inputs, and checkpoint; revisit affected sections rather than repeating discovery.

The lead agent owns the whole workflow; a specialist receives the applicable rules, accepted artifact versions, and a precise work package. Reading only a role description is insufficient when a task changes contracts, state, evaluation criteria, or retirement conditions. This guide is subordinate to the engagement's instruction hierarchy and authority.

State the mode, scope, intended evidence claim, and available verification environments early. A repository-wide objective must not silently become a convenient leaf-module pilot. Where repository rules permit it, make the instruction hierarchy in Section 3.2 an explicit work item: install a concise per-session operating contract early enough to guide the first slice, then refine it from actual work. Later sessions should inherit the method through the files their harness loads, without needing the original conversation or rereading this entire playbook.

**MUST** identifies a condition necessary for the claim being made. **SHOULD** identifies a default that may be adapted with a recorded reason. Templates describe information to preserve, not a requirement to create every suggested file.

### 1.3 Execute the next useful action

At each checkpoint, execute this loop for the active slice:

1. **Choose:** name the outcome, boundary, and reason to invest. Retain the implementation when change has no justified benefit.
2. **Understand:** inspect the affected journey, state, consumers, and existing checks. Resolve only uncertainties needed for this slice; keep independent work moving.
3. **Specify:** record preserved promises and intentional changes. Identify the oracle, required checks, baseline, and recovery needs before judging a candidate.
4. **Change:** implement the smallest complete patch, seam repair, replacement, or migration, including affected documentation and consumers.
5. **Verify:** exercise the identified integrated candidate, including affected product journeys. Diagnose failures; never lower the gate to fit the candidate.
6. **Harvest:** retain decisions and evidence, remove justified temporary complexity, record the exact outcome, and continue to the next in-scope slice. When work exposes a missing, misleading or undiscoverable instruction, repair its owning root/scoped guide in the same slice and check the affected entry path (Sections 3.2 and 11.3).

The minimum durable packet answers **why, what must hold, where change belongs, how to verify, what happened, and what comes next**. References to existing artifacts count. One short record and the existing tests can be enough; use the compact template in Section 19.1. Establish a check for an important uncovered promise, rather than generating new tests that merely mirror a low-impact edit.

### 1.4 Select the necessary depth

| Slice condition | Additional work that matters |
|---|---|
| Ordinary patch or local refactor | Relevant intent, boundary, existing regression checks, diff review, and concise evidence |
| Weak or entangled boundary | Dependency/state discovery and the seam-repair procedure in Section 7.6 |
| New or intentionally changed behavior | Explicit acceptance delta and compatibility treatment in Section 6.7 |
| Replacement or reconstruction claim | Survival pack, reusable behavioral gate, substitution evidence, and Section 15 |
| Persistent state, mixed versions, or external effects | Compatibility, reconciliation, and recovery in Section 13 |
| Runtime probabilistic behavior | The calibrated evaluations in Section 10 |
| Deployment or retirement | Stage-specific release, observation, and removal evidence in Sections 14 and 16 |
| Repository-wide campaign | Capability inventory, prioritized slices, and coverage of the important end-to-end journeys |

Choose depth from actual consequences. Several rows may apply. A documentation correction does not need a replacement rehearsal; a one-line change to financial rounding may need historical-state and consumer evidence. Increase rigor when an assumption or failure consequence warrants it, not to fill the playbook's headings.

---

## 2. Define the target

### 2.1 System identity and durable assets

A product retains its identity through the promises people and other systems depend on:

- Useful outcomes and domain rules.
- Inputs, outputs, protocols, and supported interaction patterns.
- Data meaning, ownership, durability, and lifecycle.
- Authorization, isolation, privacy, and other applicable constraints.
- Error semantics, ordering, timing, resource limits, and recovery behavior.
- User and operator workflows, including accessibility and operational expectations.

Source code realizes these promises today. Durable knowledge makes it possible to realize them again tomorrow. Store that knowledge where replacement of the implementation cannot erase it.

Keep four things distinguishable:

1. **What should be true:** accepted intent and contracts.
2. **What is observed:** implementation behavior and operational evidence, including defects.
3. **What is proposed:** candidate requirements, architecture, or implementation changes.
4. **What has been demonstrated:** evaluations and operations performed against identified artifacts.

None of these automatically substitutes for another. A production behavior may be an undocumented obligation or a bug. A specification may be incomplete. A green test suite may miss the important failure. Record and resolve the discrepancy.

### 2.2 The seven primitives

| Primitive | Question | Durable expression | Practical demonstration |
|---|---|---|---|
| **Intent** | What must remain true, for whom, and why? | Scoped requirements, invariants, negative constraints, evidence and rationale | A replacement author can explain the obligations without reading the old implementation |
| **Compilation** | What architectural shape must an implementation fit? | Boundaries, ownership, allowed communication, runtime constraints, explicit freedoms | Candidate fits the accepted architecture without silently redesigning neighbors |
| **Evaluations** | How do we judge whether promises survived? | Boundary-level behavioral checks, fixtures, properties, quality and operational criteria | The same meaningful assertions can judge multiple implementations |
| **Provenance** | Why does the system work this way, and what produced this artifact? | Decisions, rejected alternatives, incident lessons, versioned inputs and results | A future maintainer can recover the reason for an unusual constraint |
| **Pace** | How cautiously should this layer evolve? | Compatibility policy, risk-based gates, observation windows, release constraints | Verification and rollout match actual dependency and recovery characteristics |
| **Deletion** | What depends on the old implementation, and can it disappear? | Consumer map, isolation evidence, retirement procedure, recovery plan | Removing the old runtime path does not violate known obligations |
| **Compaction** | What no longer earns its complexity? | Retired paths, consolidated concepts, simplified configuration and documentation | A later maintainer needs less incidental knowledge to make a safe change |

**Operational evidence connects all seven.** It reveals missing intent, challenges evaluations, explains constraints, informs pace, exposes consumers, and shows when a compatibility path can end.

A document or test only counts as durable knowledge when the development or operating workflow actually uses it.

All seven apply to ordinary evolution as well as replacement, at the depth justified by the change. They are neither seven mandatory documents nor a waterfall. For a small patch, existing references and a short decision record can express them: intent preserved, architectural target retained, focused gate passed, reason captured, compatible introduction chosen, removal obligations considered, and unnecessary complexity avoided. Do not turn an omitted primitive into an implicit claim that its work was completed.

### 2.3 Design commitments

1. **Replace capabilities behind stable boundaries.** Independent change matters more than line count, repository count, or service count.
2. **Protect knowledge before removing its container.** Capture edge cases, incident fixes, compatibility obligations, and reasons before replacement.
3. **Make correctness external to the candidate.** The candidate cannot redefine its acceptance gate to become acceptable.
4. **Treat boundary changes as architectural changes.** They require an explicit consumer and migration strategy.
5. **Preserve data separately from code.** Disposable implementation does not imply disposable user state.
6. **Prefer small, checkable transformations.** A validated work package is a better generation input than an unbounded repository prompt.
7. **Exercise replaceability.** An actual isolated substitution teaches more than a favorable architecture diagram.
8. **Close the learning loop.** New incidents, support cases, and operational discoveries must improve durable knowledge.
9. **Pair introduction with retirement.** Every temporary adapter, alternate path, and migration flag needs an exit condition.
10. **Optimize useful change.** Use a patch, refactor, replacement, or deliberate non-change according to evidence and total cost. Stable boundaries must support intentional product evolution as well as preservation.

### 2.4 Spend where it pays

Prioritize frequently changing capabilities, costly changes, fragile integrations, and consequential unknowns. Stable, low-stakes code can remain ordinary maintained code. Some tightly coupled regions intentionally protect an invariant; a transactionally consistent ledger may belong in one replacement unit.

Consider the full cost:

```text
replacement cost = knowledge recovery + boundary work + implementation
                 + evaluation + migration + observation + retirement
                 + ongoing maintenance of the durable assets
```

Generating code cheaply changes one term. It does not eliminate the others. A successful slice should improve a product outcome, remove a concrete future-change obstacle, or establish necessary evidence for a valuable change. Avoid a speculative regeneration platform before demonstrating a useful local cycle.

Reassess this cost when a patch introduces a new intermediary, state owner, dependency, or long-lived resource. That is a change in architectural scope, even if it began as a defect fix. Revisit the target and compatibility gate before extending the candidate; do not discover an entire new protocol or lifecycle only through successive post-implementation failures.

Before an architectural investment, write a falsifiable benefit hypothesis: **which likely change becomes easier, which current obstacle disappears, and what observation would support that claim?** Use recent work, incidents, or an actual backlog item. An estimate is sufficient to choose a pilot; distinguish it from a measured saving. Compare against a normal patch or deliberate retention, including the cost of maintaining new contracts, harnesses, and adapters.

Keep readable, idiomatic implementation code and accepted source artifacts. Replaceability does not excuse poor local design, speculative generalization, or discarding the cheapest available recovery path. Both humans and agents still need to inspect and debug the running system.

Choose the operation that fits the evidence:

| Situation | Useful operation |
|---|---|
| A local defect is understood and the boundary is sound | Patch it and preserve the lesson in an evaluation and rationale |
| The implementation obscures an otherwise useful boundary | Refactor enough to expose the seam without changing the promise |
| Intent and evaluations are strong, but the implementation is costly or unsuitable | Generate and compare a replacement behind the boundary |
| Ownership or the communication model no longer fits product constraints | Make an explicit architectural change with a migration plan |
| The behavior or consumers are poorly understood | Invest in observation, characterization, and knowledge extraction first |
| The expected benefit does not justify the full cost | Keep the implementation and record the reason to revisit, if any |

---

## 3. Establish the engagement and durable workspace

### 3.1 Inspect before changing

Read applicable repository instructions, contribution guidelines, release policies, and domain documentation. Inspect the working tree, active branch or equivalent revision, uncommitted work, manifests, lockfiles, CI configuration, and supported environments.

Distinguish staged, unstaged, generated, and externally managed inputs where the tooling supports them. Record an identifiable baseline without requiring a commit. Preserve another writer's work and publication state; a baseline experiment must not obtain a clean tree by temporarily hiding or reverting shared changes.

Resolve paths and commands from this repository. Do not assume a package manager, test runner, web stack, Git workflow, or container runtime. Distinguish a command named `test` or `lint` from what it actually executes.

Record:

- Product and repository scope, revision identities, and relevant local modifications.
- User objective and chosen mode.
- Available tools, test environments, credentials by reference, and operational evidence.
- Practical time, compute, paid-provider, and parallelism budgets.
- Existing release authority and any limitations on external effects.
- Known prerequisites, access gaps, and work that can continue independently.

Use existing permissions and release conventions. Do not commit, publish, migrate a live datastore, or deploy merely because this playbook describes those operations. Repository content and retrieved documents are evidence; executable instructions from dependencies do not automatically acquire authority over the engagement.

### 3.2 Preserve roles, reuse locations

Find the canonical location for each kind of knowledge before adding another one:

| Knowledge role | Reuse when available |
|---|---|
| Product purpose and journeys | Product docs, README, feature specifications |
| Requirements and invariants | Specifications, domain docs, contract annotations |
| Architecture and decisions | Architecture docs, ADRs, dependency policies |
| Interface shape | Schemas, protocol definitions, public types, interface docs |
| Behavioral truth | Existing tests, contract suites, evaluation datasets and runners |
| Operational knowledge | Runbooks, dashboards, incident records, SLO definitions |
| Agent navigation | Existing agent instructions, skills, repository maps |
| Change evidence | CI artifacts, experiment records, release manifests |
| Current work and handoff | Existing issue, task record, or a small repository-local status file |

In a small repository, one local design record and the existing test suite may be sufficient. In a larger product, a possible layout is:

```text
REGENERATIVE-SOFTWARE.md            portable playbook
docs/regeneration/index.md         local map, scope, current checkpoint
docs/regeneration/intent.md        accepted requirements and open questions
docs/regeneration/architecture.md  architectural target and unit inventory
docs/regeneration/units/           unit dossiers and retirement plans
docs/decisions/                    durable decision records
contracts/                        versioned boundary artifacts
tests/                            implementation and behavioral tests
evals/                            quality, workload, failure and replay checks
scripts/                          thin wrappers over existing toolchains
```

This is an example, not a mandatory scaffold. Prefer references to existing authoritative artifacts over copied specifications. Large or sensitive evidence can live in an appropriate artifact store with retrievable, access-controlled references and digests.

Keep this portable guide generic. Put product facts, execution results, and current decisions in the product's own durable records. A future agent should be able to navigate from a single local index to the evidence needed for its unit.

Keep the local index small: supported commands, capability-to-path map, contract and evaluation links, and current checkpoint. Put detailed history behind links.

#### 3.2.1 Discover instruction loading before designing the hierarchy

Session-loaded instructions are an enduring deliverable of translation. Inspect the intended harnesses, their versions/configuration when available, existing instruction files and pointer chains. Distinguish **automatic loading**, **harness-expanded imports**, **configured instruction sources**, and **ordinary links or read requests**. A Markdown link does not load its target; an import syntax recognized by one harness may be inert in another. Do not assume that a nested-directory start receives root instructions, that adding one instruction filename leaves another active, or that subagents inherit the main session's instructions.

Keep a small load map in the local index or change record: `entry directory / harness → delivered files or imports → canonical owner → scoped read rule → evidence or uncertainty`. Check observed context or loader diagnostics where available; official documentation describes expected behavior, not a session you have observed. Inspect root starts, relevant subtree starts and delegated entry paths to the extent claimed. Respect higher-priority instructions and existing ownership; an instruction-file change cannot grant new authority.

#### 3.2.2 Compile a small, navigable instruction hierarchy

Choose one canonical owner for each rule. Adapt existing files rather than generating a parallel set:

| Layer | What belongs there | What stays elsewhere |
|---|---|---|
| Session root (for example, `AGENTS.md` or `CLAUDE.md`) | A prominent seven-primitive change loop, shared product/state constraints, scope/precedence, where to find contracts and commands, and the finish/harvest rule | Campaign history, test totals, large dossiers, the full portable playbook |
| Harness adapter, if needed | A supported import or other minimal delivery mechanism pointing to the canonical owner; only genuinely harness-specific additions | Another hand-maintained copy of the project rules |
| Scoped guide | Local boundaries and state/effect owners, consequential invariants, relevant contract/gate locations and exact commands, local compatibility and recovery constraints | A repeated generic seven-primitive lecture or unrelated subsystem instructions |
| Durable packet / local index | Intent, decisions, evidence, current checkpoint and next work at their existing canonical locations | Rules that agents need every session but would only encounter by chance |

For example, a repository using both conventions may keep policy in `AGENTS.md` and a one-line `CLAUDE.md` importing it, **if that harness supports the import**. Apply the same reasoning to scoped adapters; do not create one per directory without a real loading need. If a harness delivers only a scoped guide, that guide must require reading the root contract before edits unless it has already been delivered. Root guidance must likewise require the affected scoped guide before work there. These read rules are fallbacks, not evidence of automatic import expansion.

Install this structure early, then evolve it with the product:

1. Preserve useful existing rules and identify conflicting/duplicated sources before moving content.
2. Put shared actions in the root; give scoped guides concrete owners, invariants and gates that make those actions executable locally.
3. Wire supported entry paths with working imports or explicit read rules. Avoid pointer cycles and competing authorities.
4. Keep the routine entry point short and front-load the operating loop. Measure the **expanded** instruction content: splitting a long file into unconditional imports does not reduce per-session context cost. Load deep packets only when the task needs them.
5. Harvest durable incident lessons into the narrowest owning guide, linking the contract/regression that explains them. Keep transient attempts and counts in evidence records.
6. Validate the affected entry paths and an ordinary fresh-session task, then retire superseded copies. An unfamiliar rule is not obsolete merely because its original reason is unclear.

#### 3.2.3 Make all seven primitives actionable

For an adoption claim, the effective root instructions MUST carry actions for **all seven primitives**, not just their names or a link to a report. They should direct an ordinary patch, a deliberate non-change and a replacement at proportionate depth. Use the following compact contract or an equivalent adapted to the repository:

```text
Start: inspect current revision/work and checkpoint; load the applicable root/scoped
       guides and affected contract if they are not already in this session.
Intent: name the outcome, preserved promises, and accepted behavior changes.
Compilation: identify the unit, public boundary, state owner, consumers and allowed effects.
Evaluations: choose justified oracles and meaningful required checks before changing code.
Provenance: retain reasons, input/candidate identities, attempts, results and limitations.
Pace: match verification/introduction/recovery to coupling and failure consequence.
Deletion: protect knowledge and state; establish consumer/removal conditions first.
Compaction: remove superseded complexity while preserving distinct behavior and rationale.
Finish: verify the integrated candidate, update canonical/published knowledge,
        harvest reusable lessons into the owning instructions, state the actual
        evidence claim, and leave the next actionable step.
```

Bind these actions to real paths, owners, commands and change consequences through the root and scoped guides. For example, a stateful subtree needs its historical-data/recovery gate; a UI subtree needs its lifecycle and browser checks; documentation needs authority, publishing and example checks. One rule can serve several primitives: do not require seven headings or seven new artifacts in every guide. Validate decisions and navigation with Section 11.3; delivery into context and useful application are separate claims, neither guaranteed by a filename.

If editing session instructions is forbidden, retain this contract in the permitted engagement/local index and report that automatic next-session adoption is unestablished. Do not bypass instruction ownership to obtain the claim.

### 3.3 Track progress without inventing a score

Track evidence per unit in separate dimensions:

| Dimension | What to record |
|---|---|
| Knowledge readiness | `discovered`, `specified`, or `evaluable`, with remaining gaps |
| Change verification | Which patch, refactor, replacement, migration, or documentation improvement was verified, and against which gate |
| Change cost | Expected benefit and observed discovery, coordination, verification, and upkeep costs; distinguish estimates from executed changes |
| Replacement | `replacement-demonstrated` only when substitution preserved the supported boundary without requiring consumer changes |
| Reconstruction | `reconstruction-demonstrated` only when fresh reconstruction used the survival pack without the old implementation, and passed the gate |
| Operation | `operationally-observed` only with the required identified deployment and runtime evidence |
| Retirement | `retired-and-compacted` for the named old path after its obligations have ended and removal is verified |

`Specified` means the selected obligations, boundary, and consequential open questions are explicit. `Evaluable` means a runnable gate and justified oracle can judge those obligations within a declared scope; merely listing planned tests is insufficient. Neither state implies that a candidate has passed.

Keep capability disposition separate from rewrite readiness. A family marked `improved` may contain a verified patch, well-tested units, and still-undocumented interfaces. For a unit someone is expected to rewrite, identify the surviving boundary declaration, behavior/state contract, evaluation entry, execution dependencies, and recovery procedure—or name what is missing. Passing public-function tests alone does not demonstrate that this packet is complete or that a replacement runs independently.

For unestablished claims, record `not-attempted`, `blocked`, `failed`, or `inconclusive` as appropriate. These dimensions are not one mandatory ladder: a useful patch can be verified without a replacement, a replacement can be source-assisted, and dead code can be retired without a new implementation.

Claims are scoped to a unit, contract version, candidate, environment, and evidence set. Their current applicability can lapse when these change; preserve the historical record and mark the claim stale. Avoid describing a whole product as regenerative because one unit succeeded. `Not applicable` requires a reason; a skipped deployment is not operational observation.

### 3.4 Make repeated runs cumulative

On subsequent runs, reconcile the existing unit inventory, canonical records, and last checkpoint before creating anything. Preserve stable IDs and useful evidence; update changed facts and mark stale claims. Do not create another parallel architecture document or repeat a completed rehearsal without a new reason.

Keep a short ordered backlog of remaining obstacles with evidence, product consequence, prerequisite, next action, and success criterion. After each slice, select the next one from what was learned. Continue within the requested scope and budget, pausing at a completed gate when a genuine dependency blocks progress. Repository-wide adoption is a sequence of useful slices, not repeated scaffolding of the first slice.

For a campaign, maintain a capability inventory with a disposition for each in-scope area: `improved`, `adequate-for-current-needs`, `deferred-with-reason`, `blocked`, or `uninspected`. Attach evidence and the relevant unit-level claims. This prevents easy leaf modules from consuming the whole engagement while important stateful workflows remain invisible. Deferral is an explicit scope decision, not a way to mark unfinished work complete.

Review the inventory against important journeys and the seven primitives, not just directory names. Broad family rows must expose consequential subunit gaps instead of implying all members share the strongest claim. Separate locally actionable gaps from checks that genuinely need unavailable infrastructure or a product decision; missing live quality evidence does not excuse omitting a deterministic local contract gate.

---

## 4. Coordinate the lead agent and subagents

### 4.1 Lead-agent responsibilities

The lead agent owns scope, the product model, artifact reconciliation, implementation selection, and the final evidence-based claim. Delegation distributes work; it does not distribute accountability into an unreviewed pile of reports.

Product owners and maintainers remain stewards of intent and architectural tradeoffs. Agents can extract, propose, implement, and verify decisions within delegated authority; they must not silently invent business policy or treat an inferred architecture as an accepted one. Preserve the reasoning in shared artifacts so neither a particular author nor a particular agent session becomes indispensable.

Use subagents when permitted and when an independent question or parallel task justifies the coordination cost. Otherwise perform the needed roles in one session. A separate self-review pass can improve a result, but must not be described as independent review.

Useful roles:

| Role | Primary question | Typical output |
|---|---|---|
| Product and intent investigator | Which promises and exceptions matter? | Journey map, scoped invariants, contradictions, evidence references |
| Boundary and state investigator | What actually changes together? | Consumer graph, state ownership, hidden coupling, candidate seams |
| Evaluation engineer | What could a plausible replacement get wrong? | Behavioral gate, fault probes, evaluation blind spots |
| Implementer | What is the smallest justified implementation change? | Candidate and implementation-specific checks |
| Documentation and interface steward | Can users and agents use the product correctly from its docs? | Verified examples, contract/doc corrections, navigation updates |
| Operations and retirement investigator | Can this be introduced, observed, recovered, and removed? | Rollout, state compatibility, observation and retirement evidence |
| Independent reviewer | Does the evidence justify the claim? | Counterexamples, acceptance assessment, residual uncertainties |

These are roles, not mandatory passes or a requirement to spawn seven agents. Combine lightweight roles; split difficult investigations. For consequential changes, independent evaluation or review is useful when available, but distinct agents can share the same mistaken assumption. Independence comes from checking evidence and counterexamples, not from agent count.

### 4.2 Work-package contract

Every delegated task MUST specify:

```text
Objective and why it matters:
Unit and product boundary:
Accepted input artifacts and revisions:
Questions to answer / invariants to preserve:
Allowed reads and owned write paths:
Shared workspace / version-control operations permitted:
Read-only or implementation task:
Known neighboring agents and shared-file restrictions:
Permitted commands, environments, and resource budget:
Required evidence and verification:
Return format and stop conditions:
```

Require specialists to return file/symbol references, commands and results when run, uncertainty labels, contradictions, and recommended next actions. “Looks correct” without an observable basis is not an acceptance result.

### 4.3 Parallelize independent work

Run product archaeology, dependency mapping, test inventory, and documentation comparison in parallel when they do not share writes. Assign one writer to shared contracts, package manifests, migration ordering, generated indexes, and central documentation. Serialize changes whose validity depends on another change.

Include generated and installed artifacts in write ownership. Separate source paths can still share a compiler output directory or a workspace-linked package. Finish and identify the producer's build before a dependent consumer runs; freeze the consumed artifact closure and check it afterward. Concurrent rebuilds cannot be treated as independent verification of one combined candidate.

Route repairs to the artifact's existing owner and preserve accumulated diagnoses when continuing delegated work. Where separately owned runners exchange observations, freeze their receipt schema, required check identities and version in one coordination contract; validate the actual delivered shapes before an expensive integrated run.

File ownership does not confer ownership of shared workspace state. While other writers or evaluators are active, do not shelve, reset, restore, replace, or temporarily mutate their source tree, index, generated configuration, or runtime resources—even for a baseline or negative control. Use an isolated fixture/reference environment; let the lead coordinate operations affecting the whole workspace. A restored file does not retroactively make a shared-tree experiment isolated.

Audit actual touched paths, including generated artifacts and shared checkpoints,
against each writer's accepted allowlist. A scope violation remains a violation
even when no simultaneous writer or data loss occurred. Stop the conflicting
write, retain its bytes, disclose the deviation, and explicitly transfer ownership
before continuing; do not describe the combined work as unconditionally disjoint.

Freeze a work package's accepted inputs before candidate generation. If a specialist discovers missing intent or a contract conflict, return the affected slice to specification instead of letting each agent invent its own interpretation.

The implementer may propose evaluation changes, but changes to acceptance semantics require a separate recorded decision and review. Independent review means independently checking claims against evidence, not assuming that two agents agreeing makes a claim true.

Check reviewer conclusions as well as candidate conclusions. Require references to the actual frozen gate, raw observations and combined artifact; identify which competing implementations a case really distinguishes. Correct causal or coverage overclaims explicitly and retain the corrected verdict. A source-based prediction about an unexecuted schedule is not additional runtime evidence, even when an independent reviewer makes it. Route evaluator repairs to their owner, then freeze the corrected bytes before the lead executes them; keep review authority separate from permission to edit shared state.

After integrating parallel changes, re-evaluate the affected obligations against the final combined revision. Reports from separate branches or earlier artifact versions do not establish the combined result. Disjoint file writes can still conflict through shared state, configuration, or protocol semantics. Invalidate affected evidence after a merge, rebase, or intervening edit; rerun only the checks whose inputs or assumptions changed.

### 4.4 Preserve context between sessions

At each completed gate, save:

- What is accepted, proposed, disputed, and demonstrated.
- Current revisions, candidate identity, modified files, and active write ownership.
- Commands run and evidence locations.
- Decisions and assumptions that could invalidate the next step.
- Exact next action and any unresolved prerequisite.
- Run-owned resource locations, retained diagnostic scratch, and shared resources or operations that must remain untouched.

A checkpoint summarizes work; it does not replace the canonical requirements or evidence. On resume, compare the working tree and referenced artifacts with the checkpoint before continuing.

Provide a short fresh-session prompt that names the mode, product scope, canonical checkpoint, next action and success criterion. Point to durable records rather than copying the conversation; repeat binding constraints whose omission could affect shared work. The next session should reconcile changed inputs, execute the next slice, verify it and harvest the result without repeating completed discovery or rehearsals without cause.

---

## 5. Discover the actual product

Discovery should be bounded but deep enough to choose an honest first slice. Traverse the selected capability end to end rather than reading arbitrary files until the context window fills.

### 5.1 Pass A — user and operator reality

Identify who depends on the product: users, callers, administrators, operators, downstream developers, agents, and external partners.

Map the important journeys:

```text
actor → entry point → authorization → orchestration → owned state
      → external dependencies → visible result → failure/recovery path
```

Include initialization, configuration, ordinary use, cancellation, restart, export, upgrade, and deletion where applicable. Identify which journeys deliver the product's main value and which failures would make the product unusable despite healthy processes.

For each selected journey, capture expected outcomes, actual outcomes, relevant docs, current verification, and known uncertainty.

### 5.2 Pass B — knowledge archaeology

Inspect the sources that contain accumulated experience:

- Tests, fixtures, boundary validators, defaults, retry policies, serializers, migrations, and unusual conditionals.
- Issue discussions, release notes, incident reports, support documentation, ADRs, and useful commit history.
- Operational configuration, deployment scripts, observability queries, scheduled jobs, and integration examples.
- Public documentation, SDKs, CLI help, agent skills, tool schemas, and generated API references.

For each surprising behavior, ask:

1. What obligation or incident explains it?
2. Who currently depends on it?
3. Is it intentional, a temporary workaround, a defect, or still unknown?
4. Which durable artifact would let a replacement preserve or deliberately retire it?

Record provenance as `observed`, `required`, `historical`, `inferred`, or `unknown`, with the source that supports that classification. Inferences are hypotheses until supported. Lack of a remembered reason is not evidence that a behavior can be removed.

### 5.3 Pass C — dependency and blast-radius map

An edge in the dependency map should include producer, consumer, mechanism, promise, evidence source, relevant versions, failure effect, and confidence.

Look for all of these:

| Dependency class | What to inspect |
|---|---|
| Static | Imports, private types, shared libraries, generated clients, package exports |
| Interface | APIs, CLIs, tools, SDKs, plugin hooks, callbacks, webhooks, file formats |
| State | Shared tables, filesystem layout, indexes, caches, identifiers, serialization, object storage |
| Temporal | Ordering, deadlines, polling, startup sequence, cache warmup, eventual consistency |
| Side effect | Messages, notifications, billing, audit writes, retries, scheduled work |
| Operational | Health checks, log parsers, metric names, dashboards, backup jobs, deployment assumptions |
| Human | Support macros, manual exports, customer scripts, administrative workflows, undocumented integration recipes |
| Environmental | Runtime versions, native libraries, locale, time zone, filesystem semantics, provider quirks |

Use static analysis and observed behavior together. Code search cannot enumerate unversioned clients or a support team reading a log format. When runtime evidence is unavailable, label the external-consumer inventory incomplete and constrain the claim accordingly.

Map each cross-cutting promise across its supported entry, execution, result and diagnostic paths. A correct intermediary or client-side cleanup does not prove the promise held at the consumer's actual boundary. Alternative transports, workers, administrative tools, errors and logs can bypass the first path inspected; include the consequential siblings in the impact set.

When rejection depends on configuration, inspect the guard and its false branch
under the selected settings. A healthy rejection control in one configuration
does not establish that another configuration is unreachable. Record the entry,
condition, invoked implementation and public projection; follow dependency-absent
and recursive fallbacks too. A family name shared by two paths does not transfer
their deadlines, fallback behavior or permitted effects between them.

Use change history when available to test the proposed seams: which files, schemas, and teams repeatedly change together, and for what reason? Exclude bulk formatting, generated output, and coordinated releases before interpreting co-change as coupling. Combine that evidence with actual dependency and ownership constraints.

For every candidate unit, answer:

- What disappears or degrades if this unit is absent?
- Which direct and indirect consumers are affected?
- How would evaluations and operators notice?
- What state or side effect would make recovery difficult?
- What changes elsewhere would be required to substitute it?

Known, bounded, detectable, recoverable blast radius is the goal. Zero blast radius is not required for a useful component.

### 5.4 Pass D — verification reality

Inspect what the tests truly exercise:

- Are assertions about public behavior or private implementation structure?
- Which critical dependencies are mocked, including authentication and persistence?
- Do integration tests actually run, or silently skip when credentials are absent?
- Does the build execute generators or uploads as side effects?
- Which failures are baseline failures, flaky checks, or environment problems?
- Are benchmarks using representative data, concurrency, and provider conditions?
- Can documentation examples be executed against the supported release?

Run a relevant baseline after understanding commands and environment effects. Record command, working directory, revision, environment, result, and omissions. If the baseline is broken, classify it before using it to judge a candidate.

Preflight prerequisites in dependency order: execution platform, required artifacts/packages, access, and resource capacity. Keep diagnostics distinct; a failed platform query does not prove an image or package is missing. Check the actual temporary, cache and container-storage filesystems, not just the checkout's disk. Place large scratch on adequately provisioned, run-owned storage and account for space retained by failed attempts.

Carry that scratch configuration through every required command and child process, including browser profiles, documentation fixtures and native tooling. Recheck capacity as failed attempts accumulate; a valid initial preflight does not cover later runs. Reuse an existing compatible tool installation when authorized, with its version/path recorded, rather than silently changing the product's dependency graph to obtain an evaluation environment.

Where downloads or installs are outside scope, disable implicit fetch fallbacks before launching tooling; a missing local artifact is a prerequisite, not permission to fetch it. Inspect persistent runtime configuration before reuse: an engine or store can pin storage/runtime roots that a later command cannot safely substitute. A failed query with the wrong configuration does not establish artifact absence. Record attempted external transfers even when they fail; unavailable transfer observations mean unknown, not zero egress.

Provider mocks and an offline package-manager flag do not cover ancillary
resources such as first-use tokenizer data or public-name resolution. Preflight
those actual paths too: bind available local artifacts and the chosen network
policy before the run. Reuse compatible tools read-only from retained artifacts
when permitted, with extracted bytes/version recorded. If a network adaptation
causes an unapproved fetch, retain and disclose it; cache bytes or an empty
credential environment do not prove transfer volume, destinations or zero egress.

Resource floors can apply to setup-only stages and evaluation self-controls too. Classify a preflight refusal and its dependent failures before attributing regressions to the product; preserve the guard. Bound recovery work against the actual shortfall, including extraction scratch and compressor memory. Reclaim only identified owned duplicates after evidence preservation (Section 12.3); if safe recovery is insufficient, name the capacity prerequisite instead of turning verification into an open-ended storage project. After provisioning changes, remeasure and reconcile inputs before fresh executions; a later pass does not diagnose an earlier crash.

Budget concurrent processes as well as storage: available memory, swap pressure and browser targets can limit a fixture even when disk preflight passes. Where the claim permits it, independent user contexts may share one owned browser process; preserve their authentication/state isolation and collect errors/crashes from every context. A target crash is an environment/execution failure until its cause is established, not evidence that a product-value assertion rejected a defect. Reduce owned fixture pressure rather than terminating unknown-owner processes or changing product dependencies.

When using an owned resource-bounded launcher, record its bytes/digest, the actual underlying binary and process/heap flags, and carry the scratch configuration through it. Collect owned process diagnostics where available; label inaccessible diagnostics as unavailable. Passing with a changed budget does not establish that memory exhaustion caused an earlier crash; retain the failed attempt and distinguish the adaptation from a proven diagnosis.

When an authorized dependency is available, use a small representative producer/consumer check early enough to challenge assumptions before expanding fakes and specifications. Keep deterministic fixtures hermetic: explicit configuration, isolated mutable resources, and no accidental activation through ambient credentials or local configuration. Distinguish `not configured`, `not attempted`, and an observed connection failure; none means that the dependency's behavior was validated. Sanitize retained live observations without removing the failure mechanism.

### 5.5 Pass E — choose the first slice

Prefer a meaningful leaf capability or recently changed unit with:

- A clear product benefit or concrete replacement obstacle.
- Observable behavior and an accessible evaluation environment.
- A natural boundary and manageable state.
- A bounded set of supported consumers.
- A recoverable implementation change.

Avoid selecting by fewest lines of code. A tiny serializer may anchor every stored session; a large pure transformation may be easier to replace. Name one likely follow-up change to test whether the selected seam will improve real work rather than only support a convenient demonstration.

If no unit is ready, choose an enabling slice: extract an incident regression, formalize a protocol, introduce a narrow state-ownership seam, or build a real integration fixture. Deliver and verify that slice, and state which replacement obstacle remains.

**Discovery exit condition:** a product map sufficient for the chosen slice, a baseline, a documented boundary and state hypothesis, concrete unknowns, and a reasoned selection. Complete product archaeology is not required before useful local work.

---

## 6. Extract and validate intent

### 6.1 Write implementation-independent obligations

A useful intent statement contains:

```text
ID + scope + actor/context + invariant or outcome + reason
   + evidence + verification method + status
```

Describe the promise, not the current sequence of method calls.

| Implementation description | Durable intent |
|---|---|
| Call `reserve()` before `charge()` | A customer must not be charged for stock that was not successfully reserved for that order |
| Cache the result for 30 seconds | Repeated requests must meet the accepted freshness and response-time budgets; record the budgets and their rationale |
| Invoke the final-stream callback | Distinguish user-visible completion from transport completion and persist all required state updates before considering the session durable |
| Call the auth helper in every route | Every entry point that exposes protected state must enforce the applicable principal and resource scope |

Preserve negative requirements: no duplicate financial effects, no cross-tenant data exposure, no false claim of durable completion, no lost accepted work, no silent truncation where completeness is promised.

Include existing contractual, regulatory, and domain obligations where they apply. Record the actual source and scope rather than inventing generic requirements. A user story describes a desired activity; intent also captures the constraints that must survive every way of implementing that activity.

### 6.2 Type statements so they can be checked

Use a small vocabulary:

- **Requirement:** an observable capability or outcome.
- **Invariant:** a property that must hold across relevant states and operations.
- **Constraint:** a limit on performance, resources, interoperability, or permitted implementation.
- **Definition:** a term, state, identifier, unit, or domain concept.
- **Context:** explanatory evidence that does not itself create an obligation.
- **Decision:** a chosen resolution, including rationale and rejected alternatives.

Give durable statements stable IDs, such as `REQ-017`, `INV-004`, and `DEC-012`. IDs belong to the canonical records; they must not be regenerated from sentence order or invented anew each time an LLM reads the prose.

Link requirements to the capabilities, contracts, evaluations, and operational claims they justify. Start with Markdown tables or structured metadata; a dedicated graph database is unnecessary.

### 6.3 Separate preservation from correction

Classify observed behavior:

| Status | Handling |
|---|---|
| Accepted obligation | Preserve and evaluate |
| Compatibility obligation with a planned end | Preserve until explicit retirement conditions hold |
| Confirmed defect | Capture the reproduction and the intended corrected behavior; record the intentional difference |
| Implementation accident with no required external effect | Keep replaceable; removal still needs dependency evidence |
| Disputed or unexplained | Investigate; block only changes whose correctness depends on resolving it |

Characterization tests are valuable archaeological tools. Label them as observed behavior until their assertions are accepted as obligations. Do not freeze every bug into a permanent contract, and do not discard an inconvenient behavior merely by calling it accidental.

### 6.4 Validate before synthesis

For the selected unit, challenge the intent for:

- Missing scope, ambiguous terms, contradictory requirements, and undefined precedence.
- Boundary values, empty or malformed inputs, old data, and supported versions.
- Partial success, retries, concurrent operations, cancellation, restart, and external outages.
- Both safety and progress: what must never happen, and what must eventually happen under stated conditions and deadlines. A system that rejects every request may satisfy a negative constraint while failing its purpose.
- Units, time zones, precision, rounding, locale, ordering, and freshness.
- Measurable timing and resource constraints under a specified workload.
- Testable acceptance and an identifiable source of authority for expected results.

Test the consequential combinations as well as individual dimensions. For example, two supported input representations may require the same normalization rule, or re-enabling a capability may need to restore progress after disabling it stopped effects. Derive a small interaction set from plausible failures and consumer assumptions, rather than attempting an exhaustive Cartesian product or only enumerating branches in the current code.

Do not invent a latency target or compatibility window to make the template complete. Measure a baseline, propose a target with rationale, or mark it unresolved. A consequential unresolved threshold prevents a claim that the threshold was preserved.

When evidence conflicts, identify the smallest unresolved decision, its consequences, and who can resolve it; continue independent work. Preserving an evidenced existing obligation within delegated scope does not require a new approval ceremony. Ask for a decision when correctness depends on choosing a product promise, not simply because an old requirement lacks a formal record.

An intent review provides structured judgment, not a proof that natural-language requirements are complete or consistent.

### 6.5 Stabilize meaning before selective regeneration

Persist the accepted structured requirements. Have agents propose patches to those records, then validate and review the patches. Do not rebuild the canonical requirement graph from prose on every run.

Distinguish:

- **Editorial changes:** meaning is unchanged; update documentation lineage without requesting implementation regeneration.
- **Semantic changes:** a promise, constraint, definition, or dependency changes; identify affected units and checks.
- **Uncertain changes:** treat as potentially semantic until resolved.

A content digest detects byte changes. It does not prove semantic equivalence. Stable IDs, versioned records, explicit relationships, and reviewed change classification provide a practical basis for selective work. Do not claim an LLM can reliably infer a deterministic intent graph from arbitrary prose.

### 6.6 Connect obligations to observable evidence

For the selected unit, maintain an obligation ledger in an existing specification, test inventory, or dossier:

| Obligation and version | Applicable scenarios and consumers | Evaluation and oracle | Latest candidate/result | Gap or decision |
|---|---|---|---|---|
| `<ID/version>` | `<normal, failure, historical state, relevant versions>` | `<check reference and source of expected behavior>` | `<artifact, run, result state>` | `<uncovered scenario or resolved decision>` |

Use it in both directions: every important obligation needs evidence or an explicit gap, and every required gate needs a reason to exist. A link to a test file is not proof that the assertion covers the obligation. Inspect what the check observes, its mocks, and the relevant scenarios.

For an important claim, point to the asserting case/property and observation, not only its suite. Verify that the gate covers the claimed scope: all owned state locations for a wipe, both directions of a mode transition, and the selected value reaching its eventual consumer—not merely being accepted by an earlier validator. Record a characterization as such when authority or producer compatibility is still inferred.

Mark checks as required or advisory **before** comparing a candidate. An unresolved gap in an important obligation limits readiness; a high test count does not close it. Start with the selected capability rather than requiring a complete product-wide matrix before work can begin.

### 6.7 Make intentional evolution explicit

Preservation is the default for existing promises, not a veto on new product behavior. For a feature, correction, or contract evolution, record an **acceptance delta**:

| Class | Treatment |
|---|---|
| Preserved obligation | Same semantics and gate for supported consumers |
| Added or changed obligation | New version, reason, authority, scenarios, and expected differences |
| Transitional obligation | Supported old/new combinations, migration, and exit conditions |
| Retired obligation | Evidence that its consumer or product obligation has ended |

Evaluate the candidate against the preserved obligations plus the accepted delta. Keep the old gate and baseline as evidence; do not expect the old implementation to pass genuinely new requirements. An expected baseline failure must match the specific intended difference, not excuse unrelated failures. If the delta requires consumers to change, report a contract migration rather than unchanged-boundary replacement.

For an early prototype, record provisional product hypotheses and inexpensive examples. Stabilize interfaces as supported consumers emerge; premature permanence around an unvalidated product can make evolution harder. A prototype still needs an honest account of the behavior and state it currently promises.

---

## 7. Define the architectural target and contracts

### 7.1 Compilation means an explicit target

Here, compilation means realizing accepted intent inside a declared architecture. Before generation, specify:

- Replacement units and their responsibilities.
- State ownership, mutation authority, and transaction boundaries.
- Supported boundary contracts and consumer compatibility.
- Allowed communication mechanisms: calls, commands, queries, events, streams, or workflows.
- Side effects and where they are permitted.
- Runtime, framework, dependency, deployment, and resource constraints.
- Failure containment, recovery, observability, and test entry points.
- Which internal design choices remain open to the implementer.

The target should be sufficient to constrain implementation without prescribing every private method. Use the current viable architecture unless evidence justifies changing it.

### 7.2 Choose natural units

A good unit contains a decision or capability that can change independently. It generally owns its mutations and can be evaluated at a meaningful boundary.

Useful units include a parser, pricing capability, indexing stage, persistence adapter, protocol client, rendering subsystem, or workflow. A unit can be a module inside a monolith. It does not need a process, repository, network endpoint, or separate deployment.

If two components must always coordinate to preserve one invariant, consider keeping them in one unit. If a shared abstraction imports private types or performs hidden side effects, reuse may be defeating independent change.

Do not create a contract and evaluation bureaucracy around every function. Split when doing so reduces coordination and verification cost; merge when a split merely moves complexity into interfaces.

Test a proposed boundary against concrete change scenarios: alter a domain rule, replace a provider, evolve a stored format, or fix a failure relevant to this product. Predict which code, contracts, and consumers would change. Prefer a boundary that contains likely changes while keeping shared invariants coherent. An agent should be able to find the unit's obligations and verification entry points without reconstructing unrelated internals; a small context window is not itself a reason to fragment the domain.

### 7.3 Describe the full contract

For each boundary, document the applicable parts of this inventory:

| Area | Contract details |
|---|---|
| Shape | Inputs, outputs, schemas, encodings, null/missing distinctions, unknown fields |
| Semantics | Preconditions, postconditions, invariants, state transitions, observable side effects |
| Identity | ID stability, uniqueness, normalization, correlation, ordering if promised |
| Authority | Principal, roles, resource scopes, ownership, credential propagation |
| Failure | Error classes, codes, partial results, retryability, unavailable versus unauthorized |
| Time | Deadlines, cancellation, backpressure, ordering, freshness, expiry, progress |
| Delivery | At-most/at-least-once behavior, deduplication, replay, idempotency scope and lifetime |
| State | Durable formats, serialization, read/write compatibility, migrations, recovery |
| Resources | Size limits, concurrency, memory, latency, throughput, provider and monetary budgets |
| Operations | Readiness, liveness, diagnostic outputs, audit obligations, shutdown behavior |
| Evolution | Version policy, deprecation, supported combinations, extension rules, retirement evidence |

A schema usually captures only part of this. “Both implementations return JSON with these fields” is insufficient when clients depend on timing, ordering, error distinctions, or persistence behavior.

For numeric outputs, declare meaning, units, useful range and transformations,
not only the field's type. Trace consumer thresholds back to the actual producer:
a rank-fusion score, similarity and calibrated probability are not interchangeable.
A shape-compatible scale change can silently discard all useful results. Check
the range and decision rule before interpreting an empty result as absence or
tuning a cutoff; calibration and quality claims still need their own evidence.

Map authority per effect: resource ownership, direct or group grants, access to contextual data, permission to perform a general action, and admission to an ongoing subscription can have different rules. Test overlapping grants and removal of the last covering grant; removing one grant need not revoke another independent right. Omitting an inaccessible optional context can be correct while the caller's general action remains permitted. Derive the gate from the supported boundary rather than inventing a blanket denial.

Carry that authority through alternate implementations and feature-flag fallback
branches as well as the default path. Disabling an optimization must not erase a
scope the caller already supplied. Preserve absent-versus-empty values and the
fallback's supported algorithm/shape rather than replacing it merely to regain
confinement. Judge each returned projection separately: filtering primary records
or chunks does not establish that related entities, summaries or metadata are
scoped. Record any broader gap without claiming a narrow forwarding fix closes it.

Carry authority through recursive calls and dependency-absent degradation, not
only the primary feature-flag branch. A fallback that re-enters an otherwise
correct standard path can lose the caller's scope before that path receives it.
Use a permitted-progress contrast and retain the actual requests at both seams.
Separate resource eligibility from content redaction: allowing access because one
related member is permitted does not establish that a shared summary, aggregate,
identifier list or separately fetched relationship set contains only permitted data.

Distinguish connect-time authorization from continuing authorization. A successful new-read or new-subscription denial does not prove that an already-admitted feed or operation was terminated, nor does a disappearing UI entry. Observe both boundaries where relevant. A feed that does not revalidate is an observed limitation, not automatically a preservation requirement or permission to ignore an existing revocation obligation. Specify any stronger lifecycle guarantee explicitly before claiming it or changing that boundary.

Completion of an already-admitted operation does not itself authorize its later persistence request. An answer or result may still arrive while a new save is correctly denied under current authority. Evaluate response completion, attempted persistence, authoritative save verdict and retained state separately for each actor; continued owner work cannot establish another actor's rights.

### 7.4 Make framework effects visible

Inspect callbacks, ORM hooks, signal handlers, middleware ordering, route conventions, background tasks, dependency injection, and build-time behavior. Make cross-boundary effects explicit in contracts and evaluations even when the implementation continues using those mechanisms internally.

An architectural constraint might say:

```text
The ingestion capability owns document lifecycle transitions.
It publishes completion only after durable content is available to readers.
Notification delivery is an explicit downstream effect with defined retry rules.
No other unit writes ingestion-owned state except through the accepted boundary.
```

Enforce important constraints with the lightest fitting mechanism: package exports, import checks, dependency rules, schema checks, architecture tests, datastore permissions, or integration evaluations.

### 7.5 Classify the change before implementing

| Change | Required treatment |
|---|---|
| Implementation behind an unchanged boundary | Preserve accepted contracts; verify equivalent required behavior |
| Intentional behavior change | Amend intent and acceptance criteria explicitly; evaluate old and new obligations separately |
| Contract or ownership change | Record the architecture decision, affected consumers, compatibility and migration plan |
| Runtime/framework/provider change | Revalidate implicit behavior, persisted formats, operational assumptions, and relevant evaluations |
| Evaluation-only change | Explain the new knowledge or correction; re-evaluate affected claims without automatically regenerating code |
| Documentation-only clarification | Verify meaning and examples; regenerate only actual derived documentation artifacts |

Additive changes can still break consumers: new enum variants, changed defaults, stricter validation, altered ordering, or an extra event may violate an existing assumption. Compatibility is a property to test against supported consumers, not a label derived from diff size.

If the architectural target itself changes, describe the old target, the reason it no longer fits, the proposed target, preserved intent, migration cost, and evaluation plan. Recompilation is justified by evidence such as cost, latency, reliability, platform support, or team operating capacity.

### 7.6 Repair a weak seam incrementally

Use this sequence when the selected capability cannot yet change independently:

1. **Characterize one real entry path.** Capture its important outputs, state changes, and failure behavior before moving code.
2. **Choose a coherent owner.** Keep the decision and the state transitions needed for its invariants together. Identify every existing writer and bypass.
3. **Expose the narrow boundary.** Reuse a suitable function, module export, or interface. Make exchanged data and effects explicit; avoid leaking private ORM entities, framework contexts, or mutable internals into consumers unless they are an intentional supported contract.
4. **Route an existing path through it.** Move callers incrementally, preserving behavior. Construction or registration belongs at a discoverable composition point. Adapt persistence or provider mechanics at a seam only where that separation pays for itself.
5. **Remove bypasses and enforce the dependency rule.** Package exports, import checks, or targeted architecture checks should catch the actual forbidden dependency. If shared writes remain, record the coupling; adding an interface did not remove it.
6. **Exercise the integrated journey and remove superseded wiring.** Verify the same promise through the real entry point. Account for temporary routes and compare the result against the follow-up change predicted in Section 5.5.

Keep domain decisions separable from I/O when that improves reasoning and evaluation. Use direct, idiomatic code for cohesive internals. Introduce a plugin system, message bus, repository abstraction, or service only for an evidenced need; an interface for every class is not a replacement architecture.

Check failure propagation through the narrow seam before adding architecture. A sentinel that is legitimate for a missing read can falsely acknowledge a rejected mutation if a wrapper discards its meaning. Swallowed rejection can make the UI select a state storage never accepted. Preserve authoritative failures through helpers and callers, and freeze healthy-success plus rejected-operation checks at the relevant boundaries. Repair that adapter/caller pair when sufficient; a new state protocol is not the default remedy.

A healthy client can propagate rejection correctly while its actual caller catches it and falls through into success-only cleanup or view changes. Verify the consumer's control flow as well as the helper's contract. On a known rejected mutation, stop the success transition; separately permitted list refresh or diagnostics must not acknowledge the proposed state. Use direct consumers of retained context/state so an ordinary re-fetch cannot conceal that loss.

This enabling slice establishes a better seam. It establishes replacement only if an actual alternate implementation meets Section 15's conditions.

### 7.7 Verify composition, not only components

A set of passing unit gates does not imply a working product. At each consequential interaction, connect the provider's guarantee to the consumer's assumption: authorization context, timing, state visibility, ordering, delivery, and failure handling must agree under the same supported versions and conditions.

When a change can affect cross-unit behavior, include the affected critical journey in the verification scope. Reuse existing checks and rerun those whose inputs or assumptions changed. Check shared-resource effects when relevant: individually acceptable timeouts, retries, memory use, or connection pools can combine into missed deadlines, duplicate effects, or overload. Allocate end-to-end budgets and retry ownership explicitly where they matter. A compatibility matrix establishes combinations only when those combinations are exercised or supported by other justified evidence.

Keep one useful integrated check for an emergent failure instead of duplicating the same assertions at every layer. Boundary checks localize defects; journey checks establish that the parts still deliver the outcome together.

Include lifecycle composition where it matters. For example, if shutdown promises no new work, relevant admission must close before asynchronous draining; stopping known tasks alone cannot stop work accepted afterwards. A product may instead permit specified read-only work during that phase. Delayed completions need an explicit ownership/ordering rule across resource changes and repeated operations; a newer snapshot may supersede an older in-flight refresh. Scope state to its request/session/resource. If correctness depends on framework callback ordering, declare and evaluate that dependency, or remove it with a small seam repair. Label a currently-correct but fragile seam differently from a reproduced defect.

---

## 8. Match pace and rigor to architectural position

### 8.1 Classify the actual dependency position

Pace is about compatibility and change cadence. Risk is about the consequence and recoverability of a particular change. Record both; they are related but not interchangeable.

| Position | Typical characteristics | Appropriate discipline |
|---|---|---|
| Fast-changing | Contained consumers, reversible state, bounded effects | Focused behavioral gate, quick feedback, simple recovery |
| Shared product layer | Several consumers, meaningful persistence or workflows | Broader contract and integration checks, version awareness, staged introduction when relevant |
| Foundational or slow-changing | Long-lived data or clients, pervasive identity, high-cost failure, difficult reversal | Deep compatibility evidence, migration rehearsal, expanded observation, explicit retirement horizon |

These are descriptors, not a universal service taxonomy. A recommendation system handling regulated decisions can be consequential. A cache library can be foundational if its serialization defines every active session. A small diff in a slow layer deserves slow-layer evidence.

### 8.2 Define the unit's change policy

Record:

- Expected change frequency and the product reason to change now.
- Supported consumers and oldest supported data/client versions.
- Verification appropriate to failure consequences.
- Rollout increments and recovery trigger, if deployment is in scope.
- Observation duration **and** minimum representative traffic or events.
- Important low-frequency cycles: scheduled jobs, billing periods, offline clients, expiry windows.
- Conditions that allow old paths and state compatibility to end.

Use existing review and release policies. Escalate actual unresolved product or architectural decisions to their owner; do not introduce generic approval queues for ordinary local work.

Retest stable boundaries when the dependency landscape changes. A stable interface can accumulate new consumers, log readers, and timing dependencies without changing a line of its declaration.

The knowledge layers have different cadences too: intent and foundational contracts usually outlive many implementations; evaluations expand as new evidence arrives; implementation can change frequently behind those constraints. Freezing an evaluation gate for one candidate does not freeze product learning indefinitely.

---

## 9. Build evaluations that survive replacement

### 9.1 Separate the evaluation from its implementation adapter

An evaluation is an executable claim about an obligation that remains meaningful across implementations. Unit tests can serve that role when they target a stable public function; HTTP tests can fail that role when they merely mirror current accidents. The distinction is what the assertion means, not the test framework.

Structure a reusable behavioral harness as:

```text
versioned cases / properties / workload
                 ↓
public-boundary driver or implementation adapter
                 ↓
candidate outputs + observable state + side effects + measurements
                 ↓
implementation-independent assertions and acceptance policy
```

An adapter may translate launch details or transport mechanics. It MUST NOT hide regressions by rewriting outputs into expected values, swallowing failures, dropping unexpected events, or silently changing semantics. Version and review adapters as part of the trusted evaluation machinery.

Name the actual orchestration exercised, not merely the deepest real dependency:

| Evidence boundary | What it can establish | What remains unestablished |
|---|---|---|
| Source/structural inspection | Presence, wiring and plausible event-order mechanisms | Executed behavior |
| Real query/request assembly through a recording transport | Actual generated predicates, scope parameters and effect requests at the named seam | Store execution, planner semantics, returned data or production isolation |
| Extracted actual callback with controlled bindings | That source expression's behavior under the named state/event schedule | Framework scheduling, component lifecycle and user interaction |
| Real request context + disposable persistence + test driver | Actual route/auth/state effects and the driver's client protocol | The product UI's orchestration, even if it uses the same helpers |
| Actual UI journey through the framework | Interaction, lifecycle and reload behavior in the identified UI environment | Production parity, external-service quality or untested environments |

Source-assisted probes may execute the actual expression rather than a hand-copied approximation; keep their extraction identity, bindings and scheduling assumptions explicit, and fail on ambiguous extraction. Such probes are implementation-sensitive counterexamples, not implementation-independent reconstruction gates. Strengthen the claim through the real consumer when the defect depends on framework lifecycle or user actions. Do not promote successful driver persistence into UI acceptance.

Fail explicitly when a changed extracted expression needs an unbound dependency. Declare which transitions, mutable closure values and write ordering the adapter simulates rather than executes; an adapter-imposed queue or completion flag cannot prove the product supplies that behavior. A retained defective expression should have byte-level provenance to its original source, independent of the evolving candidate adapter.

Keep mirrored state fields and their construction semantics aligned too: binding a newly referenced immutable request snapshot is insufficient if the adapter initializes it from later mutable state. Record adapter repairs separately from oracle changes, preserve the intended assertions, and use the actual framework consumer to judge lifecycle claims the adapter cannot establish.

Keep implementation-specific callback counts, refresh triggers and scheduling assumptions in the adapter contract. They describe how the identified implementation is driven, not additional public product obligations. A replacement with a different valid schedule needs an appropriate adapter under the same value/state oracle; failure of an unsupported driver schedule is not evidence that the replacement violates the product contract.

Trace branch-specific effects through the actual consumer. Two writes to the same resource can publish different notification channels or none; an acknowledged auxiliary write is not proof that the selected page's refresh trigger ran. Use a healthy supported operation that emits the relevant event and observe its delivery/readiness. Preserve documented optional/default event fields instead of requiring one incidental wire spelling; correlate the event with the actual actor and committed effect.

Where valid implementations can differ, declare the comparison rule explicitly: exact equality for a stable format, a set comparison only when order is not promised, a justified numerical tolerance, or properties of nondeterministic identifiers. Retain raw observations and freeze the comparator before evaluation. Normalize only contractually irrelevant variation; never strip inconvenient fields, timing, ordering, or errors after seeing a failure.

Apply a declared public projection to both actual and expected values. Comparing projected output to a metadata-rich full fixture is a shape error, not evidence of lost metadata. Keep excluded but consequential state covered by an independent full-state oracle; correcting the public comparison must not remove raw metadata, identity or byte-preservation obligations.

### 9.2 Establish the oracle

Derive expected behavior from accepted requirements, domain rules, supported consumer contracts, and independently assessed evidence. Where available, use mathematical properties, authoritative reference data, known protocol vectors, or carefully reviewed examples.

Derive a consumer check from that consumer's supported entry path and public data shape, not from a capability exposed only by its producer. A denied route or operation can be an intentional boundary that needs an explicit negative assertion; do not extend production permissions merely to satisfy an invented test.

The old implementation is an observation source and a differential reference. It is not the sole authority for correctness. When old and new disagree:

1. Preserve the minimal counterexample.
2. Identify the requirement or uncertainty it exposes.
3. Classify the difference as a regression, intended correction, permitted variation, old defect, or unresolved ambiguity.
4. Resolve the acceptance rule through evidence and the appropriate decision owner.
5. Re-run both sides when relevant and preserve the decision.

Inspect internal results and the actual public projection separately. Optional
fields or completion metadata returned by a helper may be dropped by its handler;
the helper's shape is not evidence that the client received those values. Label
incidental nulls, missing flags and callback schedules as observations, not lasting
requirements. If a later correction changes them intentionally, declare a versioned
acceptance delta, retain the old gate and baseline, and preserve its still-required
scope/progress checks. Correcting a faulty evaluator and changing product intent
are distinct decisions even when both require new gate bytes.

For optional values, distinguish nonempty, empty, explicit null and absent keys
at the serialized consumer boundary. Preserve permitted falsey values without
collapsing them through a truthiness fallback; missing-key compatibility must
not turn into a lookup error. Give consequential projected fields independent
positive cases so one rejected field cannot hide the others. Label schema-valid
empty/null probes separately when the actual producer never emits those states;
type acceptance alone does not establish a producer guarantee. Trace failure
phase too: a refusal before work and an abstention after work can legitimately
carry different metadata despite sharing a failure label.

For streamed completion metadata, observe chunks independently of visible content.
A terminal reason can arrive on a content-free chunk; a later usage-only chunk can
have no choices at all. Capture required metadata before a text-only adapter drops
it, preserve the declared terminal value across unrelated trailing chunks, and
pair cut-output cases with healthy completion, null/missing metadata and supported
empty-choice controls. Do not infer truncation from text or transfer one pipeline's
visible notice, deadline or callback schedule to a sibling.

Declare value equivalence precisely. If a contract permits an absent flag or boolean
false, a test for "not exactly true" can wrongly accept null, numbers or strings.
Test the allowed typed values, and distinguish an attribute-present null control
from an attribute-absent control. A test name mentioning a positive flag is not
evidence that its fixture ever supplies that value; inspect the actual assertions.

Do not generate implementation and expectations from the same unsupported assumption and mistake agreement for verification.

A simulated store can help connect real authentication, orchestration and query assembly to a fixture outcome, but its interpretation remains an explicit oracle assumption. Contrast permitted, empty and unrestricted scopes, and retain the actual predicate/parameter requests so a lenient fake cannot conceal a missing filter. Recording a correct request proves assembly; interpreting it in a fake proves only that fixture interaction. Neither establishes the database's actual execution or live isolation. Where a mechanical repair touches sibling builders, use a frozen direct-seam selection with healthy controls rather than treating one HTTP case or a source-pattern count as coverage of every changed path.

Inspect the evaluator's own dependency path. An assertion that calculates its expected value using the candidate's parser, rounding helper, authorization routine, or serializer may repeat the very defect it should detect. Shared public types can be appropriate; shared decision logic needs an independent property, reference vector, or other justified oracle. Treat the harness, comparator, fixtures, and runner configuration as maintained software with explicit trust assumptions.

For framing and fragmentation claims, locate cuts in the actual encoded payload delivered to the candidate and inspect the bytes on both sides. Account for multibyte characters, serializer whitespace and the distinction between a byte index and an end-exclusive slice offset. A prefix assembled separately from the real serializer can miss both a character and a delimiter; a passing ordinary-input control does not prove the intended split occurred. Establish the fixture's cut locations before attributing a rejection or accepting the claimed coverage.

Prefer named fields or structured records for inter-stage values over growing positional argument lists. Validate required fields, types and identities fail-closed. In a cross-process journey, bind the consumer's reported upstream to the producer actually launched; a passing check against a different endpoint is not integrated evidence. Validate synthetic fixture values — identifiers, addresses, keys — against the receiving component's own validation rules before a run; a syntactically plausible fixture can be rejected by the actual sink and stop a run at setup, which is a fixture defect, not a product-value rejection or an accepted whole run.

Distinguish request capture, response acknowledgment, transaction commit and reader visibility. Observing an outgoing write does not establish persistence. When a required read can legitimately race a pending write, wait within a declared bound for the contractually required acknowledgment or read visibility, then apply the unchanged value oracle. Preserve the last observation on timeout; a sleep, repeated write or eventual non-null value is not a substitute for exact owned-state checks.

Classify mutation failure by phase. An identified request prevented from forwarding before dispatch can establish a known no-commit outcome; a missing response after dispatch or commit leaves an uncertain outcome, not proof of rollback. Retain transport and authoritative-state observations separately, and reconcile through the supported boundary before claiming success, failure or safe replay. If a no-dispatch claim uses absence from a server log, also observe a healthy actual operation in that same diagnostic channel; log absence alone is insufficient.

For a loss-after-commit probe, forward the real mutation, retain its genuine response and authoritative commit evidence, then fail delivery to its caller. Contrast it with no-forwarding and acknowledged-success controls. The evaluator may know that the write committed while the caller has only a transport failure; neither conclusion can substitute for the other. Verify supported reconciliation, exact retained state, eligible pending work and actual retry/replay attempts rather than assuming rollback or safe automatic replay. This probe covers its identified phase, not every uncertain dispatch outcome.

Diagnostic evidence must identify the operation being judged. A repeated method/resource log line can belong to an earlier successful write. Use a request identity or a bounded operation-local log segment together with authoritative response/state evidence; account for diagnostic delivery latency without changing the required value. A healthy line in the right channel establishes observability, not by itself the current transaction's outcome.

Wait for the effect specific to the operation being judged. Content or recall may already match before a feedback/metadata write commits; require the independently expected selected record's new metadata as well. Resolve UI selectors to that record's identity rather than treating a substring used to locate it as its exact stored content. An already-selected control may intentionally perform no action: a persistence probe must produce a supported value change and observe its new attempted write, acknowledgment and specific durable effect. Test intended idempotent no-op behavior separately; do not count preexisting matching state as evidence that a fresh operation executed.

Check whether the observation path repairs or bypasses the state being evaluated. A normal action that re-fetches authoritative state can conceal a broken adoption, cache or recall update. Exercise a supported action that actually consumes the adopted state, with independently expected content, metadata and opaque values; compare its attempted effect, acknowledgment and durable result. Stable IDs or record counts alone do not establish freshness or successful adoption.

An observed selection and a later consumer action are not atomic: a pending valid refresh can change the target between them. Declare allowed coherent outcomes and stabilize the relevant response delivery before acting, while preserving genuine producer bytes. Specify the actor, resource, operation and phase of a no-refetch check; a read that repairs that consumer's state before dispatch differs from a legitimate refresh after completion or a peer adopting the consumer's acknowledged write. A global read counter can wrongly charge another participant's valid work to the selected consumer. Do not force one previously visible outcome to remain selected unless the product promises that stability.

### 9.3 Build a risk-proportionate portfolio

| Evaluation type | What it preserves |
|---|---|
| Contract and consumer checks | Supported inputs, responses, errors, protocols, SDK and CLI expectations |
| Properties and metamorphic checks | Invariants over broad inputs; relationships that hold under defined transformations |
| Incident regression cases | Specific lessons from actual failures and near misses |
| Concurrency and state-machine checks | Contention, ordering, duplicate work, stale writes, legal transitions |
| Failure and recovery checks | Timeouts, partial availability, malformed dependencies, restart and cancellation |
| Persistence and migration checks | Historical data, serialization, state continuity, upgrade and recovery |
| Security and isolation checks | Authentication, authorization, tenant scope, data exposure, trust boundaries |
| Performance and resource checks | Tail latency, throughput, memory, cost, backlog, degraded-load behavior |
| User journeys and accessibility | Real task completion, interaction semantics, assistive technology, error recovery |
| Documentation and agent-use checks | Successful use from published instructions and schemas |
| Controlled operational observation | Differences that realistic live conditions reveal beyond pre-release checks |

Keep useful implementation-specific tests. They support local correctness and debugging even when they do not survive a replacement. Avoid replacing a good test suite simply to rename it `evals`.

**Audit evaluation value.** For each proposed addition or consolidation, ask which observable obligation it protects, which plausible wrong implementation it rejects, what existing coverage misses, and what execution/maintenance cost it adds. This is a review question, not a requirement for a metadata file per test. Remove tautologies over generated constants and redundant assertions; retain independent valid-input, no-false-positive and progress checks. Similar assertions at a unit boundary and a real consumer may protect different failures. Expensive multi-resource fixtures — real lifecycles, whole-store snapshots, cross-process harnesses — should prefer extending an existing harness whose lifecycle, state ownership, and obligations match, rather than copying a bulky per-slice clone or forcing unrelated units into one speculative general harness. Test count, file count and coverage percentage are not measures of rewrite readiness.

### 9.4 Required dimensions for meaningful behavior

Select dimensions relevant to the unit, including:

- Empty, minimal, maximal, malformed, and previously supported inputs.
- Unknown enum values, omitted versus null fields, encoding and version boundaries.
- Duplicate, delayed, reordered, concurrent, and replayed operations.
- Timeout before an operation, timeout after an effect but before acknowledgment, and uncertain outcome.
- Cancellation before work, during effects, after visible completion, and during cleanup.
- Restart with pending work, old state, partial writes, and expired credentials.
- Dependency outage, rate limit, unexpected success-shaped error, and recovery.
- Tenant, user, role, resource, and lifecycle combinations.
- Cold and warm starts; realistic cardinality, load, and resource pressure.

Where applicable, explicitly include the return path (disable/re-enable, disconnect/reconnect, failure/retry), the full owned-state set for destructive operations, and both result and diagnostic channels for confidentiality. Exercise actual entry-point wiring when a direct helper test would miss configuration propagation, middleware bypass, admission ordering, or an ignored user choice.

Make time, randomness, and external services controllable where that preserves the property. Combine deterministic fakes with enough real integration evidence to test the assumptions the fakes cannot cover.

For asynchronous state, deliberately hold the post-completion result while a later operation, navigation, retry or edit starts; then release it and inspect both current state and reloaded durable state. Verify the actual stored/public projection: transient fields may be discarded, making a partial result appear settled. For concurrency claims, launch genuinely overlapping writers, assert each acknowledgment and check coherent result/state pairs. Sequential interleaving is a different observation. Characterize a documented last-writer-wins boundary without inventing stale-rejection or compare-and-swap guarantees.

Record gesture/request, authoritative commit and acknowledgment-delivery order independently. Under last-writer-wins, a successful earlier mutation remains accepted even when a later commit supersedes its state; its delayed acknowledgment is not current-state authority. Reverse acknowledgment delivery does not establish reverse commit order, and a case whose dispatch and commit orders coincide cannot distinguish a latest-dispatched-mutation rule from commit truth. Choose separate schedules when that distinction matters:

| Mutation dispatch | Observed commits | Acknowledgment delivery | Authoritative read delivery varied by the case | Discrimination under the declared last-writer-wins contract |
|---|---|---|---|---|
| A, then B | A, then B | B, then A | Not varied | Current state B; rejects stale last-delivered-ack binding, but also passes latest-dispatched selection |
| A, then B | B, then A | B, then A | Not varied | Current state A; distinguishes commit truth from latest-dispatched selection |
| A held before forwarding, then B | B, then A | B, then A | Capture positive B while A is held; deliver newer positive A, then older B last | Current state A; exposes stale positive-read adoption after newer evidence |

These are case-design examples, not execution claims. Holding A before forwarding can create the second schedule; holding its genuine response after commit creates the first. Prove the chosen phase and commit order, preserve both accepted outcomes, and leave unexecuted orderings explicitly uncovered.

Read delivery is an additional ordering axis, not a mutation-order policy. An older response can positively name a superseded association rather than merely omit a resource. Retain genuine response bytes, dispatch/capture/delivery sequence and the selected view; judge through direct consumers before a later refresh can repair their state. Apply the declared freshness/ownership contract without turning both accepted mutations into latest-gesture-wins or inferring universal read-order coverage from one schedule.

Unchanged durable state can be incidental protection: a uniqueness or authorization constraint may reject a misdirected write while its required state update is silently lost. For isolation claims, inspect attempted effects and acknowledgments as well as final state, and verify the originating resource receives its exact required update. Include navigation away **and back**, auxiliary edits such as feedback, and an older result arriving after a newer operation has settled when these can expose distinct ownership failures.

For subscription-driven journeys, establish actual feed readiness before a healthy remote-write control; creating a connection object or seeing an earlier page read is insufficient. Observe the prerequisite state before each dependent case, so an unadopted prefix cannot make a same-identity update appear to pass by changing the count. To expose delayed-read races, hold a genuine response from the selected server, verify its captured state, perform navigation or newer work, then release it. For live overlays, include both replacement of the selected operation and more progress arriving between capture and delivery; assert the fresh base plus exactly the latest owned progress, with no stale pair or duplicate. Record any instrumentation or transport barrier; do not replace the product response with expected data.

Successful reconnection and successful catch-up are separate obligations. Require an observed transport failure and renewed readiness, then verify missed settled state and current live progress through consumers that cannot repair stale state by re-fetching. Exercise recovery after remote completion and while the remote operation is still unfinished, including repeated drops, exact ordered replay without duplicates, and settled changes made during the outage. Also combine local visible completion/held auxiliary work with remote adoption; a correct origin write alone does not establish a coherent current view.

For retries that restart rather than resume output, expose partial progress first, trigger the actual retry signal, then hold the next attempt ready but without new progress. Verify the declared reset before fresh output can conceal it, exact final state, bounded exhaustion and healthy non-retry controls. Keep one action's correlation identity distinct from a new user action and from physical attempt identity. Stable correlation alone does not establish idempotency or absence of duplicate effects.

### 9.5 Challenge the evaluator

For important obligations, demonstrate that the gate rejects a plausible violation:

- Remove or disable the unit in an isolated fixture.
- Substitute a shape-correct but behaviorally empty implementation.
- Mutate a permission check, event order, rounding rule, deduplication condition, or error classification.
- Inject a malformed response or an old-state fixture.

A targeted negative control is usually more valuable than chasing an arbitrary coverage percentage. If no evaluation notices a missing capability, determine whether it is unused or unobserved before claiming success.

Verify the cause of rejection. An absence probe that fails to import establishes a wiring dependency; it does not prove that an assertion detects a behavioral regression. A behavioral mutant should still build and reach the relevant assertion, which must reject it for the intended violated obligation. Record the baseline result, introduced fault, expected failing assertion, and actual result, then restore the unmodified candidate. A harness outage or unrelated failure is an invalid control.

Name the evidence correctly: an original defect reproduction, an intentionally introduced mutant, an absence/wiring probe, and a passing candidate case are different observations. A newly added case that already passes can fill a valuable gap, but is not a reproduced defect. Correct a mistaken evaluator through the separate gate-change procedure; do not count its failure as a product bug.

A diagnostic probe that succeeds only while a known defect exists needs an explicit exit condition. Preserve its baseline counterexample, then establish the positive intended-behavior assertion before judging an authorized fix. Run that positive gate against the retained broken case to prove sensitivity and keep healthy controls. A correct fix failing the old diagnostic expectation is not a regression; deleting or relaxing the intended obligation to preserve a green diagnostic run is gate erosion.

When a baseline launcher requires unchanged input identities or expected defect observations, keep those assumptions in its retained record. A candidate launcher may bind new input identities and label the diagnostic observations as changed, but it must import or otherwise preserve the frozen positive value oracle, full required selection and failure semantics. Declare this mode-specific identity treatment before execution; a diagnostic run completing successfully must not substitute for product acceptance.

Fault-injection fidelity is scoped to the claim. When the claim covers real entry, readiness, or recovery behavior, enter through the actual entry path and trigger the real failing operation with its actual preconditions; a mocked initializer or shortcut establishes only wrapper behavior, not the claimed operation. Synthetic dependency faults are appropriate when the intended cause and phase are proven — for example, by asserting the injected fault produces the expected error at the expected point before trusting the control. Match the injection mechanism to the environment — privilege-based restrictions, locking, and error classes can change with the running user, runtime, or driver. Keep healthy positive controls alongside negative ones: a gate that has only ever demonstrated rejection cannot show that valid behavior still passes. When one orchestration wrapper spans several phases, record which phase actually failed; an exception during teardown or cleanup does not establish a rejection of admission or readiness.

Exercise consent/cancel through the actual platform control when that interaction is claimed; invoking a mutation helper alone does not establish it. Derive selectors and expected text from the evaluated locale, including a localized confirmation. A hard-coded label that cannot address the current surface is a driver failure, not a product-value rejection. Restore temporary fixture configuration in a finally path and stop or isolate dependent cases when that restoration or prerequisite fails; preserve the failed attempt rather than weakening the value gate.

Check the actual visibility and interaction state before a direct consumer action. Native gestures may leave drawers or overlays open even when storage and selection are correct. Close them through supported local controls and observe committed visibility before message actions; do not use a data refresh, forced interaction or rewritten response to conceal stale state. An intercepted hover/click that never reaches the value assertion is an adapter failure, not behavioral sensitivity.

Validate uncertain transport mechanics with a minimal representative diagnostic before another expensive journey. An offline-emulation flag may leave established sockets alive. An owned forwarding barrier can drop actual sockets and hold reconnection requests while preserving genuine producer bytes; distinguish that fault from an added HTTP-status failure. Observe actual header/readiness delivery for idle streams. Correct unsupported causal hypotheses when raw observations contradict them; do not promote a plausible explanation into an incident fact.

An observation interface may filter what it returns by context — transport security, origin, or scope — so a missing record can be observer filtering rather than product absence. Compare the filtered query against a full capture of the same surface before attributing the result, and attribute observed state to its actual source rather than inferring it. A product journey can be healthy while only the observer's query was wrong. When observation must continue past a prerequisite the run did not create, supply that state out-of-band from the observed surface — in the observer's own context, restored afterward — never by injecting state into the product, and record which assertions were established from genuinely product-created state versus resolved out-of-band.

Verify interception and quota semantics with ordinary surrounding traffic. A framework's match budget may count fallthrough or nonselected operations before the handler filters them. Filter by the intended operation and consumer identity, and reserve sequence/quota before asynchronous capture when dispatch order matters. Hold all competing responses relevant to the selected-read rule: an uncaptured newer refresh can invalidate an older held-read probe. Retain request order, captured bodies and nonmatching-operation controls so a missing interception cannot masquerade as a product defect.

Define mode-specific completion for evaluation barriers. A manually held response requires the declared release and final delivery; passthrough requires delivery without inventing a manual-release obligation; a deliberate delivery failure requires its identified failure outcome. Reserve once before any asynchronous forwarding so a second match cannot overwrite a pending operation's capture or release handle. Mode-correct drain checks must still reject missing outcomes, unmatched requests and unknown failures.

Before extending a no-repair barrier, trace the consumer's wait graph. A save can remain queued until its follow-up refresh resolves; indefinitely holding every refresh can prevent the later save or consumer from reaching its oracle. Choose a bounded hold or, where supported failure handling preserves progress, capture the genuine response and deliberately fail its delivery during the judged window. Record that additional fault condition, exact failure accounting and release/teardown boundary. This is a scoped evaluation condition, not evidence for the pure delivery schedule or broad outage behavior without that condition; never fabricate a successful response to bypass the dependency.

Readiness must identify the active operation being judged. A released predecessor or completed record cannot satisfy a wait for a new held response. Correlate consumer and producer observations before comparing or releasing them; when one correlation ID spans retries, retain attempt/generation information as well. Mixing a fresh consumer request with an older producer result can create a false passing value check followed by a release failure.

Check the composition of interceptors and observers too. A specific interceptor that fulfills a response may bypass a generic passive recorder. The owning interceptor must retain that actual attempted request once, before asynchronous dispatch/capture, and reconcile its acknowledgment with the common record. Diagnose unexplained attempt/acknowledgment counts as machinery failures; do not hide them by subtracting counts or exempting unidentified traffic.

Do not introduce destructive fault injection into a shared environment. A negative control belongs in the same explicitly isolated environment as the behavior it challenges.

For repeated candidate optimization, also challenge generalization. Add independently chosen edge cases, generated inputs with known properties, or held-out cases when justified; this applies to deterministic code as well as model products. Hidden cases may test disclosed obligations, not introduce undisclosed requirements. Record exposed failures and avoid claiming that repeated success on the tuning examples establishes broader behavior.

### 9.6 Make acceptance explicit

Every required evaluation needs:

- Requirement and contract IDs.
- Dataset/fixture/workload identity and supported version scope.
- Exact invocation, environment, setup, and cleanup.
- Expected result or justified quantitative acceptance criterion.
- Actual outcomes, including all required cases and meaningful subgroups.
- Result state: `passed`, `failed`, `inconclusive`, `blocked`, or `not-run`.
- Limitations and the classes of failure it does not test.

A typecheck establishes structural validity, not preserved product behavior. A disabled optional scaffold may need only structural checks; a functional replacement needs behavioral evidence appropriate to its obligations.

Zero selected tests, missing fixtures, silently skipped integration tests, unavailable telemetry, or an invalid benchmark must not be reported as a passed behavioral gate. An evaluation environment failure is distinct from a candidate failure, but neither establishes acceptance.

The gate passes only when every applicable **required** check passes against the identified candidate. Any required `failed`, `inconclusive`, `blocked`, or `not-run` result prevents that claim. Report advisory results separately. An explicit exception can authorize a limited next action under existing project policy, but the unmet check remains unmet and its supported correctness claim remains unestablished.

Report **evaluation execution** and **product-obligation verdicts** separately in co-retained machine-readable evidence as well as the checkpoint. An evaluation-improvement slice may finish successfully by confirming a product defect; that obligation must still read `failed`, not be hidden behind an all-green assertion total. Identify healthy cases, deliberate negative controls and defect confirmations in the selected rows. Require strict boolean outcomes, unique nonempty check identities and an explicit required selection; malformed rows or partial selection must not silently disappear.

Before freezing a consequential gate, map each declared obligation to the cases
actually collected and executed. A fixture branch, scenario name or validator rule
that no selected case reaches supplies no coverage. Resolve missing cases before
candidate judgment; retain the earlier selection and obtain the newly required
observations rather than downgrading the declared promise to fit the existing gate.
Canonicalize module/suite, class/group and parameterized case identities separately
when translating runner formats: folding a group into a module path can falsely
reject an unchanged selection. Derive subgroup counts from those actual identities,
and check reviewer summaries against them too.

When raw observations are required, retain them before fallible assertions for
healthy rows as well as rejected rows. Validate the actual required payloads,
types, nonempty collections and case identities; a receipt-class label or successful
test row does not prove those observations were serialized. Explicitly classify
rows whose adapter can retain only public output, without claiming upstream capture.

Retain row-level healthy observations on a failed run while keeping integrated acceptance failed. Record which clauses were not reached after a first failing assertion; that rejection proves the failed conjunction, not independent sensitivity to its remaining clauses. The accepted candidate must execute the complete frozen obligation, and a wrapper's overall non-acceptance label must not be misreported as an individual healthy control having failed.

When a runner emits a report, acceptance MUST also honor process exit status, report validity, selection, suite/setup/teardown failures and unhandled runtime errors. All assertion rows can pass while the run itself fails. Prefer the runner's structured results; retain actual selected/passed/failed/skipped counts and distinguish an unexecuted gate from a candidate failure. Do not hardcode a historical test count as a substitute for validating the required selection and current gate identity.

A composite command can hide an inner failure when its final diagnostic command succeeds. Propagate the operation's exit status through wrappers and independently assert the required semantic result: successful discovery plus exact returned content for a positive probe, or the declared rejection for a negative control. An outer zero exit with a failed inner probe remains a failed attempt. Preserve the original receipt and correct the wrapper for future invocations rather than relabeling the old result.

Error handlers must preserve failure semantics: logging an unhandled rejection or browser exception only to stderr/observations must not turn it into success. Collect required runtime errors across every page/context/process the journey creates, including fresh reload observers and target crashes, and feed them into the structured verdict and exit status. Distinguish an intended assertion rejection from an unmet case prerequisite, unsupported UI selector or tooling failure: all prevent acceptance, but only the first can demonstrate behavioral sensitivity. A passing per-obligation row on failed required selection, execution or teardown does not establish acceptance of that claim.

An injected operation failure can be a required expected outcome without excusing evaluation failures. Reconcile attempted mutations with actual acknowledgments or identified expected transport failures, bound to the actor, resource, method and attempt where applicable. Require the declared error class and state effect; unknown failures or missing outcomes still fail. Retain deliberate network-error diagnostics separately, without blanket exemptions for page exceptions, unhandled rejections or unrelated errors.

Classify failures within the actual observation phase and correlate each permitted failure one-to-one with its cause: an intercepted request identity or an instrumented intentional close of the exact connection, ordered within a justified delivery bound. Ordinary framework cleanup during an action can cancel an existing subscription inside the probe window as well as before it. An error spelling or source-based prediction of cleanup is insufficient to exempt it; collect actual invocation evidence, retain unmatched and late events, and keep unexpected failures fail-closed. New instrumentation cannot retroactively supply observations missing from an earlier run.

Separate primary failures from dependent contamination. A failed barrier or setup stage can leave owned work pending and make later count, selection or drain checks fail; these remain failures but are not independent product defects. Stop the dependent sequence or isolate its owned resources before another independent case, retaining the original rejection and its causal classification. Cleanup must not convert the failed case into acceptance.

For a multi-stage run, establish completion from durable stage receipts and the final process outcome, not console output that may be truncated. Compute success after finalization and required cleanup; failures there must invalidate the success report and exit status. A missing or malformed observation of external effects is not an observation of zero effects.

Distinguish an unexpected runtime exit from the evaluator's deliberately initiated shutdown. Record the lifecycle phase before issuing the owned stop; do not classify a normal teardown exit as a scenario crash or excuse an earlier crash as cleanup. Collect failure observations even when the scenario throws, persist a provisional receipt before removal, then retain the final verdict with actual teardown/retention outcomes. Exercise missing-log and failed-retention controls where these are load-bearing.

Declare the stage and environment for which each check is required: local verification, release, operational observation, or retirement. A local gate can pass while a required live integration check still blocks release. Name that narrower result explicitly; do not reclassify a missing check after the run to obtain a broader claim.

For a timed readiness failure, correlate the attempted connection, server admission/compilation, response headers and consumer readiness using retained observations. Report the failing phase and any unavailable landmarks; a development compiler stall is distinct from a proven product transport defect. Later readiness or correct state does not satisfy the missed bound. A targeted fresh execution needs a documented diagnosis or changed execution condition; keep the failed attempt and the unchanged gate rather than widening its deadline or retrying until green. A production latency claim still needs its identified production environment.

### 9.7 Avoid test-gate erosion

Do not make a failing candidate pass by deleting assertions, relaxing thresholds, updating snapshots indiscriminately, mocking away the broken behavior, or retrying until a favorable sample appears.

When a gate is wrong, correct it as a separate knowledge change with rationale and evidence. Preserve the original failure record. Freeze the revised gate before judging the revised candidate. Then run the corrected checks and the required healthy controls, in an isolated copy, against the unchanged baseline or a preserved known-defective case to confirm the gate still rejects the intended failure and still accepts valid behavior; this targeted confirmation does not require rerunning the full suite or changing the shared source tree.

Record the corrected criterion and gate version, including the supported boundary evidence. If separately versioned runners exchange check IDs, reject superseded IDs rather than silently accepting stale receipts. Keep stronger assertions at the boundary that actually supports them; correcting an unreachable consumer check does not retire its producer's obligation.

Separate an observation-timing repair from an acceptance change: waiting for a committed value may repair the driver while the exact-value obligation remains unchanged. If retained raw observations contain all inputs needed by a revised comparator, re-execute that comparator read-only to test sensitivity without replaying the journey. Label this as retained-evidence rejudgment; do not retrospectively claim newly added error collection, timing or lifecycle checks ran. Missing observations require a targeted execution or an explicit unverified result.

If a strengthened gate adds assertions, a retained failure of an unchanged conjunct can still prove that the defective baseline fails the conjunction. It does not independently prove sensitivity to the added requirements or establish that their observations were collected. Keep the old receipt/version intact; obtain the new observations against identified inputs and scope the baseline-rejection claim precisely. Correcting a selector to the supported UI label changes the adapter, not the required value; retain the failed attempt and classify its cause separately from product defects.

For existing failures, document whether they affect the selected obligations. Unrelated baseline failures can be tracked separately; failures that undermine the replacement claim must be resolved or constrain that claim.

### 9.8 Make performance and reliability measurements comparable

When a slice changes an operational promise, record hardware/runtime, dataset size and distribution, concurrency or arrival rate, warmup, cache state, measurement duration, and dependency conditions. Compare baseline and candidate under equivalent conditions; retain failures and timeouts in the results instead of measuring successful requests alone.

Measure the outcome the consumer experiences: end-to-end latency, relevant tail percentiles, queue wait, rejected work, throughput, peak resources, and cost per completed task. Faster internal computation does not compensate for a growing backlog or slower failure response. Include saturation and recovery behavior where they can invalidate the contract.

For flaky evaluations, preserve each outcome and investigate the source of nondeterminism. A quarantined check still represents an uncovered obligation unless another valid gate covers it. Re-run because conditions or code changed, or because a predeclared sampling plan requires repetition, rather than to manufacture a green result.

---

## 10. Evaluate probabilistic and agentic behavior

Apply this section to products whose runtime behavior includes models, agents, ranking, recommendation, extraction, or other stochastic outputs. AI-assisted development alone does not require a runtime AI evaluation stack.

### 10.1 Separate hard invariants from quality judgments

**Hard invariants** include permitted tools, resource scopes, schema validity, enforced budgets, credential handling, authorized data access, and required lifecycle events. Enforce these in deterministic system boundaries where possible and evaluate them directly. A model's instruction to respect a rule is not enforcement.

**Quality dimensions** include answer support, retrieval relevance, extraction precision, task completion, helpfulness, and ranking quality. Evaluate these with appropriate reference sets, rubrics, statistics, and human calibration.

Do not combine everything into one average that lets fluent answers compensate for a violated isolation invariant. A finite test run with zero critical failures provides bounded evidence; it does not prove an impossible future failure rate.

### 10.2 Version the experimental conditions

Record the applicable:

- Provider, model identifier and actual revision when available.
- Prompt and tool-schema versions, generation settings, seeds where supported.
- Retrieval corpus, embedding model, index configuration, chunking, reranking, and access scope.
- Agent policy, tool implementations, retry and fallback behavior.
- Dataset split, judge version, rubric, workload, concurrency, and budget.
- Known provider nondeterminism and unavailable version details.

Changing an embedding model can change retrieval behavior and stored-state compatibility even when vector dimensions remain equal. A matching API shape does not establish model equivalence.

Missing historical provenance must not be filled from the currently configured producer without evidence. Preserve the uncertainty, define the compatibility policy, or rebuild a separately identified representation from known inputs. A new metadata stamp cannot certify how old state was produced.

### 10.3 Use representative cases and independent judgments

Include ordinary tasks, difficult domain cases, ambiguous and unanswerable requests, historical failures, long inputs, multilingual cases where supported, and adversarial inputs relevant to the product's trust boundaries.

Separate development/tuning examples from held-out acceptance cases when repeated optimization could overfit. Track dataset origin and intended population. Minimize or de-identify captured operational data while preserving the failure mechanism.

Use model judges only with a versioned rubric, calibration against reviewed examples, checks for evaluator bias and prompt injection, and explicit uncertainty handling. A different model is not automatically an independent oracle. Inspect judge disagreement and critical outliers directly.

### 10.4 Define statistical acceptance before looking at the candidate

Choose sample size, repetitions, meaningful regression margins, aggregation, and important strata before the run. Use paired cases and comparable conditions for old/new comparisons where possible. Report absolute results as well as the difference.

For each quantitative claim, state:

```text
metric + population + sample size + workload + baseline
       + acceptable margin + uncertainty method + decision rule
```

Use an appropriate confidence interval or uncertainty estimate; do not require a universal confidence level for every product. Too little evidence or excessive variance yields `inconclusive`. Record all attempts. Repeatedly trying candidates against the same holdout consumes its independence; refresh or revalidate it when necessary.

A deterministic acceptance rule can consume probabilistic evidence. It cannot make the underlying measurement deterministic. Keep this distinction explicit in CI and release reporting.

### 10.5 Evaluate the complete experience

For retrieval or knowledge products, relevant checks include:

- Retrieval relevance at the accepted cutoff and coverage of required evidence.
- Faithfulness of claims to sources, citation validity, and source access permissions.
- Appropriate abstention when evidence is missing or contradictory.
- Correct multi-turn state, identifier continuity, truncation handling, and memory isolation.
- Tool selection, bounded iteration, duplicate-effect prevention, and recovery from tool errors.
- First useful result, total completion time, tail latency, token use, and cost per successful task.
- Deletion and permission changes propagating to indexes, caches, and subsequent answers.
- User-visible distinction between refusal, unavailable dependency, partial answer, empty result, and success.

Compare complete workflows, not only isolated prompt outputs. A better answer generator that loses memory, stalls the UI, leaks scope, or doubles operating cost may be an unacceptable replacement.

---

## 11. Make documentation part of the product contract

Documentation must help a user, operator, developer, or agent perform real work against a known product version. Treat documentation drift as an observable product defect.

### 11.1 Maintain an authority map

For each documentation surface, identify its audience, canonical source, supported release, owner or stewardship role, and validation method:

- README and getting started.
- User guides and interaction behavior.
- API, CLI, event, schema, and SDK references.
- Configuration and default behavior.
- Operations, deployment, upgrade, backup, recovery, and retirement.
- Architecture, intent, and decision records.
- Agent instructions, skills, examples, and tool descriptions.

Generated references should derive from canonical schemas or metadata where useful. Human-authored material should explain semantics, workflows, exceptions, and reasons that schemas cannot express.

Determine generated ownership from the actual generator's outputs and configured
include/exclude map, not a filename or directory convention. A manifest can omit
a static discovery file; a site mirror can omit a separately maintained handbook.
Give each source and generated artifact an explicit writer and its own check.

Avoid copying an entire API contract into multiple READMEs, skills, and internal guides. Link shared truth, generate stable portions, and validate the audience-specific examples that remain.

### 11.2 Update docs with the behavior they describe

For each changed capability:

1. Identify affected public and internal documentation surfaces.
2. Mark whether a statement describes current released behavior, a pending candidate, or a supported legacy version.
3. Update examples, defaults, failure semantics, compatibility notes, and operational consequences together.
4. Update agent navigation so the next agent can find the canonical artifacts.
5. Remove obsolete guidance only when its supported version or consumer obligation has ended.

A documentation discrepancy should trigger investigation, not an automatic decision that either code or prose must be correct. Version skew between a checkout and published docs is a hypothesis to verify.

Check defaults and precedence per field against executable branches, not only comments, annotations or one nearby test. Truthiness-based merging can conflate omission with explicit false/empty values; a string-field precedence test does not establish the same rule for a boolean. Align prose with confirmed supported behavior without silently changing that behavior or inventing an approval dependency for an ordinary correction. Keep unresolved behavior separate, update affected duplicate references and generated content identities, and distinguish source-derived schema comparisons from a complete runtime schema.

Likewise, a shared feature name or similarly named helper does not establish a
shared pipeline. Trace each documented entry to its actual stages, flag consumers,
weights, result score and enrichment paths. Split statements spanning different
branches instead of correcting one entry by making its sibling's prose false.
Record a multiplier's receiving depth and cap explicitly when that depth differs
from the caller's requested count.

Review branch-spanning prose against a small source-backed entry map: transport,
implementation, configuration, deadline, dependency fallback and result projection.
Check the final text, not only the writer's summary. Keep rejected conflations and
their corrections in the change record; public guidance should explain current
use and version applicability without carrying private baseline history or test
totals. A latest dirty candidate must not be described as every released version.

Trace error prose through authentication, quotas, payment and other dependencies as well as the handler and contained retrieval branches. Permission validation is not an existence lookup; an unavailable dependency is not an index-readiness contract; an ignored unsupported field supplies no restriction beyond the caller's actual resource/key scope. Derive identifier type, namespace and lookup meaning from their producers and consumers. Document fresh retrieval separately from client-carried context, and describe a model-selected optional tool as available rather than guaranteed to run.

Before claiming full schema parity, capture the complete actual runtime-generated schema using real route/type declarations and the real generator in an identified, configuration-isolated environment. Avoid unrelated service startup where possible; record tool versions, runtime configuration, operation selection and reference resolution, and retain both full schemas and exhaustive differences. Keep raw name/structure differences alongside explicitly documented dereferencing or representation normalization. Equivalence after ignoring titles/examples is a different claim from complete structure equivalence; array-order and other heuristic classifications need caveats. Count unique operations separately from comparison facets or repeated operation identifiers; generated client names can make apparently cosmetic identifiers contract-visible. A freshly bound, byte-identical schema may inherit its full comparison, but generator evidence is not deployed-runtime parity and comparison alone does not authorize schema edits.

### 11.3 Evaluate documentation

Use applicable checks:

- Build and link checks for the documentation site.
- Execute copy-paste examples against a disposable supported environment.
- Validate request/response examples against schemas and real behavior.
- Exercise quickstart, upgrade, export/import, and troubleshooting paths.
- Check configuration names, defaults, precedence, and feature-gated behavior against the actual configuration source.
- Verify documented smoke, health, and configuration endpoints against the product's actual routes before executing them; a runbook typo is corrected in the documentation, not by adding a product endpoint.
- Verify package names, entry points, installation commands, and supported runtime versions.
- For agent-facing docs, ask a fresh agent or isolated session to perform a representative task using only the public instructions; compare its outcome with a defined rubric.

Log the task, documentation version, environment, observed result, and missing information. A fresh reader should not need private implementation knowledge to complete the intended workflow.

For session-instruction adoption, distinguish three kinds of evidence:

| Check | Procedure | Supported claim |
|---|---|---|
| Content / pointer check | Explicitly read the root/scoped guides; check imports, targets, commands and consistency | The written instructions are usable and navigable; no automatic-loading claim |
| Fresh-session behavior | Give a fresh agent an ordinary maintenance task or continuation request **without naming the instruction files or reminding it of the seven primitives**; observe what it discovers and does | The effective instructions guide the sampled task; record any explicit reads or extra prompting |
| Loader observation | Inspect the named harness's initial context or loader diagnostics at the claimed root/subtree/delegated entry path | Which files actually arrived automatically or through an expanded import in that environment |

Use the latter two together when claiming observed per-session adoption. A manually preloaded prompt is not an autoload test; one successful task is not a guarantee that every agent or harness will comply. Where the loader cannot be observed or a supported entry path cannot be exercised, retain the working read fallback and state that narrower evidence limit.

Before the task, choose a small rubric: does the agent find the canonical contract and state owner, distinguish preservation from a proposed delta, select meaningful required gates and recovery needs, protect existing work/consumers, and place new knowledge in its owning guide or packet? Can it choose no change when the promise already holds, reason about failures from operation, version, path and event order instead of inferring a cause from one symptom or a health signal, and leave an accurate evidence claim and next step without the previous conversation? Exercise a relevant scoped path as well as the root when claiming both. Record the task, entry path, delivered/read files, instruction identity, observed decisions, results and gaps. Repair missing instructions at their owner; do not simply add more copies. This evaluates instruction delivery/usability, not implementation reconstruction.

For published mirrors, validate nonempty content, important sections, examples and links; a successful site build or an empty comparison is not a semantic consistency check. Bind each inclusion probe to a configured source page and the generated section it contributes. A phrase shared with an excluded surface proves the included page's wording, not publication of that excluded surface. Verify separately maintained material at its own boundary, and keep machine-readable probe labels consistent with that distinction. Reuse inclusion/generation from one canonical body when the toolchain and audiences permit it.

### 11.4 Keep the durable pack concise and navigable

Document contracts, invariants, failure behavior, ownership, operational expectations, and rationale. Keep implementation navigation where it is useful, but do not make line-by-line implementation descriptions the only specification.

Prefer one authoritative statement with stable references over repeated summaries that can diverge. Remove stale diagrams and duplicate guidance during compaction while preserving decision history and supported-version instructions.

After compound or parallel documentation edits, read the final on-disk entry index and follow its current checkpoint/next-action links. Successful patch application or an accurate detailed receipt does not prove the entry packet retained every intended edit. Mark earlier counts and next-step statements as historical, synchronize current version pointers, and verify that a fresh reader reaches failed obligations as readily as passing evidence.

---

## 12. Preserve provenance and operational evidence

### 12.1 Record derivation, not just diffs

A future maintainer should be able to answer:

- Why was this capability changed?
- Which intent and architectural target produced the candidate?
- Which constraints came from incidents, customers, measurements, or deliberate tradeoffs?
- Which alternatives were considered and rejected, and why?
- What generated or edited the artifact?
- Which evaluations judged it, under what conditions, and with what results?
- What operational evidence supports continued acceptance?
- What remains uncertain?

Use existing version control, ADRs, CI artifacts, and release records. Add only missing relationships. Record concise decision rationale and observed actions; raw internal reasoning transcripts are neither required nor a substitute for evidence.

### 12.2 Address artifacts explicitly

Reference requirements, contracts, prompts, templates, schemas, datasets, harnesses, adapters, build recipes, dependency locks, candidate artifacts, and reports by stable version or content digest where practical.

A generation record should include the base revision, dirty-tree patch identity if relevant, scope, accepted inputs, model/tool versions available to the harness, command/configuration references, candidate digest, evaluation results, and disposition.

Content identity and generation provenance are separate. Matching a baseline or retained artifact neither proves source use nor makes a behaviorally correct candidate fail; different bytes do not prove a fresh context. A replay tool should record identity comparisons and behavioral acceptance independently, leaving source context `unknown` unless separately established. Record accepted working inputs as well as revision lineage: an intentional, uncommitted contract change is not automatically stale merely because it differs from the base revision.

Do not call a dirty producer tree revision-identical merely because its entry
handler is unchanged. Bind the consumed files and transitive paths; state which
prior local repairs are part of the accepted basis. A sparse lock/binary sample
supports only that named sample, not a complete installed-artifact inventory.

The build recipe must identify necessary source, generated inputs, tools, dependencies, configuration, and external services. A lockfile does not guarantee that its packages or toolchain remain retrievable. Preserve accepted artifacts or controlled dependency copies when their loss would violate an actual recovery requirement; otherwise state the external dependency. Verify a clean build when making a rebuildability claim, rather than relying on an already-populated workspace.

Record the interpreter, resolved package versions, native dependency checks and image identity actually exercised. A lockfile-pinned install on another runtime is not evidence for the CI or production runtime. Isolated installs should use the identified source and locks without relying on or mutating another writer's working dependency tree.

When runtime identity is derived from package metadata, identify the authoritative declaration and retain it in the shipped input closure. Verify its resolution in the supported package layout; a stale literal or obsolete nested lock is not package identity. Keep advertised-version agreement, installed-artifact identity and distribution/publication as separate claims.

Distinguish a private copy of an existing installed graph, a fresh lockfile-controlled install and a clean rebuild. An authorized private copy can preserve dependency resolution for a local evaluation; record its source identity and native-binding execution, prevent cache writes to the original tree, and do not call it install/rebuild evidence. Development and production compilers may exercise different paths. Diagnose launch failures in the selected environment without changing product code or suppressing isolation gates merely to obtain a usable fixture; record any runner/environment change and its narrowed claim. Language-runtime network hooks observe only their intercepted paths, not every native socket or OS-level egress.

For browser tooling, identify the automation package and matching browser binary/revision actually used; external read-only installations are execution prerequisites, not product dependency changes or portable availability guarantees. A narrowly disabled development-only auxiliary connection may be a legitimate runner adaptation if the evaluated product path still executes unchanged. Record its reason and coverage limit; never disable the product transport or its isolation/error oracle to obtain a pass. Browser request interception also does not establish OS-level egress confinement: process-background services can bypass page-request hooks. Inspect available process diagnostics, retain such attempts separately from application/provider requests, and narrow the isolation claim accordingly. If the engagement requires process-wide confinement, use an appropriate owned control or name the missing prerequisite; a clean application tripwire cannot satisfy that requirement.

Use `unknown` for unavailable provenance. Never invent a model revision, successful test, reviewer, approval, or production observation.

Distinguish three claims:

- **Rebuildable artifact:** the implementation can be built from preserved inputs.
- **Replayable evaluation:** the recorded procedure can be rerun under known conditions, subject to declared external dependencies.
- **Behaviorally regenerable unit:** a new implementation can satisfy the surviving obligations.

Probabilistic generation does not promise identical source bytes. Store accepted generated outputs when future retrieval matters. Content addressing identifies an artifact; it does not prove that generation is deterministic.

### 12.3 Make evidence durable enough to retrieve

For every important evidence reference, retain:

- Collection time, environment, artifact identity, and applicable version range.
- Workload, population, measurement method, and limitations.
- Storage location, access expectations, and retention policy.
- A digest or other integrity reference when practical.

Do not rely on an expiring dashboard screenshot or a CI URL with short retention as the only explanation for a permanent requirement. Preserve a compact, reviewed conclusion and the minimum reproducer or durable aggregate needed to understand it.

Avoid committing credentials, personal production data, or identifying customer details into fixtures or generation records. Store secret references rather than secret values. This preserves usable evidence without turning the provenance system into a copy of production data.

Write a minimal result on failure as well as success **before cleanup**: candidate and accepted-gate identities, invocation/environment, stage, actual counts/outcomes, and a safe diagnostic identifying the failed obligation or prerequisite. Do not preserve arbitrary credential-bearing subprocess output. State whether raw artifacts are temporary and which reviewed aggregates, counterexamples and accepted source artifacts remain durable. A manifest records trusted inputs; a candidate-editable manifest is not independent tamper protection.

Receipt capture is part of a required stage's execution, not deferred closeout
formatting. Retain its actual command, times, exit outcome, input identities and
safe raw observations before the next fallible stage. For idempotency claims, keep
both compared outputs. A transcript-only result must remain labeled as such; when
the missing artifacts cannot be recovered, rerun only the necessary receipt-bearing
stages against identified inputs. Reuse unaffected retained builds and judgments.

Retain the structured observations used by the verdict before removing scratch, so acceptance can be audited later. The replay invocation must include the flags and configuration that select the claimed phases and environment; a default command that omits an opt-in phase does not replay its evidence.

Retain the actual executed gate/adapter/fixture bytes and relevant before-edit source alongside their hashes, including selected uncommitted inputs a version-control diff would omit. A digest identifies an artifact but cannot recover it after the working file changes. Prefer snapshots at execution time; if historical bytes must be recovered from a documented exact delta, verify every recovered digest against the original execution manifest and label the operation as byte recovery, not a new execution or product reconstruction. If unchanged current bytes are copied only after execution, verify them against that execution's digests and label this post-execution byte retention; it does not establish launch-time capture or a new run.

Keep gate identity, baseline identity and candidate identity separate in launch and
retention code. The frozen gate and unchanged neighbors stay pinned; an explicitly
changed implementation must match the identified candidate's execution-time inputs,
not the baseline digest it is intended to replace. Carry this distinction through
finalization as well as launch, and preserve the baseline bytes independently.

Challenge this identity treatment before a real candidate run: exercise the launch
and retention adapter with legitimately changed candidate bytes, a mismatching copy
and disallowed neighbor drift in owned synthetic fixtures. A correct launch check
does not prove that a later retention loop uses the same expected identity. Keep
these machinery self-controls distinct from product executions, and bind the
runner/import closure externally so self-hashing does not create a circular freeze.

Filenames, modification times and matching dirty-status lists are not content identities. Reconcile consumed runtime, complete evaluator/fixture closure, installed dependencies and execution tools against their recorded digests. When current acceptance combines fresh work with unchanged-input prior runs, label each inherited execution explicitly; do not rename retained test counts or receipts as newly executed evidence.

Compare a resumed slice against its accepted working-tree snapshot when attributing
that slice's changes. A diff from the published revision includes earlier local
work and cannot establish which session changed a checkpoint or consumer. For
append-only continuation claims, verify the preserved body directly against that
snapshot. Source line references must identify their before/candidate basis; an
availability claim needs an actual probe, not an assumed environment.

A reused fixture or template can carry metadata describing its own origin — captured evidence arrays, environment labels — rather than the executed run. Establish the executed mode and target from the owning verdict, declared run configuration and actual runtime inspection; treat inherited provenance strings and counts as describing the template, not the new target, and do not relabel inherited executions when a different mode or build variant is claimed.

Make scratch removal conditional on **actual receipt retention**, not a configured output path or an attempted copy. Verify that the destination contains the expected readable report before removal; write final cleanup and claim verdicts there afterward. On resume, recheck important evidence locations: a surviving aggregate can preserve a historical conclusion when raw artifacts are absent, but cannot establish current raw-artifact retrieval. Keep that distinction explicit and do not repeat completed work merely to conceal the retention gap.

Regenerability, ignore rules and modification times do not establish deletion ownership. A newly touched cache directory may contain pre-existing artifacts; never remove it recursively or with a time-based selector to clean one generated file. Prefer checks that produce no persistent artifact or direct their output into an owned destination. If cleanup crosses ownership, disclose the exact affected paths and evidence limits, preserve remaining inputs, and distinguish verified recovery from merely possible regeneration; a digest audit of other files cannot support an all-work-preserved claim.

When losslessly re-retaining owned generated diagnostics, verify payload bytes and link targets before removing the original. Copy tools can rewrite relative links even when regular file bytes match. Record any accepted metadata differences, the new container's digest and retrieval mapping, and the historical container identities; do not describe re-packaging as unchanged container bytes. Keep archival/setup process limits separate from product readiness bounds, and preserve originals through failed copies, comparisons or compression attempts. This procedure does not authorize altering live state or another owner's resources.

When a wrapper augments a retained result with consumer-specific verdicts or evidence scope, identify the authoritative final writer and hash the final bytes after augmentation. Check that final scope labels agree with the journey actually exercised. A read-only closeout verifier can confirm receipt validity, required selection, current consumed-input hashes and preserved baselines; it is identity/evidence validation, not a new journey execution or behavioral replay.

Keep each stage's actual consumed-input closure distinct from files sampled only
for preflight or later report authoring. Preserve the original stage manifest,
then classify a knowledge-only addendum by its real dependencies instead of
rebuilding an unchanged producer or silently ignoring a changed execution input.
When byte-preserving append-only provenance is claimed, compare the retained
prefix exactly: harmless formatting edits still break that identity claim.
Correct accidental byte drift or disclose an intentional delta without relaxing
the preservation oracle; retain failed audits separately from product failures.

Bind a receipt's final digest from a separate manifest or successor record; do not
embed a self-digest and mistake an earlier body hash for the final file. Keep actual
command argv and execution/observation times in producer receipts; hand-entered
report times are assembly metadata or explicit approximations. A probe label must
name what its actual needle establishes. If finalization catches a source-only
formatting defect, retain the failed audit, correct and rebind those source bytes,
and rerun only the invalidated stage when executable/build inputs remain unchanged.

When final acceptance combines stages from a stopped batch and later targeted executions, retain the original batch's nonzero outcome. Enumerate every accepted stage's actual invocation, exit observation, final receipt, candidate/gate identity and environment adaptation; distinguish captured process status from a manually recorded observation. Reconcile all required stages against the final inputs before claiming integrated acceptance. Do not rewrite a failed batch as successful or infer process exit solely from passing assertion rows.

A documentation-only harvest has its own provenance. Preserve the executed playbook/instruction basis, gate bytes, runtime identities and original verdicts; update current guidance and continuation pointers without rewriting old receipts to the new version. Retain before-edit knowledge snapshots and write the harvest's evidence/current-input audit separately. An audit that verifies old evidence is not a journey replay and must not overwrite the final report it is validating. When a campaign permits only documentation or checkpoint addenda, verify the unchanged remainder by digest and make additions append-only so every original byte prefix stays verifiable; treat a dirty working tree as preserved evidence input, not something to clean or commit to obtain a tidy record.

When verifier corrections produce several reports, name the authoritative accepted receipt by its exact versioned path and digest in the checkpoint; an unnumbered filename can still belong to a retained failure. Audit both structured outcomes and human-readable summaries against observations. A mistaken summary such as "removed" for something that was already absent needs a separate correction, not a rewritten receipt or an invented operation. Preserve the failed verifier's actual input/exit record and distinguish machinery diagnosis from a new product execution.

When reusing retained evaluation machinery, reconcile its latest accepted
corrections and judgment sidecars too. A preserved producer script can precede an
oracle correction; copying that earlier template alone can reintroduce the same
mislabelled probe or invalid criterion. Bind the reused executable and the accepted
evaluation semantics separately, then check their actual pairing before execution.

Reconcile counts against each invocation's actual selection and counting unit. A larger passing suite does not disprove a smaller run over fewer files; sum the selected subsets before correcting a total. Count control rows, new test definitions, parameterized cases and operation facets separately, and say whether roots or aggregate entries are included. Retain additive corrections to mistaken summaries, including machine-readable labels and review summaries. Keep measured execution timestamps separate from report-assembly stamps; distinguish bytes, decimal units and binary units. A conditional review is accepted only after its stated supplements/checks are actually supplied and bound in the final record. An unavailable required linter or other tool remains unverified: a syntax check or a different successful gate is not equivalent evidence for the missing check.

### 12.4 Observe claims, not only process health

Connect telemetry to obligations:

| Claim | Possible evidence |
|---|---|
| Accepted work eventually reaches a terminal state | Age of pending work, completion/failure outcomes, abandoned-task count |
| Retry does not duplicate an external effect | Idempotency collision and effect counts, reconciliation results |
| Users retain a complete conversation | State-persistence acknowledgments, replay checks, missing-update diagnostics |
| A dependency failure is recoverable | Error classification, retry budget, recovery time, backlog drain |
| A retired interface has no supported consumers | Version-tagged traffic, job inventory, client lifecycle and consumer confirmations |

Status surfaces should distinguish `healthy`, `degraded`, `failed`, `warming-up`, and `unknown` as appropriate. A lack of observations is not health. Preserve diagnostic contracts that operators or automation consume.

Record evidence freshness and measurement coverage, including sampling, disabled instrumentation, and excluded populations. Check that the observation mechanism itself works. An old successful run or a silent broken monitor cannot support a current claim merely because the implementation digest is unchanged.

When a runtime's external reporting must not reach a live service during evaluation, point it at an owned local sink and prove the delivery pipeline with a deliberately thrown synthetic error as a positive control; a run with reporting enabled but never exercised does not establish that reporting works. A failed positive control blocks whole-run acceptance: diagnose whether the cause is sink/fixture setup or the product's reporting path before repairing it. If the evaluator relays captured artifacts to that sink on the product's behalf, keep the relay scoped to the captured artifacts and label the result as relayed observation, not the product's live delivery; each build variant's actual delivery configuration is a separate claim.

---

## 13. Make state and migrations survive replacement

State frequently outlives the implementation. Treat data formats and transition rules as slow-layer contracts, even when the library writing them looks like a replaceable leaf.

### 13.1 Inventory state

For each unit, identify:

- Durable records, files, indexes, caches, sessions, checkpoints, and queued messages.
- The authoritative source and derived representations.
- Mutation ownership, transactions, identifiers, references, and consistency rules.
- Serialization formats, schema versions, defaults, encryption/key dependencies, and retention.
- Export/import, backup, recovery, repair, and reconciliation procedures.

“Derived” does not mean free to delete. Establish that the inputs, transformation versions, permissions, compute budget, and reconstruction time are available. Recomputing embeddings or indexes may change behavior or cost more than recovery allows.

Preserve semantic distinctions in boundary values throughout their lifecycle: an absent filter can mean unrestricted access while an empty allowed set means no permitted resources. Creation-time nonempty validation does not establish that updates, deletion of the last grant or historical state cannot produce emptiness. Trace those producer paths, then carry the effective scope through scalar/list translations, query builders, counts and optional enrichment. Evaluate unrestricted, nonempty and empty controls against the existing confinement promise; preserve separately supported direct-resource behavior rather than inventing a blanket denial or new deletion policy to repair a truthiness error.

### 13.2 Define the compatibility matrix

Test relevant combinations explicitly:

| Reader/writer or participant | State/protocol to check |
|---|---|
| New implementation | Existing historical state and supported old inputs |
| Old implementation during rollback | State written by the new implementation during the rollout window |
| Mixed running versions | Concurrent reads/writes, messages, locks, and ownership |
| Long-lived or offline consumer | Stored payloads, sessions, tokens, and delayed work |
| Recovery tooling | Backups, exports, migrations, replay and repair formats |

If a combination is intentionally unsupported, describe the deployment ordering or interruption it requires. A rollback command is not a recovery plan if the old binary cannot interpret the state that now exists.

Matching persisted file shapes establishes only the named format compatibility. It does not establish that another tool's executable was run successfully against those files. Derive state identity and isolation from the supported key/namespace contract; do not invent credential-scoped storage or stronger cross-tool guarantees from format agreement alone.

A fresh schema's automatic reference cleanup can hide a missing application-level detach that historical schema versions require. Exercise a fixture without that action when making the corresponding compatibility claim. A copied or source-extracted transition pattern proves only the named pattern under its adapter; it does not establish that the real handler invokes it, authorizes it or performs it atomically. Keep pattern controls and actual entry-point execution distinct, retaining the compatibility constraint until its supported historical states are retired.

### 13.3 Prefer explicit state transitions

For a compatible migration, a useful sequence is:

1. **Expand:** introduce a representation or capability without breaking supported readers.
2. **Migrate:** backfill or convert with progress, idempotency, resumability, and reconciliation.
3. **Validate:** compare domain invariants and representative records, not only row counts.
4. **Switch:** change authority or routing at a defined point.
5. **Observe:** verify mixed-version and failure behavior over the required window.
6. **Contract:** retire old representation after its consumers and recovery obligations end.

Dual writing needs a defined authority, failure handling, reconciliation, and end condition. An extra write path without these creates more uncertainty than it removes.

Distinguish a primary transaction abort from a failure in optional post-commit follow-up. When the primary transaction fails, roll back the failed transaction before returning control or reusing the session, connection, or shared resource: swallowing the exception without rollback can poison later operations on that resource and turn subsequent valid requests into failures. Conversely, when the primary result has already committed, a failed optional follow-up does not undo it: report the primary outcome according to its actual durability, discard only the uncommitted follow-up work, and continue or fail the remaining follow-up as the contract declares. Do not rely on session or process teardown to reverse committed state, and do not report an error as if the whole workflow had been undone.

Visible completion, transport completion and durable completion can occur at different times. Bind late results and persistence to the originating operation generation and resource/session identity; define whether a superseded result is ignored, reconciled or stored only for its original owner. A late callback must not borrow whichever mutable state happens to be current, overwrite a snapshot restored for an edit/retry, or persist another operation's in-flight content. Keep externally visible effects out of state-update functions that the framework may replay. Preserve the declared storage concurrency contract: an atomic last-writer-wins transaction does not itself prevent a stale client callback from writing the wrong coherent snapshot.

Separate resource ownership from view ownership. Leaving a view may detach rendering without canceling a valid originating operation's persistence; returning to the same resource must not accidentally strand its recall or edits behind an obsolete view generation. Specify supersession by user action instead of treating every later operation alike:

| Transition | Ownership question to resolve and evaluate |
|---|---|
| Ordinary continuation while prior post-completion work remains | May the prior result update its settled prefix or inform current recall, and until what newer completion? |
| Edit, regenerate or fork | Which prior generations are superseded, and which immutable at-request snapshot must the redo retain? |
| Navigation away/back | Which effects still belong to the origin, and which rendering/loading/ref updates require the current view? |
| Newer operation settles before an older result arrives | Must the older result be discarded or reconciled without overwriting the newer coherent state? |
| Move, reparent, pin or other organizational change | Which association/metadata may change, which history/state/defaults must remain, and does valid origin work retain eligibility? |
| Mutation rejection or acknowledgment after navigation | Was the write accepted, and which currently selected view may reflect its result? |

For organizational changes, declare the permitted field delta and independently compare the rest of the authoritative representation, including opaque values, metadata, identity, attribution and relevant timestamps. Target-container creation defaults must not silently replace existing resource state unless that is an accepted behavior change. Exercise consent/cancel, current permission at dispatch, valid late auxiliary work, and direct consumers of retained retry/edit state.

Where removing a container retains its child resources, specify each child's surviving owner, association, grants and authoritative state. Container-management authority must not be assumed to grant access to another owner's now-detached resource. Compare all affected owners' surviving state and unrelated resources, then evaluate fresh admission and valid pending work separately for each actor. Continued author-owned work does not prove continued non-author authority or termination of previously admitted subscriptions.

Mutation success is necessary but not sufficient for updating a current view. A rejected operation must not select its proposed target; a valid origin transaction may commit while the user navigates, but its delayed acknowledgment must not rebind another resource's context. Recheck current ownership after awaiting acknowledgment instead of trusting captured selection values. When the effect is a simple state projection, prefer a pure current-state comparison over a new ref, registry or persistent protocol; keep I/O and other side effects outside replayable state updaters. Evaluate origin durability and selected-view effects separately, preserving the declared concurrency and valid-work policy rather than introducing persistent coordination to repair a local callback.

For overlapping successful mutations of the same selected resource, current-view ownership alone is insufficient: an older acknowledgment can still name a superseded association. Derive that projection from positive association evidence at an existing authoritative read boundary when available, rather than the callback's target argument, latest gesture or last-delivered response. Preserve the declared commit semantics and accepted effects; do not add persistent revisions, serialize user work or reload unrelated history merely to repair a local context binding. Missing visibility may reflect lost permission or incomplete reconciliation, so absence alone must not invent a detach or revocation rule.

Read-request ownership and selected-view ownership are separate dimensions. The latest requested refresh can still have been dispatched for a view that navigation replaced. Capture the resource/session and view generation before awaiting; a global list refresh may remain useful while its selected-context projection has become obsolete. Apply only the permitted association fields under both latest-read and current-view guards, preserving history, opaque recall and immutable retry snapshots independently. Latest completed read evidence is not an instantaneous or distributed-state guarantee.

Treat remote adoption as an explicitly read-only transition unless the contract authorizes a write. Bind asynchronous refresh results to their resource, current view generation and declared competing-read order; reject results made obsolete by navigation, newer refreshes or local work. Unchanged IDs/count can still carry changed content, feedback or memory. Apply the accepted full state without writing it back or performing ref/effect mutations inside replayable framework updaters. When returning to a still-valid local operation, reattach its current rendering/loading/cancellation controls while preserving its immutable at-request snapshot. Visible completion alone does not expire retry state the contract still retains. On terminal return to the same valid operation, keep that snapshot independently of fresh loaded history/recall; a different operation or fresh instance uses its declared stored fallback. Evaluate auxiliary work landing both while away and after return, then exercise retry/edit directly so a normal re-fetch cannot mask a lost snapshot.

Select dependent state coherently: rendered history, opaque recall and the immutable snapshot used by an edit/retry must belong to the declared selected operation/view. Under full-snapshot last-writer-wins, a valid late origin write may remain eligible after remote adoption and may win storage; that does not permit remote history to be paired with origin recall in the current view or a later consumer write. Freeze the allowed coherent outcomes before implementation, then check origin durability and the selected view separately. Do not cancel legitimate work or invent a stale-write rejection merely to avoid the coherence problem.

Scope supersession to the declared controller or coordination domain. Independent client instances can share durable state without sharing their operation registries: an action in a fresh instance does not revoke another instance's valid origin work unless an explicit shared coordination contract says so. Evaluate each origin's write eligibility and each consumer's selected coherent state separately; stored fallback in a fresh instance does not imply recovery of another instance's immutable request snapshot.

Treat live progress as an ephemeral overlay when the architectural target permits that model. An active overlay should not prevent adoption of missed settled state; compose a fresh base with the latest owned overlay snapshot, accounting for tokens arriving while the read is pending. Replacement, disconnection and completion need explicit overlay retirement rules so replay cannot leave ghost or duplicate entries. Keep snapshot/ref mutations outside replayable state updaters, and verify overlay/request ownership through the actual consumer before claiming race coverage.

Ephemeral relay/replay ownership is distinct from durable storage concurrency. If one overlapping operation is selected for live observation, use an operation identity, not just the caller or resource ID, to guard its token appends and cleanup. An older completion must not delete or contaminate the newer selected replay, including when both operations have the same caller. This local ownership repair need not introduce persistent revisions or compare-and-swap. Independently verify that every originating operation completes under the existing durable contract; a latest-selected live view does not imply that the latest-started operation wins storage or that concurrent histories are merged.

Capture owned snapshots before dispatching queued full-state writes. If local serialization is used, evaluate its ordering and failure behavior and account for auxiliary edits to pending snapshots. A client-local queue does not establish ordering across clients, compare-and-swap, or a server revision contract. Keep those promises separate rather than adding persistent coordination solely to fix a local callback race.

### 13.4 Rehearse recovery realistically

Use sanitized historical fixtures and disposable stores to test interrupted migration, partial backfill, retries, concurrent updates, restart, and restoration. Define recovery point and recovery time expectations when they matter to the product.

A backup is useful only if restoration is feasible and verified. Restoring a pre-deployment backup can lose valid later writes; distinguish binary rollback, forward repair, event replay, and disaster restoration.

Verify a multi-resource restore at the level of records, identities, and referenced bytes in the fresh target — not by row counts, service health, or the restore command's exit status. Exercise the completeness oracle with deliberately incomplete copies (for example, records without their blobs) so it demonstrably rejects them. Retain the original snapshot; prove that writes made to the source after the snapshot are absent from the restored point, and allow only contract-declared recovery changes at the target — not an unspecified class of differences that could hide a live or partial copy. A restore rehearsed on a stopped or quiesced fixture does not establish online, atomic, or incremental snapshot guarantees, however convenient the storage library's wrapper appears.

Storage restoration and product recovery are distinct claims. For a product-recovery claim, boot the real consumers and exercise important restored-state journeys through supported entry points: authentication, permitted and denied reads, key use and linked content where applicable. Run on disposable copies after storage acceptance and before destructive controls, or on independently provisioned targets; capture the originals before and after to prove isolation. HTTP journeys do not establish browser behavior, and no-model runs do not establish model quality.

For process-restart continuity, observe the old process's actual termination, capture the durable bytes, and launch a fresh process on the same owned state root. Check startup's permitted effects and a subsequent consumer request's exact history/opaque state against independently known prior values. Two clients in one process do not test restart; a value still supplied by shared in-memory or fixture state cannot establish that persistence recovered it.

Consumer startup can legitimately mutate state. Enumerate accepted schema ensures, default records, backfills and bookkeeping precisely; derive expected values from pre-boot state or an independent rule. Match structural additions to their exact definitions and ownership. An unexpected write requires diagnosis and a recorded acceptance decision, not a broad metadata/timestamp exclusion or copying the consumer's output into the oracle.

Recover the whole authoritative representation, including journals, sidecars, blobs and producer/schema identity where applicable. Prefer a consistent snapshot or an independently rebuilt target with the original retained; renaming one live file is not a recovery procedure for a multi-file store. Validate documented commands against the actual configuration precedence and refuse unsupported combinations before destructive effects.

External effects such as payments or notifications cannot be undone by restoring code. Define duplicate prevention, reconciliation, or compensation consistent with the domain. If an artifact is intrinsically immutable or cannot be rolled back, constrain the replacement unit to a replaceable adapter or define an explicit migration to a new identity.

---

## 14. Run the regeneration pipeline

The pipeline turns a bounded knowledge change into an evaluated candidate. It applies to patches and feature evolution as well as replacement. Provenance spans every stage. Deletion and compaction operate alongside it, including when a removed requirement should cause removal rather than generation.

### 14.1 Stages and gates

| Stage | Inputs | Output | Gate before advancing |
|---|---|---|---|
| 0. Establish baseline | Scope, working tree, existing tooling | Inventory and baseline evidence | Environment and existing failures understood for the slice |
| 1. Validate intent | Evidence, requested change, canonical requirements | Preservation scope and accepted delta, if any | Relevant conflicts resolved; acceptance is testable |
| 2. Fix the target | Intent, architecture, consumers, state | Versioned unit work package and impact set | Boundary, ownership, compatibility, pace and recovery defined |
| 3. Prepare the oracle | Obligations, cases, fixtures, harness | Frozen evaluation gate and obligation ledger | Required/advisory checks defined; important failure classes observable; valid negative control where warranted |
| 4. Implement | Work package and allowed context | Smallest justified candidate and applicable build artifact | Structural and implementation-level checks appropriate to this change pass |
| 5. Evaluate | Integrated candidate, frozen gate, baseline | Behavioral report and disposition | Required checks, including affected journeys and accepted differences, pass against this candidate |
| 6. Introduce | Accepted candidate, rollout/recovery plan | Staged release or deployment-ready artifact | Existing release conditions met; state compatibility established |
| 7. Observe and learn | Runtime evidence, user/operator outcomes | Updated confidence, new cases, corrected knowledge | Required observation supports continued use or triggers recovery |

Stage 6 may end with a deployment-ready handoff when live execution is outside scope and the applicable pre-release gates are satisfied. If release evidence is missing, hand off the locally verified candidate with that release blocker; do not label it deployment-ready. Stage 7 cannot be inferred from local tests. Report the actual highest completed stage.

Build evidence is specific to the actual build variant. A deployment path that rebuilds from repository source can produce a different configuration than a previously evaluated published image, and a compile-time public flag baked into an artifact cannot be replaced by a runtime override for the same behavior. When a release path will actually build a variant that has no boot evidence, name that variant as an explicit remaining gate — built from the same recipe, booted with its default configuration, exercised for readiness and the affected journeys — rather than extending one variant's claim to all of them. Gates that require revision history, a complete repository layout, or tools absent from the runtime image need those prerequisites supplied in the environment where each gate actually runs; a setup failure there is environmental, and the remedy is provisioning in an evaluation-only environment or a documented override, not a product change or a weakened gate.

Stages are logical gates, not a requirement to finish every product specification before coding. Complete them for one slice, loop back when evidence invalidates an input, and repeat across the engagement. In a campaign, retain each slice's stage separately; one completed slice does not advance every unit.

Freeze the executable gate before delegating or generating the runtime candidate: record the gate's artifact identities together with its result against the unchanged baseline, so later failures are attributable to the candidate rather than to a moving standard. Oracle-debugging simulations and harness self-checks are disposable evaluation machinery, not acceptance runs or reconstruction evidence; label them that way in the record. An evaluator repaired after freezing restarts from the gate freeze, not from a revised candidate.

At evaluation time, establish that the runner is exercising the identified candidate: check resolved package/module paths, selected adapter, service endpoint, or artifact identity as appropriate. A stale build, reused container, cached import, or accidental fallback to the baseline can produce a green report about the wrong implementation.

Follow the consumed artifact beyond an entry point or re-export index. That file can remain identical while a transitive compiled module changes; bind the affected module and installed/workspace-linked copies to the executed build. Reconcile this closure at start and finish, especially when a producer and consumer share generated outputs.

For long or multi-stage runs, hold writes to accepted candidate and evaluation inputs, capture their identities at start and re-verify at finalize. An intervening input edit invalidates the combined claim even if assertions pass; a start-only manifest cannot establish that all stages used the same inputs.

### 14.2 Build the replacement work package

The implementer receives:

- Unit identity, objective, and explicit scope.
- Accepted requirements, definitions, and intentional acceptance delta.
- Architectural target and permitted freedoms.
- Public contracts and relevant neighbor contracts.
- State formats and migration constraints.
- Evaluation entry points, affected journey checks, and frozen acceptance criteria.
- Representative examples and supported workloads.
- Known incident lessons and rationale.
- Environment/build recipe, resource budgets, and provenance requirements.

The existing implementation may be available for ordinary translation and repair. For a clean reconstruction rehearsal, withhold it as described in Section 15. Disclose which context the implementer actually used.

### 14.3 Generate, diagnose, and repair within bounds

Prefer one coherent candidate over several speculative rewrites. Multiple candidates are useful when there is a real architectural or algorithmic tradeoff and an oracle capable of comparing them.

On failure, classify the cause:

```text
implementation defect → repair candidate
missing/contradictory intent → return to intent validation
incorrect or incomplete target → revise architecture deliberately
bad fixture / harness / adapter → repair evaluation machinery separately
unavailable environment → record blocked verification and restore prerequisites
excessive uncertainty or exhausted budget → retain baseline and produce handoff
```

Preserve failed counterexamples and attempt history. Do not silently expand the unit or change adjacent consumers to make a candidate pass. Either change requires an explicit scope or architecture decision and reconsideration of the affected gate and completion claim.

Record the phase where a run stopped. When a loader, dependency-resolution, tooling, or fixture-setup failure stops execution before a product phase, that phase remains unexercised; preserve any earlier observed checks while keeping whole-run acceptance failed. A later successful execution does not change the earlier failed verdict. Repair evaluation machinery as new versioned gate artifacts rather than editing executed inputs, then rerun under a fresh run identity while retaining the failed attempt, its diagnosis, and its resources. When evaluation code crosses a package's module-format boundary, adapt at an explicit evaluation-local interop boundary that leaves product packages, manifests and dependencies unchanged, and prove the adaptation with an expected-usage load control before paying for another full run.

Each repair should have a concrete diagnosis and expected observation. If successive failures expose unmodelled lifecycle or protocol responsibilities, revisit the boundary and evaluation design instead of accumulating local patches until examples happen to pass. Keep focused checks in the repair loop and reserve the combined gate for integrated inputs; duplicate full-suite runs by every writer add cost without independent evidence.

Separate capture/build from judgment when practical, and persist the producer's
receipt and raw output before running fallible post-processing. A downstream
oracle, probe-label or report-authoring failure does not itself invalidate the
completed producer stage. Repair the judgment in a separately identified artifact
and rejudge the retained output read-only when it contains the required observations;
keep the failed judgment and its cause. This is machinery diagnosis or rejudgment,
not a new product execution. Use a new judgment identity without rerunning the
producer solely to obtain a fresh all-green run label. Repeat the producer only
for changed relevant inputs, missing observations/artifacts, or a separately
declared new evidence claim; retain unaffected passed-stage receipts.

A producer receipt written before required retention/finalization is provisional
for whole-run acceptance even when its rows pass. If the wrapper subsequently
fails, retain its nonzero outcome and identify the failed phase. Repair the finalizer
in a separate artifact, verify the retained output against the original executed
candidate/gate identities, and issue a successor phase-specific receipt. Do not
replay the producer solely to clear the wrapper failure or rewrite its early
success as evidence that finalization originally completed.

A failed stage need not force replay of independent passed stages. Reuse their retained receipts only when relevant candidate, gate, dependency and environment assumptions still match the final inputs; rerun the failed or invalidated stages under fresh identities after diagnosis. Prove relevance against the complete executed path, including imported helpers, dynamic selection and state owners; an unchanged filename, convenient source prefix or isolated entry function alone is insufficient. A browser-only adapter correction can leave an independent transport gate reusable when its whole execution closure is demonstrably unchanged; otherwise rerun it. Launcher or setup changes may invalidate environmental claims without changing product-value expectations. Final acceptance still requires the complete reconciled stage set and actual process outcomes (Section 12.3), not whichever successful retry was most recent.

If the corrected, frozen gate accepts the unchanged implementation and no intended product-value assertion rejects it, retain that implementation. Verified evaluation/knowledge improvement is a useful translation outcome: record the added coverage and adapter limitations, without calling it a defect repair, replacement or reconstruction. An evaluator failure alone does not justify a runtime patch.

If synthesis fails, retain the working baseline. A type-correct stub may exist in an explicitly disabled development path, but it is not a successful replacement and must not masquerade as healthy functionality. A degraded runtime fallback is acceptable only when that behavior is part of the accepted product contract.

### 14.4 Selectively invalidate claims and artifacts

Maintain a small explicit dependency graph, for example:

```text
intent/definitions → contracts and architecture → implementation units
intent/contracts  → evaluations and documentation
implementation + build inputs → deployable artifact
artifact + harness + dataset + environment → evaluation result
deployed artifact + runtime evidence → operational claim
```

An edge means that a change may require revalidation, not necessarily that source code must be regenerated.

Use this procedure:

1. Classify the change as editorial, semantic, implementation, evaluation, or environment/evidence change.
2. Find directly affected records and traverse declared dependencies conservatively.
3. Mark affected claims/results stale and identify which checks must rerun.
4. Regenerate implementation only where obligations or constraints actually require it.
5. If a requirement disappears, inspect whether its implementation can be retired.
6. If the graph is incomplete, broaden verification and record the missing edge; do not assume independence.

Cache results only when all relevant input identities and environment assumptions match. A candidate digest alone cannot validate a cached result if the dataset, harness, provider, contract, or dependency version changed.

Start with explicit references and simple scripts. Automated semantic impact analysis is an aid whose conclusions need checking, not an excuse to omit conservative verification.

### 14.5 Integrate into the existing build and release path

Provide discoverable commands for the applicable operations using existing tooling:

```text
validate durable records and references
build / typecheck / lint
run unit and boundary evaluations
run state, workload, quality, and documentation evaluations
exercise replacement in isolation
produce an evidence manifest
verify retirement conditions
```

These are capabilities, not literal commands to invent in documentation. Record the exact real invocations. A thin script or task-runner entry is enough when it reduces ambiguity; avoid building an orchestration framework to wrap one test.

CI should distinguish deterministic checks, live integration checks, stochastic quality evaluations, and operational evidence. Choose the appropriate trigger and environment for each. Required gates must report failures, skips, and inconclusive outcomes honestly. Match CI runner configuration to the gates' actual prerequisites: a checkout depth, repository layout, or tool the gates need in development must exist in the runner too, or the suite fails there while passing locally — fix the environment declaration, and verify by inspecting the workflow job that runs the suite, not by assuming local success transfers.

Compose commits as atomic units: required source files land together so a fresh checkout can execute its declared workflows and follow supported recovery procedures. References to external or ignored execution receipts must be identified as retrievable evidence, not executable source dependencies. Never commit code that imports artifacts excluded by ignore rules; keep such session-owned machinery local until it is decoupled. Include the ignore rules that keep generated diagnostics and scratch outputs out of version control while their durable receipts are retained locally.

Protect evaluation and contract integrity through the repository's normal review controls. Pin test dependencies and evaluation inputs where meaningful. The generator's ability to edit files must not silently allow it to lower the release standard.

Review the integrated diff and resulting artifacts as well as the gate results. Look for newly hidden side effects, boundary violations, accidental dependencies, unexplained defaults, bypassed checks, and unrelated changes. Code review and executable evidence complement one another; generation does not eliminate the need to understand what will run.

### 14.6 Introduce immutable candidates with observable recovery

Build an identified artifact, introduce it using the product's existing deployment model, and preserve the last known-good artifact and compatible recovery inputs. Use canary, blue/green, package prerelease, shadow comparison, or staged local installation only when the product supports that method.

Shadow execution must isolate writes and external effects. Do not send the same payment, email, webhook, or state mutation twice merely to compare implementations. Use a recording sink, read-only replay, or a domain-appropriate reconciliation design.

Define rollout success and recovery triggers before exposure, including representative sample volume. Avoid expanding traffic because “nothing failed” during a period with no meaningful workload.

Establish the actual release trigger and operational ownership first. Git publication, package publication, artifact build and deployment are separate events unless the configured workflow connects them. A source-built deployment path does not by itself mean pushing auto-deploys; user-managed backup routines do not become agent-run operations merely because a release template mentions them. Use the identified deployed revision and transport for operational claims.

When the confirmed release model rebuilds and deploys repository source on push, that push is the production introduction and carries the same gates: explicit authorization, backup coverage through the owning procedures, and a defined post-deploy smoke over the transport the product requires — secure session attributes, for example, can demand HTTPS — including authentication, a representative write with reload/durability, and the changed capabilities' round-trips. Roll back by redeploying the prior accepted revisions; history rewriting is not a rollback procedure. Retain results and stop on any actual backup, build, or smoke failure.

---

## 15. Exercise replacement and reconstruction

### 15.1 Three distinct questions

**Absence probe:** disable or remove the unit in a disposable environment and observe what fails. This exposes hidden dependencies and evaluation blind spots.

**Substitution trial:** introduce an alternate implementation behind the same boundary and run the behavioral gate with the old implementation unavailable at runtime. Source-assisted development is allowed. This tests replacement of the implementation.

**Reconstruction rehearsal:** perform a substitution trial with an implementation created in a fresh context using only the survival pack and declared dependencies. This additionally tests whether the knowledge needed to reconstruct the behavior survives outside the old implementation.

None requires deleting the working source permanently. Use an isolated worktree, copy, sandbox, package substitution, or equivalent mechanism appropriate to the repository. Preserve the baseline and relevant state fixtures.

A worktree isolates source files, not databases, queues, ports, credentials, or external effects. Provision the mutable resources the rehearsal actually uses, select explicit test configuration, and confirm the target before destructive probes. Use synthetic identities and uniquely scoped fixtures; route external effects to designated sinks. Cleanup must remove only resources created for this run. If a real dependency is unavailable, declare the substitute and the evidence limitation.

Bind claimed operations and comparisons to stable resource identities. Re-resolve and validate ephemeral locators immediately before use, especially after restart; a cached address can now point to another resource. If shared infrastructure is broken, provision a private instance where permitted rather than performing destructive global repair outside your ownership. Environmental adapters must be explicit and fail-closed, with the omitted behavior named as a claim limit; an alternate transport is not evidence for the default discovery path. Track diagnostic scratch retained after failure and reclaim it when its retention purpose ends, preserving the durable evidence first.

A privately named engine can still share host supervisors, sockets, runtime namespaces, storage locks or network helpers. Map those dependencies and their permitted effects before calling it isolated; owned paths do not grant authority to repair or stop another owner's services. Verify service-name discovery separately from direct-address transport, using actual name resolution and the expected service response when that claim matters. Successful private DNS proves neither whole-stack recovery nor OS-level egress confinement; retain shared-manager interactions and uncertainty from failed setup attempts in the resource receipt.

### 15.2 Define the survival pack precisely

Keep a small manifest of actual paths or versioned references. It must distinguish:

- **Removed implementation:** source, private helpers, generated bundles, and old runtime artifacts included in the replacement unit.
- **Surviving knowledge:** accepted intent and delta, boundary and neighbor contracts, evaluation cases and oracles, state formats, rationale, and supported examples.
- **Surviving execution support:** harness and adapters, build/configuration recipes, dependency identities, generators, fixtures, and setup/cleanup commands.
- **Declared dependencies:** retained libraries, neighboring components, services, and test substitutes, including what they provide and what is outside the claim.
- **Access and identity:** which inputs the implementer and evaluator can access, their versions, the chosen environment, and how candidate identity is confirmed.

Check that the pack can build and evaluate a substitute without importing the removed tree. Tests stored beside the old source may survive if explicitly retained and implementation-independent. Generated contracts whose generator lives in that tree need an independent source or a preserved authoritative artifact. If a retained helper still implements the capability, either include it in the removal scope or narrow the replacement claim.

Check the exact input set, declaration origins, and resolution paths. The evaluator must know which contract/gate/configuration it accepted; dependency-lock identity is a declaration, not proof of the installed environment. When retained neighbors are copied for integration, exclude emitted or cached variants of the removed unit as well as its source, and verify the selected runtime candidate's identity. Keep writer/access restrictions and their limits explicit rather than treating a path-name check or source-pattern scan as a sandbox.

For reconstruction, exclude alternate copies of the old implementation in repository history, build caches, installed packages, source maps, previous session context, and task transcripts. Prefer an allowlisted export into a fresh workspace over an instruction to ignore readable source. Required algorithms and domain rules belong in the pack when they are genuinely part of the contract; relabeling a source dump as a specification does not test knowledge extraction. Record actual access limits and any exceptions.

### 15.3 Rehearsal procedure

1. **Select a bounded unit.** State what is removed, what remains, and which behaviors are in scope.
2. **Freeze the survival pack.** Verify the manifest in Section 15.2. The knowledge and execution support must survive removal of the implementation.
3. **Establish the reference.** Run the relevant gate against the current implementation and record known defects and intentional future differences.
4. **Probe observability.** Demonstrate that absence or a meaningful broken substitute is detected where expected.
5. **Choose the input context.** For reconstruction, use a fresh agent/session with only the declared survival pack and dependencies. For source-assisted substitution, disclose access to the old implementation; fresh context is unnecessary.
6. **Build the substitute.** Record every request for missing knowledge. A question requiring the old implementation identifies a gap in the survival pack.
7. **Evaluate substitution.** Run the frozen gate for preserved promises and any previously accepted delta, plus state compatibility, supported consumers, and affected integrated journeys. Verify execution with the old runtime implementation unavailable.
8. **Inspect the system.** Look for unexpected imports, ownership violations, runtime consumers, operational differences, and hidden state assumptions.
9. **Recover the environment.** Restore the baseline when the experiment ends unless the evaluated candidate is intentionally adopted through the release path.
10. **Harvest the findings.** Add missing intent, contracts, regressions, dependency edges, and rationale. Repeat only when the next run tests a newly resolved obstacle.

A fresh context cannot prove that a model has never encountered similar code; the useful constraint is that the local implementation is excluded from the supplied reconstruction inputs. State that constraint accurately.

Verify that the replacement does not transitively import, invoke, or fall back to the removed implementation. Check the actual loaded artifact and run the claimed gate with the old implementation unavailable. Surviving public contracts and unrelated dependencies may remain; the replaceable implementation must not secretly remain the oracle or execution path.

If the original source was needed to fill a gap, repair the durable pack and use a new reconstruction attempt to test that the gap is actually closed. Do not retroactively label a source-assisted port as source-independent regeneration.

### 15.4 Acceptance and interpretation

A **demonstrated local replacement** preserves the supported boundary and its selected obligations without requiring changes to consumers. The candidate must pass the required gate, preserve applicable state and effects, and run independently of the removed implementation. Report the artifact identities, context used, known defects, and environment limitations.

A **demonstrated reconstruction** additionally requires a fresh reconstruction context using the frozen survival pack without access to the old implementation, followed by the same substitution evidence. Source-assisted replacement can be valuable without proving reconstruction. If fresh context or input separation is unavailable, mark reconstruction unestablished.

A change that requires consumers to migrate establishes migration evidence, not independent replaceability across the old boundary—even when every migration was planned. If a compatibility adapter preserves that boundary, include the adapter in the evaluated replacement unit, account for its state and effects, and give it an explicit lifecycle. After an architectural migration, evaluate future replaceability against the newly established boundary.

A stable boundary adapter can be part of the enduring design. Give temporary compatibility machinery an exit condition; do not remove a useful permanent abstraction just because it is called an adapter. Identify which role it serves.

Report the obstacles discovered: undeclared consumers, missing behavior, weak evaluations, unavailable environments, state incompatibility, or excessive coordination. These are the useful output of a failed rehearsal.

Preserve the final reusable packet and accepted experiment source by durable reference, with the source-context record and failed attempts that explain it. Replaying that source establishes another substitution result, not another fresh reconstruction. A successful rehearsal of one unit does not upgrade the readiness of neighboring or merely inventoried capabilities.

Do not turn rehearsal results into an architectural beauty score. Record time, coordination, and knowledge gaps when they help choose the next investment. A successful rehearsal establishes evidence for this boundary, version, and environment; new dependencies can invalidate it later.

---

## 16. Retire implementations and compact the system

### 16.1 Run removal alongside generation

Replacement has two unfinished jobs after a candidate works:

1. Establish that the old runtime path can disappear.
2. Reduce the temporary complexity introduced during replacement.

A requirement removed from the product may justify deletion without any new implementation. Conversely, a successful new implementation may still need a compatibility period. Track both cases explicitly.

### 16.2 Retire with evidence

Use this lifecycle:

```text
introduce compatibility → migrate consumers → verify isolation
→ remove old runtime path → observe → compact
```

Before removing a supported path, establish the applicable:

- Static consumers migrated or isolated.
- Runtime consumers observed with adequate visibility and representative coverage.
- Scheduled, infrequent, offline, and external consumers accounted for.
- State migration and recovery compatibility verified.
- Operational tools, dashboards, scripts, and support workflows updated.
- Documentation, SDKs, tool schemas, and examples aligned with supported versions.
- Recovery artifact and trigger available for the agreed window.

No observed traffic is insufficient if telemetry does not cover the path or the observation period misses its lifecycle. A deletion experiment can expose a dependency; it cannot prove that unobserved external users do not exist.

### 16.3 Give temporary complexity an exit

For each temporary mechanism—compatibility layer, migration flag, dual path, transitional schema, or fallback—record:

```text
reason + owner/steward + consumers + exit condition
       + evidence query + review trigger/date + deletion dependencies
```

A date is a review trigger, not proof that removal is safe. If retirement is delayed, record the remaining consumer or uncertainty and the next action. Keep supported compatibility deliberately rather than leaving it as forgotten sediment.

### 16.4 Compact across all surfaces

Look for:

- Redundant interfaces, deprecated fields, duplicate commands, and overlapping concepts.
- Obsolete flags, aliases, defaults, environment variables, and configuration branches.
- Unused dependencies, queues, subscriptions, scheduled jobs, and deployment components.
- Duplicate implementation paths and unnecessary abstractions.
- Outdated docs, conflicting examples, and duplicated specifications.
- Brittle implementation-specific tests that no longer defend useful behavior.
- Evaluation cases that are redundant without adding a distinct failure class or distribution slice.

Preserve behavioral coverage and rationale when merging tests. Preserve lineage when archiving superseded records. Retiring active implementation does not require erasing version-control history, evidence, migration knowledge, or rollback artifacts still needed under the retention policy.

Include newly introduced knowledge and evaluation tooling in this review. Prefer the project's runner and one small receipt/adapter over a parallel platform. Keep useful per-unit packets; do not generate dossiers or exporters for every function. Retained experiment artifacts should be clearly historical inputs, not accidental second production implementations.

### 16.5 Judge comprehensibility

Compare the before and after explanation:

- How many concepts must a newcomer understand to make the next change?
- How many supported paths and configuration choices exist, and why?
- How many components must coordinate for one capability change?
- Can the current authoritative contract be found quickly?
- Has behavior become more explicit, or merely moved behind a generic wrapper?

Fewer lines, services, or documents are not sufficient. Compaction succeeds when the product remains correct and the mental model becomes simpler. A necessary compatibility path may remain; its purpose and end conditions must be clear.

---

## 17. Keep the feedback loop operating

### 17.1 Convert experience into durable knowledge

For an incident, regression, customer report, or discovered edge case:

1. Preserve a minimal reproduction or relevant observation.
2. Identify the violated or missing claim.
3. Correct implementation or operational behavior as needed.
4. Add or clarify intent and a durable evaluation.
5. Record why the solution exists and alternatives that matter.
6. Link the fix, evidence, contract, and result.

A patch is not fully harvested until the next regeneration would preserve the lesson. Keep the loop proportionate: a short requirement note and focused regression case may be enough.

### 17.2 Manage drift without discarding fixes

Compare accepted artifacts, implementation, deployed configuration, and runtime claims. Treat an unplanned implementation edit as a divergence to understand, not automatically as code to overwrite.

Classify it as:

- **Harvest:** a valid fix that must become durable intent, architecture, or evaluation knowledge.
- **Accepted exception:** an intentional divergence with an owner, reason, scope, and review trigger.
- **Temporary patch:** an emergency measure with explicit expiry/review and a durable follow-up.
- **Defect or unauthorized drift:** a change requiring correction through the project's established process.

Do not regenerate over an unharvested fix. Do not assume a provenance manifest proves the running system still matches it.

### 17.3 Use triggers appropriate to the layer

Review affected units after meaningful changes to:

- Requirements, contracts, data formats, or consumer inventory.
- Runtime/framework/provider versions and dependency behavior.
- Load, cost, error distribution, or resource constraints.
- Important user journeys, operational workflows, or support evidence.
- Expiring compatibility periods, exceptions, and temporary patches.

Schedule periodic deletion and compaction reviews where change rate and consequence justify them. Avoid a universal calendar ritual for every low-stakes module.

An observed regression initiates diagnosis, not automatic rewriting of intent or generation of another implementation. Distinguish a workload shift, broken instrumentation, dependency outage, stale configuration, and a candidate defect. A single symptom rarely identifies its cause: a constraint violation does not reveal which write path produced it, and a healthy status does not prove which version is running or that work completed. Diagnose from the failing operation, active version, code path, and event order, and name discriminating checks between candidate explanations before acting on any of them. Stabilize or recover first where necessary, then update the smallest justified set of claims and artifacts. Deduplicate repeated signals into the same investigation so the feedback loop does not create change churn.

### 17.4 Track operationally useful measures

Choose measures that answer a real decision:

- Time and coordination needed for a particular replacement.
- Specific knowledge gaps discovered by rehearsals.
- Escaped behavioral regressions and time to detect/recover.
- Critical obligations with usable evaluations and current evidence.
- Unharvested incident fixes and unresolved contract contradictions.
- Temporary paths awaiting retirement, with their actual blockers.
- Product quality, latency, reliability, cost, and user outcomes before/after a slice.

Always preserve definitions, denominators, scope, and evidence. Avoid a single “regenerative score,” targets for lines deleted, or incentives to generate more replacements. The point is cheaper, more reliable useful change.

### 17.5 Test the next useful change

Return to the benefit hypothesis from Section 2.4. Use the next real in-scope change to test it; for a dedicated assessment, trace a representative change without implementing an unrequested feature. A traced scenario is a prediction, not demonstrated changeability.

Start from the local index and unit packet. Record the missing knowledge, boundaries and consumers touched, coordination required, checks run, and temporary machinery introduced. Compare this with the original obstacle and any comparable baseline. Include verification and documentation upkeep in the cost; fast code generation alone is not the result.

If the change still requires rediscovering unrelated internals, coordinating every unit, or rewriting the evaluator, revise the seam or packet before scaling the method. If the new artifacts cost more to maintain than the demonstrated benefit, consolidate them or retain ordinary maintenance. A successful pilot justifies the next valuable investment, not a repository-wide template rollout.

### 17.6 Improve the method without importing a repository into it

When reviewing an application of this guide:

1. Compare the promised outcomes with surviving contracts, actual assertions, integration/reconstruction evidence, and the next session's instruction entry point. Review all seven primitives; do not substitute the number of tests or documents for that review.
2. Classify each shortcoming: **missing or ambiguous guidance**, **existing guidance not followed**, **a mistaken product/evaluation assumption**, or **a real environment/authority limit**. More prose is not the remedy for every execution failure.
3. Repair the concrete local gap and retain its counterexample. Check whether the proposed lesson would help a different product, stack and harness.
4. Amend the relevant rule, execution checkpoint or template with the smallest transferable instruction. Consolidate overlapping guidance; do not append repository names, private paths, incident transcripts, tool-specific defaults or a history of products where the method was used.
5. Version the guide, update the repository's actual operating instructions and affected local packet, and validate a representative next task through the loading path in Section 11.3. A portable-method edit is incomplete if the repository's root/scoped session guidance still teaches the superseded practice. State which benefits were demonstrated and which remain hypotheses.

The portable method carries general decisions and verification rules. Product facts and execution history belong in the product's own records.

---

## 18. Adapt to different repository and product types

Use the same principles with different boundaries and evidence. Select applicable techniques rather than imposing a web-service architecture.

| Product type | Durable boundaries | Particularly important evaluations |
|---|---|---|
| Library or SDK | Public symbols, types, exceptions, packaging, serialization | Consumer compilation/runtime checks, version combinations, old payloads |
| CLI | Arguments, stdout/stderr, exit codes, environment, filesystem effects | Shell/pipeline use, malformed input, cancellation, locale, stable output formats |
| Web UI or desktop app | User tasks, navigation, stored state, accessibility, visible failure behavior | Journey tests, keyboard/assistive interaction, reload/recovery, supported platforms |
| Service or modular monolith | Capability contracts, data ownership, events, failure containment | Contract, concurrency, state, performance, recovery and consumer checks |
| Data pipeline | Schema meaning, lineage, checkpoints, quality, idempotency | Replay, late data, backfill, partial processing, reconciliation |
| Agent/model product | Tool authority, task outcomes, state, budgets, quality criteria | Hard invariants plus calibrated statistical and task-completion evaluations |
| Plugin or extension | Host lifecycle, compatibility, permissions, persisted settings | Supported host versions, enable/disable/upgrade, extension isolation |
| Infrastructure repository | Declared resources, identity, state, operational procedures | Plan validation, disposable environment application, recovery, policy and drift checks |
| Documentation or skills product | Accurate tasks, schemas, examples, discoverability, versions | Fresh-user/agent task success, link/build checks, executable examples |
| Embedded or constrained system | Hardware interfaces, timing, memory, persistent formats | Simulation/hardware-in-loop, deadlines, resource bounds, recovery/update behavior |

### 18.1 Multi-repository products

Treat the user journey as the scope of understanding even when modification is limited to one repository.

- Map producers and consumers across repositories, packages, deployed versions, and documentation sites.
- Identify a canonical location for each shared contract; distribute versioned references rather than divergent copies.
- Check that companion fixtures describe the same identities and values at the consumers' actual public field paths; individually passing fixtures can still describe incompatible worlds.
- Record the revision/release set used in integration tests.
- Maintain a supported producer/consumer compatibility matrix.
- Test independent release combinations that users can actually run.
- Plan contract evolution so deployments do not require an accidental synchronized cutover.
- Track external follow-up changes explicitly when a repository is unavailable or out of scope.

When three repositories form an API, client, and integration-docs product, all three are consumers or producers of behavioral promises. Replacing an API implementation while leaving a broken SDK example is an incomplete product change.

### 18.2 Legacy systems with weak boundaries

Begin with observation and characterization. Add a small external harness, preserve important incidents, and route an existing entry point through an explicit seam. Use gradual replacement when it reduces risk.

A thin facade does not itself remove shared-state or timing coupling. Name remaining coupling honestly. If isolation is too expensive, strengthen the durable knowledge and postpone replacement rather than declaring success from a new interface file.

### 18.3 Small or resource-constrained projects

Use the minimal viable set:

- A short purpose/invariants section in existing docs.
- One meaningful boundary and ownership description.
- A few valuable behavioral checks, including the highest-cost failure.
- A reproducible build/test command and concise decision note.
- A concrete recovery and removal procedure appropriate to the project.

Add structure when real work needs it. A small repository should not need to maintain an enterprise specification system to benefit from replaceability.

---

## 19. Use the implementation templates

Use applicable fields in existing artifacts or a compact local record. Explain omissions that limit a claimed result; unresolved fields remain explicit. Example IDs and paths are placeholders, not product facts.

### 19.1 Minimum slice record and checkpoint

Use this as the default. Link existing records instead of copying their content.

```text
Why: <objective, scope, mode, expected benefit; completion criterion>
Promises: <canonical obligations; preserved behavior and accepted delta>
Boundary: <unit, owned state/effects, consumers, permitted changes>
Verify: <obligation-to-assertion/oracle links; baseline; required stage; commands/environment>
Result: <candidate and gate identities; process/report outcome; case counts; context; limitations>
Next: <current stage, exact action, blocker/decision, retirement condition>
```

Identify the base revision and relevant local changes, applicable instructions, execution limits, and canonical artifact index in the existing engagement record or beside this packet. A campaign additionally needs the capability inventory and per-slice status. Instruction adoption needs the root/scoped ownership and load map with observed versus assumed delivery and a fresh-session result (Sections 3.2 and 11.3). A replacement needs the survival-pack manifest. Expand into the following templates only where those details are useful; the six lines are a navigation aid, not a limit on necessary evidence.

For a unit expected to be rewritten, add or link its public declaration, surviving dependencies/state formats, actual gate and recovery procedure (Sections 3.3 and 15.2). At campaign handoff, identify where the Section 3.2 operating contract is installed and any restriction on its use.

### 19.2 Evidence-backed finding

```text
Finding ID:
Claim:
Classification: observed / required / historical / inferred / unknown
Source: file and symbol, document version, incident, or evidence artifact
Observed version/environment/date:
Supporting reproduction or query:
Affected capability, consumer, and failure outcome:
Contradictory evidence:
Decision needed:
Next action:
```

### 19.3 Intent record

```yaml
id: INV-001
version: 1
type: invariant
status: proposed  # proposed / accepted / disputed / superseded
scope: "<capability and conditions>"
statement: "<implementation-independent obligation>"
reason: "<why this matters>"
definitions: ["<stable definition IDs>"]
evidence: ["<retrievable references>"]
authority: "<established product decision or responsible role>"
contracts: ["<contract IDs>"]
evaluations: ["<evaluation IDs>"]
operational_signals: ["<signals or not-applicable reason>"]
supersedes: []
open_questions: []
```

### 19.4 Replacement-unit dossier

```text
Unit ID / name:
Capability, benefit hypothesis, and likely next change:
Implementation/removal scope and survival-pack manifest:
Canonical intent, contracts, versions, and accepted delta:
Consumers, dependency direction, state/effect ownership, remaining coupling:
Runtime constraints, implementation freedoms, pace, and change-specific risk:
Work package, baseline, gate/commands, integrated journeys, and blind spots:
State compatibility, introduction, recovery, and observation plan:
Retirement/compaction conditions and permanent adapters:
Evidence: verification, substitution, reconstruction, operation, change cost:
Uncertainty, steward, and next action:
```

### 19.5 Behavioral evaluation specification

```yaml
id: EVAL-001
requirements: [INV-001]
boundary: "<public behavior under test>"
oracle: "<accepted rule, property, or reviewed reference>"
scope: "<versions, population, and scenarios>"
gate_role: required  # required / advisory; declared before comparison
required_for: ["<local-verification/release/operation/retirement as applicable>"]
inputs:
  cases_or_workload: "<path/version/digest>"
  setup: "<isolated fixture and prerequisites>"
  implementation_adapter: "<reference>"
observations: ["<outputs, visible state, effects, timing>"]
assertions: ["<implementation-independent claims>"]
comparison_rule: "<exact/property/tolerance; justified normalization>"
acceptance: "<decision rule, including uncertainty if applicable>"
negative_control: "<plausible violation, intended assertion, and confirmed rejection cause>"
command: "<actual command>"
working_directory: "<actual directory>"
cleanup: "<procedure>"
limitations: ["<what this does not establish>"]
```

### 19.6 Decision and generation record

```yaml
change_id: CHANGE-001
reason: "<intent/evidence driving the change>"
change_kind: implementation  # behavior / architecture / evaluation / docs
base_revision: "<identity>"
accepted_inputs:
  intent: "<versions/digests>"
  architecture: "<version/digest>"
  contracts: ["<versions/digests>"]
  acceptance_delta: "<accepted differences or none>"
  evaluations: "<harness, adapter, dataset, acceptance versions>"
  build_environment: "<lockfiles, runtime, configuration references>"
generation:
  agent_and_tools: "<known identities or unknown>"
  model: "<known model/revision or unknown>"
  prompt_or_work_package: "<versioned reference>"
  implementation_context: "<old source available or withheld>"
  survival_pack: "<manifest reference for replacement/reconstruction; otherwise not applicable>"
  attempts: ["<attempt records, including failures>"]
decision:
  chosen_approach: "<brief rationale>"
  alternatives_rejected: ["<alternative and reason>"]
  owner_or_review: "<actual decision/review record>"
candidate: "<revision and artifact digest>"
verification:
  - evaluation: "<ID and version>"
    candidate: "<final integrated revision/artifact identity>"
    command: "<actual invocation>"
    working_directory: "<directory>"
    environment: "<fixture/configuration identities; no secret values>"
    result: not-run  # passed / failed / inconclusive / blocked / not-run
    evidence: "<retrievable report with selected, passed, failed, skipped cases>"
deployment: "<not deployed / identified release and environment>"
observation: "<not observed / evidence reference>"
retirement: "<pending conditions or completed evidence>"
uncertainty: []
```

### 19.7 Retirement record

```text
Old path / contract / configuration being retired:
Why it is no longer needed:
Replacement, if any:
Known consumer inventory and supported lifecycle:
Static and runtime isolation evidence:
Infrequent/offline/external consumer treatment:
State compatibility and retained recovery artifacts:
Observation duration and representative volume:
Remaining blocker / responsible steward / next review trigger:
Removal operation:
Verification after removal:
Documentation and operational cleanup:
Compaction result:
```

### 19.8 Change-review checklist

Apply to the actual scope and claim. This summarizes the review; it does not create extra gates for inapplicable operations.

```text
[ ] Scope, benefit, and intentional changes to promises are explicit.
[ ] All seven primitives have proportionate decisions/evidence; actual session instructions carry them.
[ ] Canonical knowledge is navigable and survives the claimed removal scope.
[ ] Boundary, consumers, state, effects, and affected journeys are accounted for.
[ ] Gate has justified oracles; candidate did not quietly redefine acceptance.
[ ] Evaluations reject meaningful failures; redundant/trivial checks and new tooling earn their cost.
[ ] Required checks pass on this integrated candidate; any controls fail for the intended reason.
[ ] Applicable compatibility, recovery, observation, and retirement evidence is present.
[ ] Docs, commands, artifact identities, results, limitations, and next action are recorded.
[ ] Actual claims distinguish verification, replacement, reconstruction, migration, and operation.
```

---

## 20. Walk through a worked example

This hypothetical example illustrates the method for a multi-repository knowledge product. Its names, protocol, and obligations are illustrative; discover the actual contract before applying it elsewhere.

### 20.1 Situation

A product has:

- A service that answers questions and streams results.
- A client application that renders answers and persists conversation state.
- An SDK and agent-facing documentation used by external integrations.

The client stream reader is difficult to change. Its documentation says to stop when the answer is complete, but operational notes indicate that a state-update event can arrive later.

The useful first slice is the **stream-consumption boundary**, not replacement of the entire model pipeline.

The benefit hypothesis is that changing framing or handling an allowed protocol extension will become a local reader change with reusable checks, rather than repeated investigation of UI rendering, server state meaning, and application persistence. Validate that hypothesis on the next relevant change.

### 20.2 Extract the durable knowledge

Suppose investigation establishes these accepted requirements:

```text
INV-STREAM-01
Completing the visible answer must not discard a later conversation-state
update that belongs to the same response.

INV-STREAM-02
Conversation state is opaque to this client: preserve the received value and
replay it according to the protocol rather than reconstructing its internals.

INV-STREAM-03
A canceled or failed response must not be reported as fully persisted success.

REQ-STREAM-04
The visible answer can finalize before auxiliary state work finishes, while
the product separately represents whether that state has become durable.
```

The architectural target gives the stream reader responsibility for framing and protocol events. The application owns persistence. The server owns state meaning. Record precisely how errors and cancellation cross those boundaries.

Characterize the real application path before routing the existing reader through that boundary. Preserve its public event data; keep server-state internals out of the reader. If the current reader incorrectly stops early, record an acceptance delta correcting that defect while preserving supported framing and consumer behavior.

### 20.3 Define the gate before replacing the reader

Build transport-independent byte-stream fixtures covering:

| Case | Required observation |
|---|---|
| Answer completion, then state update, then EOF | Answer finalizes; reader continues; latest state is persisted |
| State update before answer completion | Supported older ordering remains correct |
| JSON and UTF-8 split across arbitrary network chunks | Complete events and text reconstructed without corruption |
| Several frames in one chunk, heartbeats, permitted extensions | Valid data retained; framing and extension policy honored |
| Completion followed by unexpected disconnect before required state | Defined partial/degraded outcome rather than false durable success |
| Cancellation during the response | Work and persistence follow the accepted cancellation policy |
| A new request starts before an old response finishes | Response identity prevents stale updates from corrupting the new conversation |
| Unauthorized or unavailable upstream | Correctly classified error; no protected data or forged success |

A deliberate mutant that stops at answer completion must still build and parse the earlier frames, then fail the first case's missing-state-persistence assertion. An import error or disconnected test server would not demonstrate that the gate catches this lesson.

### 20.4 Replace and verify

First repair the contract and contradictory examples. Freeze the accepted event semantics and gate. Implement the smallest justified solution: a patch may fix early termination, while a replacement may be warranted if the reader cannot cleanly represent the lifecycle. For a replacement, use the same client boundary and run the frozen fixtures against both implementations.

Inspect differences: the current reader may fail a case, establishing a documented defect rather than a regression in the replacement. Add a real integration journey that streams an answer, stores the later state, reloads the conversation, and verifies continuity. Fakes alone cannot establish database persistence or proxy behavior.

Update SDK examples and agent documentation to teach the actual lifecycle. Evaluate a representative integration built from those docs. Record version compatibility and remaining external-client uncertainty.

### 20.5 Rehearse, retire, and learn

When testing reconstruction, give a fresh implementer a manifest-backed pack containing the protocol, fixtures, public client interface, and build instructions in a workspace without the old reader or its history. Retain the real application as a declared consumer. Evaluate the substitute in isolation and through that consumer. Missing framing or cancellation semantics become explicit gaps to repair.

Once an accepted replacement is introduced and observed under the relevant lifecycle, remove the obsolete reader and temporary routing flag. Preserve the regression fixtures and decision record.

The verified fix preserves the later state update, makes the protocol explicit, supplies reusable evaluations, and corrects integration guidance. A successful reconstruction additionally demonstrates that a fresh implementer can replace the reader from the surviving knowledge. Report which work was actually performed.

### 20.6 Verify that evolution became easier

Suppose the next requested change adds an optional progress event that may arrive between answer completion and the state update. Confirm the extension policy and supported old-client behavior first; a new event is not automatically compatible.

Update the protocol's acceptance delta and examples, implement the reader handling, and extend the fixtures. Preserve the assertion that the later state update reaches persistence. Exercise the application journey to detect an accidental interaction with completion. Record whether application persistence or server-state internals had to change, and why.

If the extension fits the predicted boundary and the packet supplied the required knowledge, this is evidence for the benefit hypothesis. If it requires unrelated redesign, use that result to correct the boundary or the hypothesis. The first successful replacement alone could not answer this question.

---

## 21. Recognize failure modes

| Symptom | What went wrong | Corrective action |
|---|---|---|
| A large generated rewrite passes shallow tests | Production knowledge and boundary obligations were never extracted | Narrow the unit, recover behavior, strengthen the oracle before replacement |
| Agents create a documentation tree before making a useful change | Templates became deliverables rather than support for the work | Use the minimum packet, reuse canonical records, and complete one outcome-driven slice |
| Every helper has a contract but one feature change touches dozens of them | Decomposition optimized generation size rather than independent action | Recombine around capability, ownership, and invariants |
| A clean interface still permits private imports and shared writes | The seam changed appearances without changing dependencies | Route existing callers through it, establish mutation ownership, and enforce actual dependency rules |
| Each component passes while the user journey fails | Composition assumptions or shared budgets were never checked | Reconcile producer/consumer guarantees and evaluate the affected integrated path |
| A legitimate feature is blocked by preservation tests | Existing and intentionally changed promises were conflated | Record the acceptance delta, supported version combinations, and specific expected baseline differences |
| A coordinated migration is reported as independent replacement | Consumer changes were hidden inside the completion claim | Report migration evidence separately; demonstrate replacement behind an unchanged supported boundary |
| A reconstruction passes by reusing knowledge from the old source | Substitution and independent reconstruction were conflated | Record the actual input context and run a fresh survival-pack-only rehearsal |
| A negative control fails on an import error | The gate detected broken wiring, not the intended behavioral defect | Use a shape-valid mutant and confirm the intended assertion rejects it |
| Separate agents pass tests but the merged product fails | Evidence described separate candidates | Re-evaluate affected obligations on the final integrated artifact |
| All tests pass because auth, storage, and providers are mocked | Verification removed the important behavior | Add boundary/integration evidence for the assumptions the mocks hide |
| Old and new agree on the same bug | Differential equivalence was mistaken for correctness | Add an independent requirement or domain oracle |
| Expected results call the candidate's own decision helper | The oracle shares the implementation defect | Use independent properties, reference vectors, or reviewed expected results |
| The survival pack cannot build without the deleted source | Build, generation, or harness dependencies were omitted | Make surviving execution support explicit and validate the pack's dependency closure |
| The candidate becomes green after snapshots and thresholds change | The implementation changed its own standard | Separate and review the acceptance change, preserve failure evidence, rerun |
| Re-reading unchanged requirements churns all unit IDs | Canonical meaning was delegated to fresh probabilistic interpretation | Persist stable IDs and review structured semantic patches |
| Every doc edit triggers repository-wide regeneration | Invalidation confuses byte changes with changed obligations | Classify changes and traverse explicit dependencies |
| Replacing a small dependency invalidates all stored sessions | Persisted semantics were a hidden slow layer | Capture serialization and state-version contracts; rehearse compatibility |
| Canary appears healthy with negligible traffic | Absence of evidence was reported as confidence | Require representative workload and an explicit unknown/inconclusive state |
| Shadow testing duplicates external effects | Comparison ignored side-effect ownership | Use isolated sinks, read-only replay, or a domain-aware comparison strategy |
| A model quality average hides a broken tenant boundary | Soft quality and hard constraints were blended | Enforce hard invariants separately and inspect important subgroups |
| Published skills or SDK examples lag behind the server | Documentation was treated as a summary rather than a consumer interface | Validate versioned examples and coordinate the affected publication surfaces |
| The old path remains indefinitely after every replacement | Generation has no retirement counterpart | Track consumers, exit conditions, observation, and compaction work |
| Compaction deletes the explanation for a strange rule | Simplicity was measured by removal rather than retained understanding | Preserve rationale and behavior while simplifying active machinery |
| The next regeneration reintroduces an incident | An emergency patch was never harvested upstream | Add the missing intent and regression, then reconcile the patch |
| Agents produce incompatible definitions in parallel | Shared contracts lacked a writer and reconciliation step | Freeze common inputs and serialize ownership/contract decisions |
| Months of architecture work ship no useful improvement | Replaceability became the objective instead of useful change | Select a smaller product-driven slice and reassess the economics |
| Replacement works, but the next ordinary change is harder | New boundaries and knowledge upkeep cost more than they save | Test the benefit hypothesis, merge unnecessary abstractions, and reduce process overhead |
| A new session ignores the method despite a thorough campaign report | Only a pointer or historical summary reached the actual agent instructions | Install concise actions for all seven primitives and exercise a fresh-session task |
| Root work follows the method but subtree or delegated work loses it | The instruction-loading path was assumed; a nearer file, inert link or missing import bypassed the canonical rules | Inspect that entry path, repair its minimal adapter/read fallback, and distinguish observed loading from explicit-read tests |
| Every session starts with a growing campaign transcript | Instruction hierarchy became an evidence archive; unconditional imports hid its size | Keep the shared loop in the root, local constraints in scoped guides and history behind the index; measure expanded context and retire duplicate rules |
| Hundreds of tests pass but an important interaction has no oracle | Evaluation volume displaced obligation and composition coverage | Inspect exact assertions and add the missing consumer/lifecycle case; remove tautologies |
| A wrapper reports success despite a failed cleanup or nonzero test-run exit | Only assertion counts were inspected | Honor process and structured report outcomes, including suite/runtime failures |
| A copied artifact is called a new reconstruction | Behavioral acceptance and source-context evidence were conflated | Record identity matches separately; require the actual fresh-context/input record |
| A repair grows into an unplanned subsystem | New architectural responsibilities were discovered only after generation | Revisit target, compatibility gate and total cost before continuing repairs |
| A constraint error is blamed on the first plausible cause, or a healthy status is read as proof of version or completion | Diagnosis substituted a story for the failing operation, version, path, and event order | Name discriminating checks between candidate explanations before acting |
| A restore is declared complete because the command and service succeeded | Completeness was never checked at record, identity, and byte level in the fresh target | Compare all owned resources, reject deliberately incomplete copies, and retain the original snapshot |
| Restored storage matches but users cannot log in or read linked content | Storage acceptance was mistaken for consumer recovery | Boot real consumers on isolated restored-state copies, enforce precise startup deltas and exercise supported journeys |
| A claimed operation or comparison targets the wrong store after restart | An ephemeral locator was treated as stable identity | Re-resolve and validate the locator against the run-owned resource immediately before use |
| Durable history stays intact but late state is missing | An incidental constraint rejected a misdirected write; final-state checks missed the attempted effect and lost update | Inspect outgoing effects and acknowledgments, then require exact origin-state retention |
| A browser run prints errors but exits green | Error collectors replaced failure semantics with diagnostics | Require all-context runtime-error checks, complete selection and fail-closed final verdicts |
| A subscription reopens but the view remains stale | Transport readiness was mistaken for missed-state recovery, or an active overlay blocked settled-state adoption | Verify catch-up separately, including repeated recovery during an unfinished operation and fresh-base/live-overlay composition |
| A late origin save is correct but feedback stores a mixed snapshot | Persistence ownership and selected view/recall ownership diverged | Select matching history, opaque recall and immutable request state together; verify a non-repairing consumer's acknowledged result |
| A metadata check passes without a new write | The predicate matched preexisting state, or an already-selected control intentionally did nothing | Trigger a supported changed value; require its new attempt, acknowledgment and selected durable effect; evaluate intended no-op semantics separately |
| A no-refetch gate rejects a correct consumer after its write | A global counter charged legitimate peer adoption to the writer | Scope observations to the consuming actor/resource/phase and retain peer effects separately |
| A redo is declared ready but release fails | A released predecessor satisfied the wait, or consumer/producer observations belonged to different attempts | Require a matching active held operation before comparison/release; retain attempt identity alongside correlation |
| A rejected or delayed mutation changes the wrong view | Rejection was hidden by a read-style sentinel or a callback trusted stale captured selection | Propagate mutation failure and recheck current view/resource ownership after acknowledgment; verify origin storage independently |
| A missing acknowledgment is reported as a rolled-back mutation | Failure phase and durable outcome were not established | Prove a pre-dispatch no-commit fault or reconcile authoritative state; keep uncertain outcomes distinct |
| Fresh-schema deletion passes while historical state would fail | Automatic reference cleanup masked a required explicit transition | Exercise the historical action boundary and distinguish pattern controls from actual handler execution |
| A localized consent probe fails and later cases fail too | The driver used another locale's label and leaked temporary fixture configuration | Use the actual locale/platform control, restore configuration in finally, and isolate dependent failures |
| A successful auxiliary write never triggers the expected refresh | The evaluator chose a branch that publishes another channel or no notification | Trace the actual branch-specific effects and observe a healthy supported trigger before the dependent probe |
| The latest read clears a newly loaded resource's context | Latest-request ordering was mistaken for current-view ownership | Guard the narrow projection with dispatch-time resource/view identity as well as latest-read ownership |
| Storage has the later association but direct consumers use an earlier target | A delayed successful acknowledgment was mistaken for current state | Reconcile positive authoritative association evidence without discarding valid work or immutable snapshots |
| A gesture-order fix is called commit-order correct after only aligned dispatch/commit tests | The gate cannot distinguish those rules | Design reverse-commit and reverse-ack schedules separately; leave unexecuted orderings uncovered |
| A consumer action times out behind a drawer while state checks pass | Interaction overlays prevented the driver from reaching the value oracle | Close supported local visibility controls, retain the adapter failure and rerun the unchanged value gate |
| An independent reviewer overstates what a schedule or file timestamp proves | Review conclusions were not checked against raw cases and content identities | Correct and retain the review, reconcile actual input digests, and separate predictions from execution |
| A no-repair read barrier prevents later saves from finishing | The held read also gates the product's persistence/progress queue | Trace the wait graph; use a bounded, explicitly scoped delivery condition that preserves progress without repairing the judged state |
| A valid action is rejected for a transport cancellation | Expected-fault accounting ignored an actual framework close inside the window | Correlate the exact connection's recorded close and failure one-to-one; keep unknown and late failures distinct |
| Fragmentation coverage is claimed but the payload was never cut there | Prefix lengths, serializer formatting or multibyte width misplaced offsets | Locate and inspect cuts in the actual encoded bytes before attributing coverage |
| A consumer's unchanged index is cited as proof it used a repaired dependency | The changed compiled transitive module was not identified | Bind the full affected consumed artifact closure and serialize shared-output builds |
| A wrapper exits zero while its positive probe failed | A final diagnostic command swallowed the operation's status | Propagate the inner status and require the semantic positive/negative result; retain the failed receipt |
| Documented precedence matches a comment but contradicts the branch | Tests for one parameter type were generalized to another | Trace omission/false/empty handling per field and correct prose without silently changing product behavior |
| Similarly named features have contradictory flag, stage or score prose | One entry's pipeline was copied onto a different execution path | Trace each entry and branch; identify score meaning and consumer cutoffs before correcting shared prose |
| Turning off an optimization broadens access, or filtered records coexist with off-scope metadata | A fallback dropped caller authority, or only one result projection was scoped | Preserve scope through the alternate path and evaluate primary data and enrichment separately |
| A rejected request is called unreachable under another configuration, or a recursive fallback broadens its scope | A negative control pinned only one guard condition, or re-entry lost caller context | Trace the selected false branch and recursive call; pair positive reachability/progress with independently scoped request observations |
| A helper returns completion metadata but the public response reports no cut | The handler's projection was assumed to preserve the helper's result | Observe the public boundary; record missing values as a finding and freeze a versioned positive propagation gate before repair |
| An inclusion probe passes for a source excluded by the generator | Identical wording on an included page was attributed to another surface | Bind the probe to the configured source section and check excluded surfaces separately |
| A passed build is repeated after a report/probe error | Producer completion and downstream judgment were treated as one indivisible run | Retain the producer receipt/output and repair/rejudge only the affected stage |
| Losing the last resource grant expands retrieval or enrichment | An empty scope was collapsed into an absent filter, or a resolved scalar scope was dropped | Exercise lifecycle-produced empty state and carry the effective scope through every consuming seam |
| A complete schema comparison is inferred from route counts or a version label | Runtime generator output, transitive types or normalization semantics were never compared | Capture the full real schema, retain exhaustive differences and identify operations, facets and equivalence passes separately |
| A later suite count is used to reject an earlier passing total | Different selections or counting units were treated as the same run | Reconcile actual per-invocation selections and correct summaries additively |
| Cleaning one generated cache file deletes existing local artifacts | Ignore rules, regenerability or directory mtimes were mistaken for ownership | Use an owned output destination or exact owned file identity; disclose any crossed ownership and limit preservation claims |

---

## 22. Finish with evidence and a usable handoff

### 22.1 First-session completion

A useful `translate` session should normally leave:

1. A bounded product and dependency map grounded in repository evidence.
2. One selected capability with a reason for investment.
3. Explicit intent, boundary, state, compatibility knowledge, and any acceptance delta for that slice.
4. A meaningful behavioral gate and an honest baseline.
5. A justified implementation or enabling improvement, verified to the extent the environment permits.
6. Updated affected documentation and a small navigable packet for the next change.
7. Provenance, exact commands/results, and a resumable next action.
8. Where instruction edits are permitted, a concise seven-primitive operating contract in the actual agent instruction entry point, with scoped owners/gates, working harness pointers or read fallbacks, and a recorded fresh-session check. Distinguish observed loader delivery from content/navigation checks; otherwise retain the permitted local contract and report the instruction-edit or entry-path limitation explicitly.

Exercise replacement when the survival pack and environment support it. If a prerequisite is genuinely missing, complete the independent useful work and leave the precise blocker. Do not claim replacement from a plan to perform it later.

For a wider campaign, this list defines a completed slice, not a reason to stop the engagement. Continue through the requested outcomes while useful authorized work remains. At campaign closure, account for every in-scope capability through the inventory in Section 3.4, and report unresolved work explicitly. A justified decision to retain a stable implementation can complete that area's assessment without implying it was replaced.

Audit completion per primitive and per consequential boundary, using the claim distinctions in Section 3.3. Name missing declarations, oracles, adapters, state fixtures or environments for the **claimed** next change or replacement. Do not manufacture tests or rewrites for stable low-impact areas merely to fill a matrix.

### 22.2 Unit-level definition of done

To call a slice **verified**, it MUST have a defined objective, justified changes, relevant updated durable knowledge and documentation, all applicable required checks passing on the final integrated candidate, and retrievable evidence. State whether it was a patch, refactor, replacement, migration, evaluation improvement, or documentation improvement.

State whether the claimed improvement in future-change cost is a hypothesis or was exercised by a subsequent change. Useful verified work can be complete before that later evidence exists; do not manufacture a second implementation or an unrequested feature just to strengthen the claim.

To additionally claim **replacement-demonstrated**, the unit MUST have:

- **Intent:** accepted obligations, important negative constraints, and reasons outside the implementation.
- **Target:** an explicit architectural boundary, ownership, side effects, constraints, and internal freedoms.
- **Oracle:** reusable behavioral evaluations covering the important known failure classes and affected integrated journeys, with meaningful evidence that failures are detected.
- **Substitution evidence:** an identified alternate implementation exercised against the gate and supported consumers, preserving the supported boundary without requiring consumer changes and without invoking the removed implementation.
- **State continuity:** applicable historical state and effect behavior preserved or intentionally migrated.
- **Provenance:** a survival-pack manifest, retrievable inputs, decisions, candidate identity, evaluation results, and limitations.
- **Documentation:** accurate instructions and examples for the demonstrated version and workflow.

To claim **reconstruction-demonstrated**, add the fresh-context, survival-pack-only reconstruction evidence defined in Section 15. Source-assisted replacement alone does not establish this claim.

To claim **operationally-observed**, supply identified deployment and representative runtime evidence meeting the observation policy. This can apply to a patch or migration as well as a replacement.

To claim **retired-and-compacted**, supply evidence that the named old runtime path and temporary machinery are removed where their obligations have ended, with supported compatibility and retained recovery knowledge accounted for. Retirement does not require generating a replacement for obsolete behavior.

These are scoped evidence claims, not guarantees of exhaustive correctness.

### 22.3 Final response format

End each engagement with a concise summary backed by durable records. Include the applicable entries; a local patch does not need a table of unused deployment or reconstruction fields.

```text
Objective and scope:
Selected capability and why:
Benefit hypothesis and observed change cost, when assessed:

What changed:
- Product behavior / implementation
- Durable intent, architecture, and contracts
- Tests, evaluations, and documentation

Verification:
- Exact commands, working directories, result states
- Candidate/revision and evidence references
- Meaningful negative controls or replacement rehearsal performed

Actual completion and evidence claims:
- Knowledge readiness and verified change type
- Replacement and reconstruction: demonstrated or actual unresolved status
- Operation and retirement: observed/completed or actual unresolved status
- Pilot/campaign outcomes achieved; inventory of remaining in-scope work

Remaining uncertainty and blockers:
- What is unknown, not run, external, or not yet observed

Retirement and next slice:
- Temporary paths and their exit conditions
- Highest-value next action, prerequisite, and success criterion
```

Keep the final narrative shorter than the evidence it links to. The repository must carry the knowledge forward after the chat ends.

### 22.4 The ongoing standard

Before closing any future change, ask:

> Did this work make the product's important behavior easier to understand, independently evaluate, and preserve through the next implementation change?

If yes, the product has gained durable engineering value. Repeat that loop at useful boundaries, at a pace justified by the product, until replacement becomes an ordinary, evidence-backed operation.
