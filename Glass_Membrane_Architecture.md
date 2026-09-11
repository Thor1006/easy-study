# Glass Membrane Architecture

Canonical architecture consolidation · 11 September 2026

## 1. Status and provenance

**Glass Membrane** is a model-agnostic, adaptive AI runtime. This document records the settled architecture selected for consolidation by the project owner. It specifies intended behavior; it does not assert that the runtime is implemented, benchmarked, certified, or an established industry standard.

The predecessor is `Adaptive_Heterogeneous_AI_Runtime_Project.md`, originally saved at `/mnt/data/Adaptive_Heterogeneous_AI_Runtime_Project.md`. That attachment was not accessible in the current desktop workspace, and the conversation reader exposed its creation summary rather than its file contents. This consolidation therefore uses the recovered architecture discussion in **ChatGPT Cursor Control**, including the predecessor-era architecture descriptions, later revisions, and the owner's explicit inclusion and exclusion instructions. It is not a line-by-line revision of the unavailable file. The predecessor remains a historical record.

This document is the canonical reference for the settled runtime scope described below. Earlier fixed activation ladders, named-model assignments, speculative capacity figures, and expanded agent configurations do not override it.

StudyAgent is an application harness for Glass Membrane. Its canonical project proposal remains separate; classroom behavior, application UI, and StudyAgent-specific requirements are not merged here.

Naming note: the owner explicitly selected **Silicate** for the GUI/operator interface. It remains a proposed interface name and does not rename the Glass Membrane runtime or prescribe a GUI implementation. No umbrella ecosystem name is selected here.

## 2. Architectural invariants

1. Canonical state belongs to the runtime, never to a model.
2. Shared results are immutable and versioned. Canonical state changes are validated transactions.
3. Models request resources; the runtime allocates them.
4. Large information objects move by reference. Messages carry control, metadata, and references.
5. Difficulty selects model strength; useful parallelizability selects core count. Effort is a separate scheduling choice.
6. A core, provider, or tool may fail without corrupting the global run. Recovery must account for partially completed work.
7. Every operating mode preserves state coherence, permission enforcement, resource accounting, and recovery controls.

The runtime favors efficiency for easy work and stronger reasoning for difficult work. High difficulty alone is not a reason to activate every core.

## 3. System structure

```text
User / application harness / tools
                  |
             Input Gateway
                  |
       Filter / Router: 8 slots
          |                  |
       new work           filtered steer
          |                  |
          |           Interrupt Controller
          |                  |
          +--------+---------+
                   |
                Kernel
      Scheduler · State Manager · Permissions
        Budget Governor · Deterministic Watchdog
                   |
      +------------+-------------+
      |            |             |
Task Blackboard  Typed Fabric  Context Service
      |            |             |
      +------------+-------------+
                   |
         12 logical reasoning cores
                   |
        Model Adapter / benchmark registry
                   |
          Available provider models

Shared services: retrieval, deterministic compute, storage
Governed expansion: Swarm Governor -> provider-native children
Control plane: authorized developer operations and experiments
Durability: checkpoints, write-ahead journal, versioned objects
Observability: telemetry, diagnostic logs, audit records
```

These are logical boundaries. The specification does not require a separate process, machine, transport, or model for every box. Skills, application harnesses, graphics workflows, and provider-specific behavior remain outside the kernel.

## 4. Eight-slot routing front end

The front end has eight logical slots. The established routing arrangement is four efficiency slots, two intermediate slots, and two stronger slots. These are capability classes rather than permanent provider/model identities. Slots activate as needed; eight slots do not require eight simultaneous model calls.

Routing ordinarily starts with the least expensive suitable capability and escalates when necessary. It distinguishes:

- **Answer confidence:** whether the front end can complete the request adequately.
- **Routing confidence:** whether it can identify the required resources without solving the request itself.

A request may leave the cascade once the applicable routing or completion policy is satisfied. Exact confidence thresholds require evaluation and are not fixed here. A front-end answer can use zero reasoning-pool cores while still incurring front-end inference and accounting costs.

The routing packet describes the task, domain, difficulty, uncertainty, parallelizability, context and freshness needs, retrieval, compute, modalities, verification, and recommended capability and effort. It is a recommendation to the scheduler, not authority to allocate resources or bypass permissions.

