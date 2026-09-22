# AI Usage and Engineering Ownership Log

This file documents how AI assistance was used during the Voice Agent Latency
and Reliability Observatory project. The purpose is not to claim that AI built
the project independently. The important engineering work was deciding what to
measure, rejecting weak suggestions, checking generated code against real
behavior, and keeping evidence when systems failed.

For every phase or task below, the same three questions are answered:

- where AI helped;
- where AI was confidently wrong and had to be fixed;
- what I decided myself.

Claims in this log are grounded in repository artifacts such as source code,
tests, benchmark reports, screenshots, audit notes, and generated report files.

---

## 1. Development Philosophy

### Where AI helped
AI helped accelerate drafting, scaffolding, debugging checklists, code searches,
documentation outlines, and report generation. It was useful as a fast assistant
for exploring implementation options and turning project evidence into readable
documentation.

### Where AI was confidently wrong and had to be fixed
AI sometimes suggested generic "AI app" patterns, overly polished report
language, and implementation shortcuts before the actual repository evidence was
checked. Those suggestions were not accepted as-is. They were corrected by
running tests, reading stored benchmark data, inspecting screenshots, and
checking whether claims matched the code.

### What I decided myself
I treated AI as an accelerator, not the owner of the system. I decided that the
project should be evidence-driven: every major claim needed either code, a test,
a stored benchmark run, a screenshot, or a documented limitation behind it.

## 2. Phase 1 - Understand and Define the Problem

### Where AI helped
AI helped summarize the assignment into a measurable engineering problem: voice
pipeline latency should be measured by stage rather than described with a vague
"fast" or "slow" label. It also helped draft early explanations of STT, LLM,
tool, and TTS stages.

### Where AI was confidently wrong and had to be fixed
AI initially leaned toward simpler total-response latency descriptions and could
have treated latency as one end-to-end number. That missed the main user
experience problem: the silence after the user stops speaking.

### What I decided myself
I chose TTFA as the central metric because it matches the moment a user actually
cares about: when the first audible response starts. I defined:

```text
TTFA = FIRST_AUDIO_TIMESTAMP - INPUT_END_TIMESTAMP
```

I also decided to use fixed WAV test inputs instead of a live microphone demo so
the input boundary would be repeatable and measurable.

## 3. Phase 2 - Design the Architecture

### Where AI helped
AI helped draft the FastAPI, SQLite, React, LangGraph, provider-adapter, and
observability architecture options. It also helped turn the final architecture
into diagrams and documentation.

### Where AI was confidently wrong and had to be fixed
AI suggested heavier or more generic architecture options, including patterns
that would have added complexity without improving the benchmark. Some early
ideas also blurred the execution path and observability path.

### What I decided myself
I chose a file-driven benchmark architecture with a separate observability path:
the pipeline executes STT, LangGraph, tools, and TTS, while stage events are
persisted and streamed to the dashboard. I rejected a live RTC-first design
because framework VAD and transport timing would make TTFA harder to control.

## 4. Phase 3 - Model Plug-in Architecture

### Where AI helped
AI helped draft provider adapter interfaces, model catalog documentation, and
factory patterns for selecting STT and TTS providers from a spec string.

### Where AI was confidently wrong and had to be fixed
AI tended to assume a provider dropdown was enough. That would not prove model
comparison unless the selected spec actually changed the backend execution path.
AI also sometimes suggested silent fallbacks, which would have made benchmark
results misleading.

### What I decided myself
I required strict `provider:model[:voice]` specs and rejected silent fallback.
If a provider or model is unknown or not configured, the system must report that
clearly. I also decided to persist model provenance on every benchmark run so a
stored result can be interpreted later.

## 5. Phase 4 - Benchmark Execution

### Where AI helped
AI helped scaffold benchmark scripts, test-case metadata handling, result
serialization, and documentation tables showing representative runs.

### Where AI was confidently wrong and had to be fixed
AI-generated code and explanations initially risked treating different input
files as comparable across pipelines. That would have made model comparisons
unfair because audio length, phrasing, and noise affect latency.

