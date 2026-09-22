"""LangSmith wiring (§7/§9): real nested trace tree per interaction.

Hierarchy (LangSmith project `voice-agent-benchmark`):

    voice_session (chain, @traceable root)
      ├── stt           (tool, @traceable)     inputs hashed unless opted-in
      ├── agent         (chain, @traceable)    → LangGraph/LLM runs nest INSIDE
      │                    automatically because they execute in this context
      └── tts           (tool, @traceable)

Because `create_react_agent`'s runs execute inside the `agent` traceable context,
LangSmith nests the graph's model/tool runs under it — the graph is genuinely
visible in the trace (verified end-to-end during the engineering audit).

Privacy (§17): transcript/response text crosses the trace boundary only as
sha256 prefixes unless LANGSMITH_SEND_TRANSCRIPTS=true. Audio bytes are NEVER
sent. No API keys or secrets are ever placed in spans.

The root run id is captured into `res.trace_id` so each BenchmarkRow links to the
exact LangSmith trace.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from ..core.config import get_settings


def _maybe_text(text: str | None) -> str | None:
    """Hash text unless full transcripts are explicitly enabled."""
    if not text:
        return None
    if get_settings().langsmith_send_transcripts:
        return text
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}"


def _enabled() -> bool:
    s = get_settings()
    return bool(s.langsmith_tracing and s.langsmith_api_key)


class _NullCtx:
    """No-op stand-in when LangSmith is disabled (platform still runs)."""

    def __enter__(self):
        return {}

    def __exit__(self, *exc):
        return False


def trace_pipeline_run(res, config, events: list):
    """Context manager for the voice_session root run.

    Implemented with langsmith.tracing.tracing_context + RunTree so we control
    inputs/outputs and capture the run id. Falls back to a no-op when disabled.
    """
    if not _enabled():
        return _NullCtx()

    from langsmith.client import Client
    from langsmith.run_trees import RunTree
    from langsmith import utils as ls_utils
    from langsmith.run_helpers import tracing_context

    client = Client(api_key=get_settings().langsmith_api_key)
    project = get_settings().langsmith_project

    # Root span: voice_session
    root = RunTree(
        name="voice_session",
        run_type="chain",
        inputs={
            "test_case": res.test_case_id,
            "pipeline": config.pipeline_id,
            "stt": config.stt,
            "llm": config.llm,
            "tts": config.tts,
            "tool": config.tool,
        },
        client=client,
        project_name=project,
        id=uuid.uuid4(),
    )
    root.post()
    # Make the root the ambient tracing parent so LangChain/LangGraph runs created
    # inside (ChatOpenAI, agent nodes) nest under this trace automatically.
    tc = tracing_context(parent=root, project_name=project, client=client, enabled=True)
    tc.__enter__()

    class _RootCtx:
        def __init__(self):
            self.run_id = str(root.id)
            self.root = root
            self._children: list[RunTree] = []

        def child(self, name: str, run_type: str, inputs: dict | None = None) -> RunTree:
            safe_in = {k: (_maybe_text(v) if isinstance(v, str) else v) for k, v in (inputs or {}).items()}
            # create_child() derives dotted_order/trace_id correctly; hand-building a
            # RunTree with parent_run_id is rejected by the ingest API (400).
            t = root.create_child(name=name, run_type=run_type, inputs=safe_in or {})
            t.post()
            self._children.append(t)
            return t

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            t = res.timings
            outputs = {
                "success": res.success,
                "time_to_first_audio_ms": t.time_to_first_audio_ms,
                "stt_latency_ms": t.stt_latency_ms,
                "stt_first_result_ms": t.stt_first_result_ms,
                "llm_ttft_ms": t.llm_ttft_ms,
                "tool_latency_ms": t.tool_latency_ms,
                "tool_calls": [tc["name"] for tc in res.tool_calls],
                "tts_first_audio_ms": t.tts_first_audio_ms,
                "tts_completion_ms": t.tts_completion_ms,
                "total_response_latency_ms": t.total_response_latency_ms,
                "transcript": _maybe_text(res.transcript),
                "response_text": _maybe_text(res.response_text),
            }
            if exc_type is not None:
                outputs["success"] = False
                outputs["error"] = f"{exc_type.__name__}: {exc}"[:300]
                root.end(error=str(exc)[:300], outputs=outputs)
            else:
                root.end(outputs=outputs)
            root.patch()
            tc.__exit__(None, None, None)  # pop the ambient tracing parent
            return False

    ctx = _RootCtx()
    res.trace_id = ctx.run_id
    return ctx