## 5. Twelve logical cores and adaptive scheduling

The reasoning pool contains **12 independently addressable logical core slots**. A core is a runtime execution slot with a task assignment, model binding, focus contract, private workspace, mailbox, budget, and lease. It is not a physical processor, a permanently assigned model, or a guarantee of provider concurrency.

Historical labels `E0–E5`, `P0–P3`, and `F0–F1` may remain as reservation-class labels. They must not hard-code model identity or prevent rebinding when policy permits. The logical identity survives a model change.

The scheduler can select any useful subset of the pool. It is not restricted to the older illustrated activation steps. Core count, model strength, and effort vary independently and can change as the task graph develops.

The scheduler considers ready independent work, dependencies, specialization, uncertainty, verification value, provider health, budgets, and expected coordination cost. A difficult sequential problem may use a strong core with limited support. A broad collection of simple independent tasks may use several economical cores. Twelve active cores do not automatically imply OVERDRIVE.

Capacity, permissions, queue priority, dependencies, versions, leases, and budget enforcement use deterministic logic. Models can assist semantic judgments such as difficulty assessment, decomposition, or conflict interpretation; those judgments remain subject to runtime validation.

Output length is independent of internal reasoning effort. A short answer can still require substantial reasoning.

## 6. Model Adapter and benchmark registry

The Model Adapter separates runtime contracts from provider-specific invocation and capability details. It supports model binding, supported effort settings, tool and structured-output capabilities, context handling, usage reporting, provider errors, and native swarm support where available.

The benchmark registry records measured capability and compatibility for a specific model/configuration. Relevant dimensions include domain performance, latency, token efficiency, focus adherence, delegation quality, steerability, tool reliability, context behavior, and parallel coordination. Profiles carry their evaluation/configuration provenance and must distinguish measured results from unknown support.

Model choice is task-specific. Provider names and marketing tiers do not establish permanent rankings. An unavailable feature must be reported or handled through an explicitly supported fallback rather than assumed present.

Migration transfers semantic runtime state: objective, task position, constraints, evidence references, accepted results, progress summary, dependencies, mailbox state, and output contract. Cross-provider transfer does not imply access to or portability of hidden model memory or KV caches.

## 7. Downward delegation and Swarm Governor

Strong reasoning cores prioritize interpretation, synthesis, proof, and conflict resolution. Retrieval, extraction, indexing, cleanup, and routine checks normally go to suitable less expensive cores or deterministic services. Numerical computation, hashing, storage, and version management use deterministic services where appropriate.

This is a preference against expensive bulk retrieval, not an absolute ban on a strong core inspecting a source. Direct inspection remains possible when necessary to preserve reasoning quality or resolve ambiguity.

All logical cores may request provider-native swarm work where the adapter supports it. The **Swarm Governor** controls admission, purpose, child allocation, budgets, lifecycle, cancellation, and usage accounting in coordination with the scheduler.

The governor must:

- Prefer suitable idle logical cores before adding provider-native children.
- Require a bounded purpose and identifiable parent task.
- Consider duplication, useful parallel work, provider limits, and global resource pressure.
- Grant, reduce, redirect, or deny a request.
- Keep descendants within approved limits; a parent cannot authorize recursive expansion.
- Return results through normal references, validation, and state transactions.
- Account for child usage and failures, including provider visibility limitations.

Native children do not create additional canonical logical-core identities in the 12-slot pool. They are subordinate workers. Provider-native transport may be used only while preserving runtime contracts and controls. There is no fixed total-agent configuration in this specification.

## 8. Task Blackboard and focus contracts

The **Task Blackboard** is the shared structured view of the task graph. It records goals, constraints, nodes, dependencies, owners, leases, state versions, pending requests, accepted result references, conflicts, and invalidation status. Cores read authorized views and submit proposals; they do not directly edit shared canonical objects.

Each assignment includes a **focus contract** describing:

- Task/node identity and the specific job.
- Input references and applicable state version.
- Expected output and completion criteria.
- Permitted tools, actions, and scope.
- Budget and lease constraints.
- Dependencies, subtask request rules, and stop conditions.

