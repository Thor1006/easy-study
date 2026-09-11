> **Glass Membrane — Context Efficiency Refactor**
>
> I want you to modify the current Glass Membrane runtime to significantly reduce input-token usage, especially when escalating work to performance or flagship cores.
>
> The current problem is that stronger cores receive too much context. This makes escalation expensive even when only a small fraction of the supplied information is actually needed.
>
> Your goal is to redesign context flow so that:
>
> **cores receive only the minimum relevant working set they need, and request additional information lazily when required.**
>
> Do not redesign the entire runtime. Preserve the existing scheduler, Task Blackboard, logical cores, Focus Contracts, model adapter, typed message fabric, vertical half-bypass, state/versioning, and recovery model unless a change is strictly necessary for context efficiency.
>
> ---
>
> ## 1. Introduce a Context Reduction Layer
>
> Add a context preparation stage before any expensive core executes.
>
> The new path should conceptually be:
>
> `task state -> relevance selection -> deduplication -> compression -> context-budget packing -> target core`
>
> Do not send:
>
> - full chat history;
> - entire Task Blackboard;
> - full retrieval output;
> - all previous agent messages;
> - full raw documents;
> - unrelated tool logs;
> - complete project state.
>
> Strong cores should receive only:
>
> - current goal;
> - exact hard subproblem;
> - currently relevant constraints;
> - verified conclusions;
> - unresolved uncertainty;
> - compact failed/cheap-core attempt if relevant;
> - evidence references;
> - expected output contract.
>
> ---
>
> ## 2. Add hierarchical context
>
> Implement or design context levels:
>
> `L0 = immediate working context`
>
> `L1 = compact task summary`
>
> `L2 = relevant results / verified evidence`
>
> `L3 = selected source chunks / detailed intermediate data`
>
> `L4 = raw documents / complete history`
>
> A core should start with L0 and L1.
>
> L2-L4 should only be loaded when the core explicitly needs them or the Context Planner predicts they are essential.
>
> Do not automatically expose deeper levels because they exist.
>
> ---
>
> ## 3. Use references instead of payloads
>
> Large data should remain in shared storage.
>
> Use logical references such as:
>
> - `source://...`
> - `result://...`
> - `context://...`
> - `compute://...`
> - `task://...`
>
> Inter-core messages and escalation packets should transmit references and compact metadata rather than raw large content.
>
> A core may request dereferencing when needed.
>
> Example:
>
> Bad:
>
> `send full 20,000-token document`
>
> Good:
>
> `source://paper17/section4`
>
> with:
>
> `summary`
>
> `confidence`
>
> `why_relevant`
>
> ---
>
> ## 4. Add lazy context retrieval
>
> Expensive cores must be able to request additional context on demand.
>
> Example flow:
>
> `F0 receives 4k-token minimal packet`
>
> `F0 discovers missing evidence`
>
> `F0 -> CONTEXT_REQUEST(source://..., section=...)`
>
> `Context Service -> returns only requested material`
>
> The runtime should avoid sending all possible context upfront “just in case.”
>
> ---
>
> ## 5. Add delta state updates
>
> When a core already knows task state version N, do not resend the full task state for version N+1.
>
> Send only the state delta.
>
> Example:
>
> `STATE_DELTA`
>
> `from: 41`
>
> `to: 42`
>
> `+ constraint: power_limit = 300W`
>
> `- invalidate result://P2/17`
>
> `+ verified result://E3/44`
>
> The core should apply the delta to its existing working view.
>
> If a core cannot safely apply a delta because its local state is too old or inconsistent, request a fresh compact snapshot.
>
> ---
>
> ## 6. Add per-core context budgets
>
> Every core should have a context-token budget.
>
> Do not use fixed final numbers yet; make them configurable.
>
> The scheduler or Context Planner should choose a context budget based on:
>
> - core role;
> - model capabilities;
> - task difficulty;
> - current uncertainty;
> - expected relevance;
> - latency/cost budget.
>
> Efficient cores should normally receive the smallest context.
>
> Performance cores receive larger but still tightly selected context.
>
> Flagship cores receive adaptive context, not unlimited context.
>
> ---
>
> ## 7. Add a Context Planner
>
> Add a Context Planner service responsible for selecting what a core sees.
>
> It should rank candidate context items using factors such as:
>
> - relevance to current Focus Contract;
> - state freshness;
> - verification status;
> - dependency importance;
> - novelty;
> - contradiction/conflict relevance;
> - source quality;
> - token cost.
>
> Conceptually optimize:
>
> `maximum useful information`
>
> subject to:
>
> `total context tokens <= assigned context budget`
>
> Do not overcomplicate the first implementation. A simple scoring/ranking system is acceptable.
>
> ---
>
> ## 8. Different roles receive different context views
>
> Do not send the same task packet to all agents.
>
> Example:
>
> Primary reasoner receives:
>
> - goal;
> - constraints;
> - verified results;
> - critical evidence;
> - unresolved hard questions.
>
> Retrieval worker receives:
>
> - exact search target;
> - keywords/concepts;
> - source restrictions;
> - expected evidence schema.
>
> Verifier receives:
>
> - claim to verify;
> - assumptions;
> - supporting evidence refs;
> - required independence rules.
>
> Synthesizer receives:
>
> - validated result summaries;
> - conflicts;
> - confidence;
> - evidence refs;
> - final output contract.
>
> Context should reflect role, not merely task membership.
>
> ---
>
> ## 9. Producer-side compression
>
> Agents should compress their outputs before publishing them.
>
> Workers should not dump full reasoning unless specifically requested.
>
> Preferred result structure:
>
> `CLAIM`
>
> `SUMMARY`
>
> `CONFIDENCE`
>
> `EVIDENCE_REFS`
>
> `ASSUMPTIONS`
>
> `CONFLICTS`
>
> `DETAIL_REF`
>
> Store longer reasoning or raw extraction separately and reference it.
>
> Stronger cores should receive compact summaries first.
>
> ---
>
> ## 10. Multi-resolution summaries
>
> Important sources/results should support multiple summary sizes.
>
> Example:
>
> `source://paper17`
>
> - tiny summary
> - short summary
> - section summaries
> - extracted claims
> - raw chunks
>
> The Context Planner should select the smallest level sufficient for the current task.
>
> Do not send raw detail when a short summary is enough.
>
> ---
>
> ## 11. Prompt/prefix reuse
>
> Where provider support exists, structure prompts so stable prefixes can be cached/reused.
>
> Separate:
>
> - static Glass Membrane instructions;
> - project-level instructions;
> - task-level stable context;
> - dynamic task delta.
>
> Avoid rebuilding the entire system/project prompt every turn.
>
> Keep this provider-agnostic at the runtime level but allow provider adapters to exploit prompt caching where supported.
>
> ---
>
> ## 12. Performance-core escalation packets
>
> When escalating from an efficient core to a performance core, do not transfer the whole task by default.
>
> Construct a Minimal Escalation Packet containing:
>
> `GOAL`
>
> `HARD_SUBPROBLEM`
>
> `RELEVANT_CONSTRAINTS`
>
> `CURRENT_BEST_RESULT`
>
> `WHY_ESCALATION_IS_NEEDED`
>
> `FAILED_OR_UNCERTAIN_ATTEMPT`
>
> `CRITICAL_EVIDENCE_REFS`
>
> `EXPECTED_OUTPUT`
>
> The performance core should act as a specialist/consultant whenever possible.
>
> Escalate the hard subproblem rather than the entire task.
>
> ---
>
> ## 13. Flagship context rules
>
> Flagship cores should not automatically receive more context merely because they are stronger.
>
> They should receive:
>
> - high-value context;
> - high-confidence summaries;
> - critical contradictions;
> - only the raw evidence necessary for deep reasoning.
>
> Flagship cores may request deeper context when needed.
>
> Default behavior should be:
>
> `compact packet first -> lazy expansion`
>
> ---
>
> ## 14. Add context-efficiency telemetry
>
> Add metrics to measure whether the refactor actually helps.
>
> Include:
>
> **Context Efficiency Ratio (CER)**
>
> `useful context tokens / supplied context tokens`
>
> Also record:
>
> - total input tokens per core;
> - fresh input tokens;
> - cached input tokens;
> - context requested after initial packet;
> - unused context;
> - repeated/redundant context;
> - average escalation packet size;
> - context cache hit rate;
> - number of dereference requests;
> - context compression ratio.
>
> Do not invent fixed target values yet.
>
> ---
>
> ## 15. Add anti-bloat rules
>
> Add these as runtime rules:
>
> 1. No full-context escalation by default.
> 2. No component forwards information merely because it possesses it.
> 3. Every context item must justify inclusion against the receiver’s current Focus Contract and token budget.
> 4. Prefer references over payloads.
> 5. Prefer deltas over complete state retransmission.
> 6. Prefer compact summaries over raw data.
> 7. Prefer lazy retrieval over speculative preloading.
> 8. Prefer role-specific context views over one universal task context.
> 9. Stronger models do not automatically get larger prompts.
> 10. Context is a working set, not a database dump.
>
> ---
>
> ## 16. Preserve correctness
>
> Token reduction must not weaken state consistency.
>
> Context packets must include enough metadata to detect stale information:
>
> - task ID;
> - state version;
> - result version;
> - freshness;
> - verification status;
> - dependencies where relevant.
>
> If aggressive compression creates ambiguity or removes a critical qualification, preserve the qualification.
>
> Reliability is more important than saving a few extra tokens.
>
> ---
>
> ## 17. Implementation order
>
> Implement/refactor in this order:
>
> 1. references instead of large payloads;
> 2. role-specific context packets;
> 3. per-core context budgets;
> 4. lazy context retrieval;
> 5. state deltas;
> 6. producer-side compression;
> 7. multi-resolution summaries;
> 8. prompt/prefix reuse;
> 9. Context Planner ranking;
> 10. telemetry and optimization.
>
> Do not implement every optimization at once if it makes the runtime unstable.
>
> ---
>
> ## 18. Testing requirements
>
> Create tests comparing the current implementation against the new context-efficient implementation.
>
> Use the same models and tasks.
>
> Measure:
>
> - input tokens;
> - output tokens;
> - total cost;
> - answer quality;
> - latency;
> - state correctness;
> - number of context requests;
> - CER;
> - stale-context errors;
> - duplicate context transmitted.
>
> Include at least these tests:
>
> **Test A — simple escalation**
>
> Efficient core escalates one difficult subproblem to a performance core.
>
> **Test B — large project context**
>
> Large source store exists, but only a small subset is relevant.
>
> **Test C — state change**
>
> User changes one global constraint; active cores should receive only relevant deltas.
>
> **Test D — lazy fetch**
>
> Flagship begins with minimal context and requests exactly one deeper source.
>
> **Test E — multi-core**
>
> Several cores work on the same task but receive different context views according to role.
>
> ---
>
> ## 19. Success criteria
>
> The refactor is successful if:
>
> - expensive cores receive substantially fewer input tokens;
> - answer quality does not materially decline;
> - state coherence remains intact;
> - context requests are traceable;
> - large information objects are not duplicated across agent messages;
> - performance cores are used as narrow specialists when possible;
> - the system can explain why each supplied context item was selected.
>
> ---
>
> ## 20. Core design principle
>
> Add this principle to Glass Membrane:
>
> **Move intelligence to the problem, not the entire memory space to the intelligence.**
>
> And:
>
> **Cores see views of memory, not memory itself.**
>
> Before changing code, first audit the current implementation and identify exactly where unnecessary input tokens are being introduced. Show me the highest-cost context paths, then make the smallest architectural changes that eliminate those costs without weakening correctness.

That last line is important because it forces Claude to **profile first instead of immediately rewriting everything**.