### What I decided myself
I decided that fair comparison requires the same WAV files across pipelines. I
also required append-only runs, unique run IDs, experiment IDs for matrix runs,
and preserved failures instead of deleting unsuccessful provider calls.

## 6. Phase 5 - Latency Measurement

### Where AI helped
AI helped draft timing instrumentation, waterfall display ideas, and tests that
recompute timing from stored events.

### Where AI was confidently wrong and had to be fixed
An early AI-assisted draft computed or described TTFA from the wrong boundary,
such as TTS-internal timing or completion timing. That was confidently wrong
because TTFA must start when user input ends and stop when first output audio is
available.

### What I decided myself
I fixed the definition around `INPUT_END` and `FIRST_AUDIO`, then kept separate
fields for STT latency, LLM first token, tool latency, TTS first audio, TTS
completion, TTFA, and total response latency. I also decided not to invent
sub-metrics when a provider API does not expose them.

## 7. Phase 6 - Compare Models

### Where AI helped
AI helped organize model comparison reports, summarize provider/model
combinations, and create tables for STT, LLM, TTS, and pipeline variants.

### Where AI was confidently wrong and had to be fixed
AI initially tended to phrase comparisons like "best model" or universal
rankings. That was not supported by the measured sample sizes or provider quota
conditions.

### What I decided myself
I reported comparisons as observed results, not universal rankings. I kept
limitations visible: ElevenLabs quota exhaustion, OpenAI TTS not configured,
hosted Whisper cold starts, and Gemini's observed weather-tool over-call.

## 8. Phase 7 - Reliability Testing

### Where AI helped
AI helped create reliability checklists, failure tables, and safe-error handling
ideas for bad credentials, malformed audio, provider timeouts, quota failures,
and invalid uploads.

### Where AI was confidently wrong and had to be fixed
AI sometimes treated provider failures as exceptions to hide or retry. It also
underestimated how unhelpful some raw errors are, such as timeout exceptions
that stringify to an empty message.

### What I decided myself
I decided failures should be first-class benchmark data. The system records
failure stage, error type, safe message, and run provenance. I also chose not to
use automatic provider retries because retries would blur measured latency and
could duplicate paid API calls.

## 9. Phase 8 - Basic Concurrency Test

### Where AI helped
AI helped draft concurrency wave execution, result summaries, and documentation
for mock and real-provider concurrency runs.

### Where AI was confidently wrong and had to be fixed
AI could have presented unexecuted real-stack level-10 concurrency as if it had
been validated. That was corrected: level 10 is available in the UI/control
path but not claimed as a real paid-provider result.

### What I decided myself
I decided to cap concurrency, use bounded wave execution, and distinguish mock
results from paid-provider results. I also preserved the finding that real-stack
level 5 increased p95 TTFA compared with solo execution.

## 10. Phase 9 - Frontend / Observability Dashboard

### Where AI helped
AI helped build and refine dashboard views: benchmark configuration, live run,
results, comparison, reliability, concurrency, and screenshots for evidence.

### Where AI was confidently wrong and had to be fixed
AI proposed some presentation patterns that looked like a generic AI landing
page: decorative gradients, fake polish, and overly broad explanatory copy.
Those choices did not fit an observability console.

### What I decided myself
I chose an engineering-console style: dense measured values, stage events,
monospace identifiers, restrained colors, and evidence-focused screenshots. I
also decided the dashboard should show failed runs and unavailable providers
honestly rather than hiding them.

## 11. Phase 10 - Final Summary

### Where AI helped
AI helped gather project evidence into a final summary: delivered system,
engineering decisions, measured findings, reliability findings, concurrency
findings, security/cost controls, known limitations, and future work.

### Where AI was confidently wrong and had to be fixed
AI initially drifted toward polished but generic summary language. Phrases that
made the project sound more complete than the evidence supported were removed.

### What I decided myself
I kept the final summary factual. The system is strong because it measures and
documents real behavior, not because it claims every provider always works. I
kept the known limitations in the report instead of hiding them.