A core that discovers adjacent work requests a subtask or raises a conflict. It cannot silently expand its scope or recruit workers. The scheduler decides whether to amend the contract, add a dependency, assign support, or defer the issue.

Core state stays compact: identity, execution status, assignment, model binding, focus contract, state/context references, usage, lease expiry, pending requests, and latest checkpoint. Large evidence and outputs live in referenced storage.

## 9. Typed event fabric and per-core mailboxes

The fabric organizes communication by typed events, task topics, and dependencies. It supports addressed delivery and task-relevant publication/subscription without requiring a full conversational mesh between all cores.

Event types include assignment, result readiness, data requests and readiness, dependency completion, steering, constraint changes, conflicts, pause/resume, cancellation, core failure, and budget warnings. Envelopes identify the event, task/node, sender, destination or topic, relevant state version, and referenced payload. Correlation supports matching requests and replies.

Each core has logically separate control, result, and data queues/mailboxes. Control traffic receives priority so cancellation or new constraints cannot sit behind bulk evidence notifications. A single physical transport is acceptable if logical traffic classes remain independently scheduled.

Documents, datasets, and substantial results stay in storage. Messages carry references such as `source://...` or `result://...`, with enough metadata to resolve the correct version. A delivered message is not itself permission to commit a result.

Queues are bounded. Backpressure propagates from slow consumers, saturated context services, provider rate limits, and storage pressure to upstream producers. Admission can pause, throttle, or defer work. Persisting a referenced object does not remove the obligation to bound pending work and storage usage.

## 10. Context as a service

The context service builds bounded, task-relevant packages rather than distributing the entire shared cache. Packages draw on current constraints, relevant evidence, dependency results, verified findings, and recent steering, with source references, versions, freshness, and verification status.

Source, context, result, and compute objects have distinct roles. Raw evidence remains recoverable through references after extraction or condensation. Summaries do not silently replace their sources or erase uncertainty and disagreement.

Information status distinguishes raw, extracted, verified, contested, stale, and invalid material. Verification is a recorded check, not a guarantee of truth. Results can require rechecking after a dependency or constraint changes. Stale or invalid information must not silently enter current reasoning as authoritative evidence.

Cache size, retrieval policy, context limits, freshness, and eviction remain configurable. Storage retention must preserve objects required by active work and recovery; the cache label does not make a committed result disposable.

## 11. Vertical half-bypass and interruption

**Vertical half-bypass** lets filtered steering reach the relevant active work without restarting the entire task or passing through a full new-task routing cycle. Input still enters through the gateway and filter. The bypass concerns routing latency, not permission or state validation.

The Interrupt Controller first matches steering against task/core metadata deterministically. Ambiguous semantic matching may use a model. A dedicated model is not required to run continuously as a traffic controller.

The runtime records a steer against the current state, commits the changed constraints or task intent, identifies affected nodes, and sends prioritized notifications. It marks earlier results valid, stale, invalid, or needing recheck as appropriate. Unaffected work can continue.

Provider cancellation and interruption capabilities vary. If in-flight generation cannot be stopped immediately, the runtime can still prevent its obsolete output from being accepted. Late results must be checked against the updated task and dependency versions.

## 12. Canonical state, immutable results, and leases

Canonical mutations follow this logical sequence:

```text
Read versioned state -> propose change -> validate
    -> record durable transition -> commit new version
```

Validation covers relevant versions, dependencies, ownership/lease, permissions, and output requirements. A stale proposal is rejected or reconsidered against current state; it is never silently accepted as current. The exact database or concurrency mechanism is an implementation choice.

Published results are immutable. Corrections create new result versions with supersession links. Acceptance and validity metadata can evolve through canonical transactions without rewriting historical result content. Cores retain private scratch work and shared read access to permitted versioned objects.

Assignments use renewable, bounded **leases**. A lease ties an owner to work and limits its allocation. Expiry or revocation allows the scheduler to reclaim work. A worker that finishes after losing ownership cannot commit under its old authority.

Version checks and ownership validation must also apply during migration, retries, cancellation, and recovery. Immutable publication alone does not imply that a result has passed validation or belongs in the current answer.

