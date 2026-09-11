# Glass Membrane — Full Stable Project Schematic

Version: 2026-09-11 · Audience: Claude and other project collaborators

## 1. Reading map and status

`Glass_Membrane_Architecture.md` is the canonical architecture. `CLAUDE_AGENT_OPERATING_INSTRUCTIONS.md` defines role behavior and dynamic shifting. This file expands the stable design into component, lifecycle, and data-flow schematics. It is a companion view, not a replacement specification or a claim of an implemented product.

Read in that order. If a later implementation differs, report the discrepancy and consult the owner's current instructions rather than silently rewriting either the code or the architecture. Names and fields in illustrative records below are conceptual; they do not freeze a wire protocol or database schema.

Scope derives from the recovered **ChatGPT Cursor Control** conversation and the canonical consolidation. The predecessor attachment remains unavailable as recorded in that document. No measured runtime performance is claimed.

## 2. Whole-system schematic

```text
 APPLICATION / USER LAYER
 User request / StudyAgent harness / other application / optional Silicate GUI
     | goal, constraints, inputs, output preference, user steers
     v
 INPUT GATEWAY ---------------------------------------------+
 Normalize inputs; retain source and task identity            |
     |                                                      |
     v                                                      |
 EIGHT-SLOT FRONT END                                       |
 EF0 EF1 EF2 EF3: efficient filter -> PF0 PF1: performance filter -> FF0 FF1: flagship filter
     | answer confidence / routing confidence                |
     +--> suitable answer proposal -> validation -> output    |
     |                                                      |
     +--> new task recommendation                            |
     |                                                      |
     +--> filtered steer -> INTERRUPT CONTROLLER              |
                             | match scope and affected work |
                             v                              |
 KERNEL                                                     |
 +-------------------------------------------------------+  |
 | Scheduler <-> State Manager <-> Task Blackboard         |  |
 |    |             |                                    |  |
 | Permissions   version/dependency/ownership validation  |  |
 | Budget Governor       |                               |  |
 | Swarm Governor        +-> write-ahead journal          |  |
 | Deterministic Watchdog    -> canonical commit          |  |
 +--------------------------+----------------------------+  |
                            |                               |
       +--------------------+----------------------+        |
       |                    |                      |        |
       v                    v                      v        |
 TYPED MESSAGE FABRIC  CONTEXT SERVICE       SHARED SERVICES |
 control / result /   bounded working sets  retrieval       |
 data-ref traffic     evidence + versions   compute / tools |
 per-core mailboxes   freshness / conflicts storage         |
       |                    |                      |        |
       +--------------------+----------------------+        |
                            |                               |
 TWELVE LOGICAL REASONING CORE SLOTS                         |
 E0 E1 E2 E3 E4 E5 | P0 P1 P2 P3 | F0 F1                     |
 [identities/classes, not permanent model assignments]       |
                            |                               |
 MODEL ADAPTER <-------- BENCHMARK / COMPATIBILITY REGISTRY   |
 provider interfaces     measured profiles, capabilities     |
                            |                               |
 provider models and bounded, granted native children        |
                            |                               |
 immutable result publication -> validation -> acceptance    |
                            |                               |
 current accepted results -> output assembly -> application -+

 CONTROL PLANE: authorized inspect/pause/rebind/steer/experiment requests
 DURABILITY: checkpoints + journal + retained versioned objects
 OBSERVABILITY: accounting, telemetry, audit, diagnostics
```

Arrows indicate logical information or control flow, not a mandatory network topology. The kernel and service components need not each be separate processes. All model-originated proposals pass deterministic allocation or state checks before gaining authority.

## 3. Component contracts

