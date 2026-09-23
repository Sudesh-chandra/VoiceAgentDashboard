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
from datetime import datetime, timezone
from typing import Any

from ..core.config import get_settings

# Canonical UI coordinates for this project's traces, resolved once per process
# from GET /sessions (tenant_id + project id). LangSmith's web app routes
# traces under /o/<tenant>/projects/p/<project_id>/r/<run_id> — a tenant-less
# legacy URL gets redirected to whichever org the browser happens to have
# active, which 404s when that differs from the key's org (observed live).
_ui_coords: dict[str, str | None] = {"tenant": None, "project_id": None, "resolved": False}


def _ui_base(project: str, api_key: str | None = None) -> str | None:
    """Return the /o/<tenant>/projects/p/<id> prefix for canonical trace URLs,
    or None when it cannot be resolved (caller falls back to trace-id only)."""
    if _ui_coords["resolved"]:
        tenant, pid = _ui_coords["tenant"], _ui_coords["project_id"]
        return f"/o/{tenant}/projects/p/{pid}" if tenant and pid else None
    try:
        import httpx
        s = get_settings()
        r = httpx.get(
            f"{s.langsmith_endpoint}/api/v1/sessions",
            params={"limit": 1, "name": project},
            headers={"x-api-key": api_key or s.langsmith_api_key or ""},
            timeout=10.0,
        )
        rows = r.json() if r.status_code == 200 else []
        if rows and rows[0].get("tenant_id") and rows[0].get("id"):
            _ui_coords["tenant"] = rows[0]["tenant_id"]
            _ui_coords["project_id"] = rows[0]["id"]
    except Exception:
        pass
    _ui_coords["resolved"] = True
    tenant, pid = _ui_coords["tenant"], _ui_coords["project_id"]
    return f"/o/{tenant}/projects/p/{pid}" if tenant and pid else None


def canonical_trace_url(run_id: str, start_time: Any = None,
                        project: str | None = None) -> str | None:
    """Canonical smith.langchain.com URL for a run, replicating the `app_path`
    the LangSmith API itself returns for ingested runs. None = cannot resolve
    (caller keeps the stored trace id; the UI falls back to the legacy URL).

    `start_time` mirrors RunTree.start_time (a UTC datetime); the web app's
    query value is that timestamp rendered as ISO with microseconds, no offset.
    """
    if not _enabled():
        return None
    s = get_settings()
    project = project or s.langsmith_project
    base = _ui_base(project)
    if not base:
        return None
    url = f"https://smith.langchain.com{base}/r/{run_id}?trace_id={run_id}"
    if start_time is not None:
        try:
            if isinstance(start_time, datetime):
                dt = start_time.astimezone(timezone.utc)
            else:  # epoch milliseconds
                dt = datetime.fromtimestamp(float(start_time) / 1000, tz=timezone.utc)
            url += "&start_time=" + dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
        except Exception:
            pass  # URL stays valid without the disambiguator
    return url


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
    # Persist the canonical (tenant-scoped) UI URL so every stored run links to
    # the exact trace regardless of which org the viewer's browser defaults to.
    try:
        res.trace_url = canonical_trace_url(ctx.run_id, getattr(root, "start_time", None))
    except Exception:
        res.trace_url = None
    return ctx