## 13. Watchdog, write-ahead journal, and recovery

A deterministic watchdog monitors heartbeats, lease expiry, provider health, stalled dependencies, queue pressure, usage, commit progress, tool failures, and storage health. It distinguishes runtime failures from model errors such as incorrect reasoning or poor tool selection.

Predefined recovery actions include pausing admission, revoking leases, retrying bounded work, restarting a core, migrating an assignment, rebinding a provider, or stopping a task with a diagnosable failure. Recovery does not require a model to decide whether basic runtime invariants hold.

Meaningful state transitions are recorded in a **write-ahead journal** before their commit becomes authoritative. Checkpoints and journal replay reconstruct committed task state, assignments, accepted results, and pending work after a crash. Replay must distinguish committed transitions from incomplete attempts and avoid duplicate application.

Runtime replay does not guarantee identical model generations or make external actions transactional. Tool actions whose completion is uncertain require reconciliation before retry; where supported, action identifiers can prevent duplicate effects. Committed results and audit records must remain reachable during recovery.

## 14. Telemetry

The following vocabulary is retained. It describes observability requirements, not a claim that model capability can be measured as a universal physical quantity.

| Metric | Meaning and reporting contract |
|---|---|
| **ACP** — Active Compute Power | Estimated active inference capacity per core and in aggregate, with model/configuration and measurement window. A proxy for opaque provider inference, not measured FLOP/s or answer quality. |
| **CC** — Core Capacity | Maximum capacity currently permitted for a core under its binding, effort, and allocation policy. Changes when those constraints change. |
| **TC** — Total Compute Capacity | Capacity the runtime can use concurrently under current global constraints. It must respect shared provider limits and avoid double-counting children. |
| **TL** — Token Ledger | Per-call, core, child, task, provider, and run usage: fresh input, cached input, output, and other provider-exposed categories. Communication, retrieval context, and tool serialization are attribution categories and may overlap billing categories. |
| **PE** — Parallel Efficiency | Estimated non-duplicated useful work divided by total work over a declared window. The evaluation must define useful work; raw occupancy is insufficient. |
| **ISR** — Information Stall Ratio | Time waiting for data, context, tools, or dependencies divided by the declared active-session interval; expose causes separately. |
| **DFE** — Data-Flow Efficiency | Useful bytes or tokens delivered to the correct consumers divided by total bytes or tokens moved. Report byte and token measures separately and define usefulness. |
| **SCR** — State Coherence Rate | Operations using valid applicable state divided by all state-dependent operations in the measurement scope. Report stale reads, rejected stale commits, and invalid accepted commits separately. |

**Local Workload (LW)** remains separate: CPU/GPU/NPU utilization where available, RAM/VRAM, disk and network activity, and compute queue pressure. Local hardware activity must not be added to an inference proxy without a justified common measurement basis.

Accounting must identify disjoint populations. If ACP includes child work, aggregate it once. If CC includes a child allowance, do not add that allowance again to TC. Front-end inference, support work, and native children remain visible even when reasoning-pool activation is low. Totals must state their scope.

Normalized Runtime Compute Units may be investigated as a reporting basis, but this document fixes no reference model, conversion factors, effort multipliers, or calibration values. Unavailable data is unknown, not zero. Estimated metrics must expose their method and uncertainty.

Supplementary diagnostics include queue depth and latency, cache behavior, dependency stalls, duplicate work, retries, cancellations, provider errors, and state conflicts. High utilization is not a goal when extra concurrency would add duplication or coordination cost. No nonzero rate of invalid canonical commits is authorized by a telemetry target.

## 15. Developer control plane and experimental safety

The developer control plane observes and requests changes to the runtime data plane through authorized interfaces. Operations can include inspecting cores, pausing/resuming, changing effort, rebinding models, injecting steering, revising focus contracts, inspecting mailboxes, migrating work, and restarting workers.

These operations go through scheduler and state validation. They do not grant arbitrary writes to live canonical structures or hidden model memory. Overrides are scoped, attributable, logged, and expiring rather than silently permanent.

Application and skill authors request capabilities through runtime APIs. Bounded framework extensions may exercise reviewed extension points. Kernel-level overrides belong to separately authorized runtime maintainers. Merely installing an extension or opening a developer UI does not grant highest authority. Final legal and distribution structures are outside this specification.