| Component | Consumes | Produces | Owns/enforces |
|---|---|---|---|
| Input Gateway | Requests, files/references, steers, harness metadata | Identified normalized input | Ingress boundary and identity preservation |
| Router/front end | Input and permitted relevant context | Answer proposal or routing recommendation | Assessment only; no allocation authority |
| Interrupt Controller | Filtered steering and current task metadata | Scoped affected-node controls | Mapping and coordination with state/scheduler |
| Scheduler | Recommendations, ready graph nodes, resource state | Assignments, contracts, leases, transitions | Allocation and dependency-aware execution |
| State Manager | Mutation proposals and result metadata | Accepted/rejected transactions, new versions | Canonical coherence and ownership validation |
| Task Blackboard | Committed task graph/state | Authorized structured task views | Goals, nodes, dependencies, owners, validity |
| Permission Manager | Role, request, tool/action scope | Grant/deny decision | Action and control-plane boundaries |
| Budget Governor | Limits and usage | Admission/throttle/stop decisions | Global and scoped resource constraints |
| Swarm Governor | Child requests and capacity | Grant, reduce, redirect, deny, cancel | Bounded subordinate-worker lifecycle |
| Message Fabric | Typed envelopes and refs | Correlated delivery and notifications | Bounded queues, priority, backpressure |
| Context Service | Task/topic needs, allowed references | Bounded versioned working set | Relevance, provenance, freshness/status handling |
| Model Adapter | Binding and invocation contract | Provider results, errors, usage, capability status | Provider boundary; no silent feature assumptions |
| Registry | Reproducible evaluations | Versioned capability/compatibility profiles | Evidence for binding decisions |
| Watchdog | Heartbeats, leases, queues, provider/storage health | Deterministic recovery actions | Failure detection and predefined recovery policy |
| Output assembler | Current accepted results and output contract | User-facing answer/artifact | Presentation of accepted findings and uncertainty |

## 4. Resource topology and scheduling

```text
Front end: 8 slots, activated as needed
  4 economical routing slots
  2 intermediate routing slots
  2 stronger routing slots

Reasoning: 12 logical slots, any useful active subset
  historical reservation labels: E0-E5 / P0-P3 / F0-F1
  every binding may change when policy permits

Provider-native children
  outside the logical-core count
  subordinate to parent assignment and Swarm Governor
  no fixed total-agent configuration
```

Difficulty selects capability; independent work selects core count. Effort, verification, information breadth, specialization, provider availability, and budgets constrain the plan separately. Zero reasoning cores can be valid for a front-end completion. All 12 active does not necessarily mean OVERDRIVE.

```text
Task assessment + current graph + registry + constraints
                        |
              Find ready useful work
                        |
       deterministic tool sufficient? -> assign service work
                        |
         choose appropriate model/effort for each node
                        |
       fit useful parallel assignments to lawful capacity
                        |
           issue focus contracts and bounded leases
                        |
          execute -> observe -> accept/check results
                        |
        reassess after meaningful changes or phase completion
              /             |               \
           upshift       continue        downshift/release
```

Migration conserves semantic progress through checkpoints. It does not transfer private provider internals. The sender loses commit authority when its assignment is revoked; the receiver works under validated current ownership.

## 5. Ordinary request sequence

1. The gateway identifies the request and its harness/user context.
2. The front end attempts suitable easy completion or returns a resource recommendation. It can confidently route a task it cannot solve.
3. The scheduler and State Manager establish or update a task graph, constraints, and dependencies.
4. The scheduler selects ready assignments, checks permissions/budget/provider limits, and issues focus contracts and leases.
5. The context service resolves bounded relevant inputs; the adapter invokes the bound models with supported capabilities.
6. Workers request evidence, computation, verification, or subtasks through runtime interfaces. Resources are granted separately.
7. Results are stored immutably with input/dependency/state provenance. RESULT_READY carries a reference.
8. The State Manager validates ownership, versions, permissions, and acceptance conditions. Rejected or conflicting results are retained for diagnosis but not silently accepted.
9. Accepted dependency completions unlock further work. The scheduler revises allocations as useful work changes.
10. Output assembly uses applicable accepted results, reports material uncertainty, and releases completed allocations.

## 6. Task graph and focus model