## 12. Phase 11 - Source Code ZIP / Git Link

### Where AI helped
AI helped prepare the repository documentation and identify the correct GitHub
source-code link.

### Where AI was confidently wrong and had to be fixed
AI initially mentioned the absence of a ZIP too prominently in the report. The
assignment allows a source link, so that wording distracted from the valid
GitHub submission.

### What I decided myself
I decided to provide the GitHub repository as the source-code link:

```text
https://github.com/Sudesh-chandra/VoiceAgentDashboard.git
```

I did not fabricate a ZIP filename.

## 13. Tool Calling and LangGraph Execution

### Where AI helped
AI helped draft LangGraph integration, tool-call event recording, and tests for
zero-tool and one-tool cases.

### Where AI was confidently wrong and had to be fixed
The mock intent detector initially inspected too much of the message context and
matched the system prompt instead of only the human query. That caused no-tool
queries to call the weather tool.

### What I decided myself
I fixed the logic so tool expectations are tied to the user query and test-case
metadata. I required tests for exactly zero tool calls and exactly one tool call
where expected, because tool use directly affects latency and correctness.

## 14. LangSmith / Trace Observability

### Where AI helped
AI helped wire trace metadata, draft privacy explanations, and document how run
IDs, experiment IDs, and trace IDs relate.

### Where AI was confidently wrong and had to be fixed
AI-generated documentation initially risked overstating trace evidence. In the
report pass, a placeholder trace figure was removed rather than treated as
evidence when the referenced artifact was not available in that context.

### What I decided myself
I kept LangSmith as an optional observability path and defaulted transcript
privacy toward hashed values unless explicitly configured otherwise. I also
decided not to fake trace screenshots or URLs.

## 15. Security Engineering

### Where AI helped
AI helped draft security checklists, input-validation tests, upload controls,
secret-scan steps, and documentation of out-of-scope items.

### Where AI was confidently wrong and had to be fixed
AI-assisted code originally used a path-prefix style check that could be unsafe
for path traversal. That was fixed by using safer path resolution logic. AI also
sometimes assumed secrets could be summarized casually, so secret exposure
checks were made explicit.

### What I decided myself
I required `.env` to stay ignored, provider keys to remain server-side, uploads
to be size/type/WAV validated, transcripts to be privacy-protected by default,
and missing authentication/rate limiting to be documented as a limitation rather
than hidden.

## 16. Cost and Token Governance

### Where AI helped
AI helped identify cost-control mechanisms such as run caps, concurrency caps,
token capture, and avoiding automatic retries.

### Where AI was confidently wrong and had to be fixed
AI initially read token usage from the wrong LangChain field in one pass, which
would have stored null token values. Live runs exposed the mismatch, and the
code was corrected to use the provider-reported usage metadata.

### What I decided myself
I decided not to invent dollar-cost estimates when provider pricing and usage
coverage were incomplete. The system stores usage primitives, token counts when
available, and run/concurrency caps so cost can be reasoned about honestly.

## 17. Multi-Model Provider Expansion

### Where AI helped
AI helped add and document multiple STT/TTS options, provider availability
checks, and the provider-test panel.

### Where AI was confidently wrong and had to be fixed
AI suggestions sometimes assumed provider APIs behaved similarly. In practice,
Deepgram, ElevenLabs, OpenRouter, and unconfigured OpenAI TTS had different
availability, timeout, quota, and response behaviors.

### What I decided myself
I required each provider/model to carry its own measured status and limitation.
I kept quota/auth failures as real evidence and did not collapse them into a
generic "provider unavailable" success path.

## 18. Debugging and Corrections

### Where AI helped
AI helped search the codebase, propose likely failure points, and draft tests
after bugs were found.

### Where AI was confidently wrong and had to be fixed
Specific corrections included the TTFA boundary, mock tool-call behavior, path
traversal validation, upload metadata handling, `.env` path resolution,
Deepgram WAV container handling, retired model IDs, token-usage capture, empty
timeout messages, process-shared key rotation, stale backend state during UI
verification, and trace metadata synchronization.

