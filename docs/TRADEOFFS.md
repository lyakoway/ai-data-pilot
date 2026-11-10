# Trade-offs — decisions with measured consequences

Every choice below was made against data from the project's own artifacts,
not taste. Numbers cite `backend/bench_results/*` (manifests inside).

## 1. Default model: not the most accurate one

Measured SQL Result Accuracy (golden v1, 50 cases, Wilson 95% CI):

| Model | Result Acc | SQL-plan p50 | Cost |
|---|---|---|---|
| GLM-4.5-flash (free) | **65.9%** (51.1–78.1) | 15.3 s | free |
| GLM-4.6 (paid, default) | 40.0% (27.0–54.5) | 13.7 s | paid |
| GLM-5.2 | 37.8% | 5.7 s | paid |

The free model wins on quality because deep thinking produces better SQL —
but its **p95 is 69 s** (thinking-heavy tail) vs 12–16 s for GLM-5.x. The
default stays GLM-4.6 as a *product* choice; the measured recommendation
(free model for quality, 5.2 for latency) is documented, not silently
applied. After the stage-4 prompt fix the default's accuracy held while the
best model reached 73.3% (paired A/B, +6/−2 case flips).

**Why not "most accurate wins"**: n=45 overlaps Wilson CIs — declaring a
winner on a 2-case gap would be statistical malpractice; the honest claim is
"consistent multi-category improvement in the targeted categories".

## 2. Per-tenant sqlite files now, PostgreSQL RLS later (ADR-007/003)

The alternative under pressure was an SQL-table allowlist inside the Guard.
Rejected as the primary fix: extracting table names from arbitrary SQL
(subqueries, CTEs, unicode quoting) is brittle — bypassable in minutes by a
motivated attacker — and the Guard is a pure function with no tenant
context. Physical file isolation (`csv_db/<ws>.db`) closes the entire class
of "generated SQL names a foreign table" — the engine *cannot resolve* the
name (permanent regression test). Cost: ~12 KB empty-file cleanup handled,
files-per-workspace growth — acceptable until the ADR-003 PostgreSQL
migration, which remains the end state.

## 3. Guard allowlist stays deferred (second layer, not first)

Same reasoning as above, one step further: an allowlist is defense-in-depth
*after* physical isolation, not a substitute for it. Implementing it first
would have produced a false sense of security while the shared-file engine
kept the real hole open. It remains on the P1 list.

## 4. Quality vs latency vs cost, quantified

- Thinking phase adds **+3…+26 s** to answer TTFT (token-level measurements,
  Z.ai) — the single biggest latency lever; the planner prompt now pins
  real data years and money rounding instead of paying for blind thinking.
- Concurrency: free endpoint returns **429 at concurrency 5** (after SDK
  retries); paid GLM-5.2 holds 8/8 at c=10 with p95 21 s (4× p50) — capacity
  planning must budget the queue tail, not the median.
- E2E p50 (full cycle): 16.5–33.5 s depending on model — an LLM-bound
  product truth; SSE steps make the wait inspectable rather than "faster".

## 5. LangGraph: not adopted (ADR-002)

Measured problems (step-budget exhaustion, cross-source naming) are not
orchestration problems; workflows are 15–75 s single-request; langgraph
pulls langchain-core (violates the no-LangChain constraint); migration
would risk 17 loop tests + the evaluation seams (`_generate_plan` imports).
Re-entry criteria are explicit (resumable >2 min jobs / HITL / ≥3 long
concurrent jobs) — a decision with a trigger, not a fad.

## 6. What we explicitly did NOT optimize

Inference cost in $: no reliable per-token pricing source was verified, so
the report carries token counts and leaves cost fields empty rather than
inventing tariffs. Judge calibration for RAG faithfulness is unstable
(1/3–3/3 between runs) — deltas are trusted (paired runs), absolute scores
are labeled directional.