```text
task://<task>
  goal / constraints / current version / requested output
  |
  +-- node: retrieve evidence
  |     owner + lease + focus contract + source/result refs
  |
  +-- node: deterministic experiment
  |     inputs + compute result ref + completion status
  |
  +-- node: analyze
  |     dependencies: evidence + experiment
  |     owner + lease + context refs + proposed result
  |
  +-- node: independently verify
  |     relevant assumptions + test method + findings
  |
  +-- node: synthesize accepted findings
        output contract + unresolved conflicts
```

This is an example decomposition, not a mandatory graph for every task. A focus contract specifies job, inputs, expected output, permissions, budget, ownership, dependencies, and stop conditions. Nodes are created or changed transactionally. Models may propose topology changes but do not directly edit the graph.

Duplicate work is avoided unless assigned a clear purpose such as independent verification or a distinct method. Shared visibility uses compact metadata, not shared private reasoning transcripts.

## 7. Communication and evidence flow

```text
Producer core/tool -> store large object -> immutable reference
                                             |
                            typed envelope + task/version metadata
                                             |
                                      Message Fabric
                                    /       |        \
                          CONTROL QUEUE RESULT QUEUE DATA QUEUE
                             priority      refs        refs
                                    \       |        /
                                     recipient mailbox
                                             |
                        relevant context/reference resolution
                                             |
                                      consumer work
```

Events cover TASK_ASSIGNED, DATA_REQUEST, DATA_READY, RESULT_READY, DEPENDENCY_COMPLETE, STEER, CONSTRAINT_CHANGED, CONFLICT, PAUSE, RESUME, CANCEL, CORE_FAILED, and BUDGET_WARNING. Role requests such as verification or escalation map into the implementation's typed request schema.

Identity, task/node, origin, destination/topic, state version, correlation, and references make delivery interpretable. Transport delivery and canonical acceptance are distinct operations. The architecture does not assume that an underlying transport provides exactly-once effects.

Backpressure must reach upstream producers when queues, storage, context, or providers saturate. Control traffic retains priority. More storage capacity is not permission for unbounded messages, outstanding subtasks, or irrelevant evidence.

## 8. Context and information lifecycle

```text
external/local source -> RAW -> extraction -> EXTRACTED
                                           |
                              evidence checks / verification
                                           |
                                        VERIFIED
                                           |
                       relevant, bounded context package
                                           |
                               reasoning / verification

conflict discovered -> CONTESTED
version/assumption changed -> STALE or NEEDS_RECHECK as applicable
unusable for current reasoning -> INVALID
```

These labels express validity and processing status; they need not be a single irreversible linear state machine. Validation metadata changes through runtime state operations, while published content remains immutable. Verified information may later be contested or stale.

Context packages contain constraints, evidence references, verified findings, dependencies, recent steering, versions, freshness, and uncertainty. Condensation preserves links to raw sources and does not hide disagreement. There is no mandatory dedicated condenser-agent count.

## 9. Steering, interruption, and late completion

```text
new input -> gateway -> filter
                       |
              +--------+---------+
              |                  |
           new task         existing-task steer
              |                  |
          scheduler       Interrupt Controller
                                 |
                   deterministic metadata matching
                   semantic assistance only if needed
                                 |
                  validate and commit updated constraints
                                 |
                   find affected nodes and dependencies
                                 |
            +--------------------+---------------------+
            |                    |                     |
     prioritize controls   mark affected results  preserve unaffected work
            |
      pause / recheck / revise contract / cancel
```

A result finishing concurrently with steering must be evaluated against relevant current constraints and input versions. It is accepted only if still applicable; otherwise it is rejected, marked stale, or revalidated. Provider inability to stop generation immediately does not grant permission to accept obsolete work. Cancellation stops new effects and revokes authority through runtime controls.

## 10. Transactions, leases, and crash recovery

```text
proposal + expected versions + dependency refs + lease
                          |
                  validate applicability
                    /             \
                  fail            pass
                   |               |
          reject / re-evaluate    durable journal transition
                                   |
                              canonical commit
                                   |
                         accepted result / new version
```

The durable implementation must distinguish incomplete journal attempts from committed transitions. The concrete database, atomicity mechanism, storage formats, and transport remain engineering decisions.