### What I decided myself
I treated debugging as evidence collection. When observed behavior contradicted
AI-generated assumptions, I trusted the run output, tests, logs, and provider
responses over the draft explanation.

## 19. Final Packaging and GitHub Submission

### Where AI helped
AI helped read the packaging prompt, check repository status, prepare report
files, verify ignored files, run tests/builds where available, and summarize
submission state.

### Where AI was confidently wrong and had to be fixed
AI initially assumed a bundled PDF operation marker script and a system LaTeX
compiler would be available. They were not available in this workspace, so the
workflow was corrected: the report keeps a `.tex` companion source, while the
PDF is generated locally using ReportLab.

### What I decided myself
I kept secrets, local databases, CDP/browser profiles, caches, dependencies,
and generated local-only report artifacts out of GitHub. I also decided not to
push `docs/report/` after the clarification that it should remain local.

## 20. Cleanup After Submission Review

### Where AI helped
AI helped inspect which artifacts were tracked versus ignored and identify the
difference between local report files and files intended for GitHub.

### Where AI was confidently wrong and had to be fixed
AI initially treated the generated report folder as part of the submission
package because the earlier prompt asked for a technical report. The user later
clarified that the report folder should exist locally but not be pushed.

### What I decided myself
I kept cleanup narrow: remove unneeded submitted artifacts from GitHub while
preserving local report files, benchmark evidence, screenshots, documentation,
and source code.

## 21. Technical Report Content Pass

### Where AI helped
AI helped audit the report against repository evidence, generate a content audit
file, organize the required phases, and produce report tables and figures.

### Where AI was confidently wrong and had to be fixed
AI initially produced report structure that was too close to an assignment
checklist and included wording that did not belong, such as institution/report
labels requested to be removed.

### What I decided myself
I decided the report must not invent missing measurements. It should preserve
limitations such as quota exhaustion, OpenAI TTS not configured, hosted Whisper
cold starts, buffered first-audio semantics, and no real-stack level-10 claim.

## 22. Technical Report Layout and PDF Design Pass

### Where AI helped
AI helped rebuild the local PDF, create generated figures, render pages for
inspection, and check extracted PDF text for required/forbidden phrases.

### Where AI was confidently wrong and had to be fixed
AI initially allowed layout issues such as dense tables, placeholder evidence,
awkward page breaks, and repeated caption phrases. Visual QA caught these
issues, and the report was rebuilt with readable tables and cleaner captions.

### What I decided myself
I chose a restrained professional style: no decorative gradients, no marketing
phrases, no fake screenshots, no "best model" claims, and no document-type
wording the user did not request.

## 23. Final Report Layout and Anti-Slop Pass

### Where AI helped
AI helped read the strict layout brief, rebuild the report generator, shorten
table cells, regenerate the architecture and latency diagrams, compile the PDF,
render all pages, and create `docs/report/REPORT_VISUAL_QA.md`.

### Where AI was confidently wrong and had to be fixed
The first reflow still split two tables awkwardly across pages and the QA note
quoted a removed report label while describing the fix. Those were corrected:
tables were kept together, screenshot height was adjusted, and the forbidden
wording was removed from the report package.

### What I decided myself
I kept verified measurements unchanged, kept the report local-only, limited
screenshots to distinct evidence, used the GitHub link instead of fabricating a
ZIP, and made the final document look like engineering documentation rather
than a generated brochure.

## 24. Final Ownership Statement

### Where AI helped
AI helped with speed: drafting, searching, scaffolding, report generation, and
iteration.

### Where AI was confidently wrong and had to be fixed
AI was most useful after its confident mistakes were challenged: wrong metric
boundaries, generic architecture suggestions, overstated report language, and
layout choices that looked polished but were not readable.

### What I decided myself
The engineering ownership remained mine. I chose the metric, rejected weak
architecture suggestions, corrected wrong code paths, validated behavior with
tests and live runs, preserved failures as evidence, and kept the final claims
bounded by what the repository actually proves.