Safe testing facilities include filter-only evaluation, forced routes, routing-filter bypass into the scheduler, A/B policy comparison, replay, and controlled failure injection. These are experiment controls, not additional production modes. Filter bypass does not bypass input trust boundaries, permission checks, budgets, or commit validation. Experiments carry a recorded configuration and remain isolated from production effects unless explicitly authorized.

## 16. Operating modes

| Mode | Behavior | Preserved constraints |
|---|---|---|
| **NORMAL** | Adaptive routing, bindings, effort, and useful core activation. | Ordinary permissions, budgets, state validation, and recovery. |
| **DEBUG** | Authorized inspection and controlled overrides, with diagnostic visibility. | Scoped and logged changes; no implicit root authority or unrestricted live-state mutation. |
| **SHADOW** | Candidate routing or scheduling policy runs without authority over the live run. | Candidate output cannot commit canonical production changes or perform production side effects; overhead remains bounded and accounted. |
| **OVERDRIVE** | Explicitly authorized higher resource limits for useful reasoning, retrieval, context, and verification. | Same scheduler, fabric, context service, permissions, journal, watchdog, and transactional state as NORMAL. |

OVERDRIVE changes resource policy, not architecture. It does not require all cores to be busy, permit unbounded swarms, or select a fixed agent count. Authorization may be scoped to a task or covered by an explicit bounded user policy. Exact trigger thresholds and resource settings are left to configuration and evaluation.

Mode transitions are recorded and coordinated with in-flight work. Leaving a mode must reconcile leases, outstanding requests, and overrides so expanded limits do not persist accidentally.

## 17. Stable storage and logical namespaces

The established physical organization is:

```text
runtime/
├── config/
├── state/
│   ├── checkpoint/
│   └── journal/
├── cache/
│   ├── source/
│   ├── context/
│   ├── results/
│   └── compute/
├── logs/
└── skills/
```

The stable logical namespaces are `task://`, `source://`, `context://`, `result://`, `compute://`, `core://`, and `message://`. References identify runtime objects independently of physical paths. The runtime resolves identity, version, access, and availability.

Task/node relationships organize discoverability and dependencies; storage location does not determine semantic ownership. A database or object store may replace file-backed storage without changing the way cores reference information. This layout does not require one physical file per event or runtime object.

Configuration, canonical state, source evidence, derived context, immutable results, compute artifacts, and diagnostic logs remain distinguishable. Logs support diagnosis; the write-ahead journal and committed state define recovery authority.

## 18. Evaluation obligations

The runtime itself must be evaluated independently of provider quality. Compare the same model and workload across standalone use, a simple tool harness, basic multi-agent orchestration, and Glass Membrane under controlled token, cost, and time budgets. Record configuration and compare quality-versus-resource curves rather than presenting unqualified capability gains.

Ablation testing should isolate routing, delegation, focus contracts, context handling, verification, and parallel scheduling. Stress testing covers fan-in/fan-out, bounded queues, context churn, storage pressure, steering storms, model rebinding, conflicting results, lease expiry, provider timeouts, and partial failures.

A critical scenario is a global steer arriving while several cores finish work based on the old state. The required outcome is coherent acceptance or rejection of those results, not merely process survival. Other checks include preventing expired owners from committing, preserving control delivery under load, recovering without duplicate transitions, and keeping SHADOW non-authoritative.

These are validation requirements for a future implementation. No measurements, test passes, performance gains, or fixed acceptance thresholds are claimed by this document.

## 19. Non-normative boundary

Exploratory ideas are excluded from the canonical architecture: Behemoth; Neural Hyper-Threading and a proposed 100T own-model design; speculative neural accelerator hardware co-design; exact token counts, timings, fixed thresholds, and RCU calibrations; final licensing, legal, or governance structure; and unresolved Genesis/Synthic ecosystem branding. Their appearance in historical discussion is not approval for implementation under this specification.

Historical fixed model assignments, mandatory activation ladders, and fixed expanded-agent Overdrive configurations are likewise not requirements here. Future changes need an explicit architectural decision before entering the canonical specification.