```text
core failure / lost heartbeat / expired lease / provider failure
                          |
                  deterministic watchdog
                          |
            revoke stale ownership; contain affected work
                          |
                latest valid checkpoint + journal
                          |
             reconcile pending work and external effects
                          |
         bounded retry / migrate / rebind / stop with diagnosis
```

An expired worker cannot commit under its old lease. Immutable results remain available for audit, but publication alone is not acceptance. Recovery must avoid duplicate canonical transitions. Model generation is not deterministic replay, and uncertain external tool effects require reconciliation before retry.

## 11. Swarm and downward delegation schematic

```text
reasoning core needs support
            |
      scoped subtask request
            |
         Scheduler
            |
   deterministic service sufficient? -- yes -> service
            |
   suitable logical-core allocation? -- yes -> core + contract + lease
            |
    provider-native request -> Swarm Governor
                                   |
                     purpose / usefulness / limits / support
                         /         |          \
                      grant      reduce     redirect/deny
                         |
                   bounded native children
                         |
                 results + usage + failure status
                         |
           ordinary state validation and dependency flow
```

Idle suitable logical cores take preference over native expansion. Any recursion requires a grant within the same global constraints. Parent and child accounting must not double-count usage. Flagship cores normally delegate bulk retrieval while retaining focused inspection needed for critical reasoning.

## 12. Developer and experiment boundaries

```text
application / skill author -> capability requests via stable runtime interfaces
bounded framework extension -> reviewed extension points and scoped experiments
authorized kernel maintainer -> separately controlled kernel operations
                                      |
                           Developer Control Plane
                           inspect / pause / resume
                           rebind / migrate / steer
                           scoped policy experiments
                                      |
                        scheduler + permission + state checks
                                      |
                                Runtime Data Plane
```

Highest override is not a normal developer feature. Overrides record actor, reason, scope, old/new values, and expiry. Direct mutation of live canonical structures or hidden model memory is not an ordinary control-plane operation.

| Mode | Resource/policy behavior | Authority boundary |
|---|---|---|
| NORMAL | Adaptive normal limits | Full invariant enforcement |
| DEBUG | Visibility and controlled overrides | Authorized, bounded, logged operations |
| SHADOW | Evaluate alternate routing/scheduling | No production commits or effects |
| OVERDRIVE | Explicitly authorized expanded limits | Same kernel, state, fabric, permissions, watchdog |

Filter-only, forced routes, A/B, replay, filter-routing bypass, and chaos testing are experiment facilities. They require explicit scope and controlled effects. Filter-routing bypass still enters scheduler/permission/state validation. Mode exit reconciles outstanding work and expires mode-specific overrides.

## 13. Telemetry map

| Signal | Source | Interpretation |
|---|---|---|
| ACP | Binding/activity estimates and exposed provider measurements | Active inference capacity proxy, with method and window |
| CC | Per-core binding and permitted allocation | Current legal per-core capacity |
| TC | Globally feasible simultaneous allocation | Capacity after shared provider/global constraints |
| TL | Provider usage plus runtime attribution | Token ledger with billing categories separated from overlapping workload tags |
| PE | Task/results and evaluated usefulness | Non-duplicated useful work / total work |
| ISR | Wait states and dependency timing | Information/tool/context wait / declared active-session interval |
| DFE | Fabric/context movement with usefulness attribution | Useful correctly delivered bytes or tokens / total moved in the same unit |
| SCR | State-dependent operations and validity checks | Valid-applicable-state operations / all scoped state-dependent operations |
| LW | Local hardware/OS/service instrumentation | Separate CPU/GPU/NPU, memory, I/O, network, and compute pressure |

Report invalid accepted commits separately from stale proposals correctly rejected. Report unknown provider counters as unknown. Child, router, and support costs stay visible without duplicate inclusion. No fixed RCU calibration, conversion to FLOP/s, universal model power scale, or claimed measurements are supplied.

High occupancy with duplicated work is poor coordination. A hard sequential task with few useful active cores can be correctly scheduled. Telemetry supports evaluation rather than rewarding saturation for its own sake.

## 14. Storage and logical namespaces

```text
runtime/
  config/                 policy and binding configuration
  state/
    checkpoint/           recoverable semantic/runtime checkpoints
    journal/              write-ahead transition history
  cache/
    source/               retained raw evidence
    context/              derived bounded context packages
    results/              immutable versioned results
    compute/              deterministic compute inputs/outputs
  logs/                   diagnostics, audit, telemetry records
  skills/                 role/task instructions
```

| Namespace | Logical meaning |
|---|---|
| task:// | Task graph identity and applicable state |
| source:// | Evidence/source object and version |
| context:// | Bounded context package with provenance |
| result:// | Immutable output version |
| compute:// | Computation artifact and provenance |
| core:// | Logical core identity and permitted metadata |
| message:// | Event/message identity and correlation |

Runtime resolution separates logical identity from physical placement. Files, a database, or object storage may implement persistence without changing references. No requirement creates a file per event. Active/recovery references constrain eviction; an accepted result is not disposable merely because its storage directory is named cache.

## 15. Evaluation concepts: runtime and model compatibility

The conversation discussed **NREP** as a runtime-evaluation concept and **NRCP** as a model-compatibility-profile concept. Retain the distinction; neither is a finalized public standard, certification program, or fixed scoring formula.

Runtime evaluation asks whether Glass Membrane improves outcomes and efficiency, and which components contribute. Compare the same workload/model under standalone, simple harness, basic multi-agent, and full runtime conditions. Control and disclose token, monetary, and time budgets, tool access, configuration, and repetitions. Use quality-versus-resource curves and ablations rather than unqualified scores.

Compatibility evaluation records a particular model/configuration's task capability, focus stability, steering response, delegation, tool use, context efficiency, verification, latency, token efficiency, migration behavior, and native-swarm feature support. Registry entries require provenance and distinguish unsupported, unknown, and measured behavior. Model compatibility does not establish a governance or licensing decision.

Stress scenarios include all useful cores active, fan-in/fan-out, interrupt storms, context churn, duplicate requests, conflicting evidence, topology changes, expired leases, provider timeouts, storage failure, queue saturation, and recovery after partial commits.

| Scenario | Required correctness property |
|---|---|
| Global steer races old results | Obsolete conclusions cannot silently enter current state |
| Worker completes after lease expiry | Old ownership cannot authorize a commit |
| Context/result queues saturate | Bounded memory/work; prioritized cancellation remains serviceable |
| Native parent is cancelled | Descendants stay controlled and accounted; late results are checked |
| Crash after journal write | Recovery distinguishes committed and incomplete transitions |
| Tool response is lost | Reconcile effects before retrying |
| SHADOW candidate disagrees | Candidate cannot mutate production state or execute production effects |
| Rebinding loses provider context | Valid semantic checkpoint suffices for honest continuation or a reported block |

These are future implementation checks, not test results from this documentation task.

## 16. Application boundary and excluded design space

StudyAgent supplies an application harness around the runtime. Its classroom capture, academic workflow, privacy decisions, and application UI remain in its separate canonical proposal. Other harnesses can supply other task domains. Graphics and specialized workflows are services/extensions outside the kernel.

Glass Membrane is the runtime/project name. Silicate is the proposed GUI/operator name accepted in the discussion. No umbrella branding is settled.

Excluded from this stable schematic: Behemoth, Neural Hyper-Threading/100T own-model concepts, speculative accelerator hardware co-design, fixed threshold/token/time/RCU numbers, final legal/licensing/governance structures, and Genesis-versus-Synthic branding. Fixed expanded-agent configurations and mandatory activation ladders are not requirements.

## 17. Implementation handoff

The directory currently provides architecture documentation. Do not infer an existing implementation from the diagrams. Before coding, inspect the actual repository and confirm the assigned implementation scope. Preserve these invariants while choosing concrete schemas, storage, provider integrations, and tests. Keep newly chosen implementation details explicitly distinguishable from decisions already recorded here.
