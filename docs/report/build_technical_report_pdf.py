"""Build technical_report.pdf when a LaTeX engine is unavailable.

The canonical report source is technical_report.tex. This builder mirrors the
same evidence and section structure using ReportLab so the repository contains
the requested PDF artifact in environments without pdflatex/xelatex.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "technical_report.pdf"
FIG = ROOT / "figures"


def styles():
    base = getSampleStyleSheet()
    base["Title"].fontName = "Helvetica-Bold"
    base["Title"].fontSize = 22
    base["Title"].leading = 27
    base["Heading1"].fontName = "Helvetica-Bold"
    base["Heading1"].fontSize = 15
    base["Heading1"].leading = 19
    base["Heading2"].fontName = "Helvetica-Bold"
    base["Heading2"].fontSize = 12
    base["Heading2"].leading = 15
    base["BodyText"].fontSize = 9.5
    base["BodyText"].leading = 12.5
    base.add(ParagraphStyle("Center", parent=base["BodyText"], alignment=TA_CENTER))
    base.add(ParagraphStyle("Small", parent=base["BodyText"], fontSize=8, leading=10))
    return base


S = styles()


def page(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#475569"))
    canvas.drawString(inch, A4[1] - 0.55 * inch, "Voice Agent Latency and Reliability Observatory")
    canvas.drawRightString(A4[0] - inch, 0.55 * inch, str(doc.page))
    canvas.restoreState()


def p(text, style="BodyText"):
    return Paragraph(text, S[style])


def bullets(items):
    return ListFlowable([ListItem(p(item), leftIndent=10) for item in items], bulletType="bullet")


def table(rows, widths):
    t = Table(rows, colWidths=widths, hAlign="LEFT", repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEADING", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
    ]))
    return t


def image(path, caption, width=6.4 * inch):
    full = FIG / path
    if not full.exists():
        return [p(f"Figure unavailable: {path}", "Small")]
    img = Image(str(full))
    ratio = img.imageHeight / float(img.imageWidth)
    img.drawWidth = width
    img.drawHeight = width * ratio
    return [img, Spacer(1, 4), p(f"<b>Figure.</b> {caption}", "Small"), Spacer(1, 10)]


def heading(title):
    return [Spacer(1, 8), p(title, "Heading1"), Spacer(1, 4)]


story = []

story += [
    Spacer(1, 1.2 * inch),
    p("Voice Agent Latency & Reliability Observatory", "Title"),
    Spacer(1, 12),
    p("Benchmarking, Latency Measurement, Model Comparison and Observability for AI Voice Pipelines", "Center"),
    Spacer(1, 22),
    p("Author: ValikalaChandra", "Center"),
    p("Institution: Not documented in the repository", "Center"),
    p("Date: September 2026", "Center"),
    Spacer(1, 40),
    p("This report summarizes the final repository state. It is based on the checked-in implementation, stored benchmark artifacts, screenshots, audit reports, and test documentation. It does not invent missing measurements."),
    PageBreak(),
]

story += heading("Phase 1 - Understand and Define the Problem")
story += [
    p("The project is a controlled observatory for AI voice-agent pipelines. A recorded WAV file is processed by STT, passed into a LangGraph agent and LLM, optionally routed through a tool, and converted back to speech through TTS."),
    p("The headline metric is TTFA = timestamp(first output audio) - timestamp(end of input audio). Request start, upload start, LLM first token, and TTS completion are different boundaries and are not substituted for TTFA."),
]

story += heading("Phase 2 - Design the Architecture")
story += image("architecture.png", "Architecture overview: WAV input, provider layers, LangGraph, optional tools, TTS, storage, dashboard, and LangSmith observability.")
story += [
    bullets([
        "Frontend: React/Vite dashboard for test cases, configuration, live runs, result detail, comparison, reliability, and concurrency.",
        "Backend: FastAPI REST/WebSocket API, provider availability, benchmark orchestration, exports, and concurrency tests.",
        "Pipeline runner: single timing authority for INPUT_END, STT, LangGraph/LLM, tools, TTS, FIRST_AUDIO, and persistence.",
        "Storage and observability: SQLite rows, JSON artifacts, live WebSocket events, screenshots, and optional LangSmith traces.",
    ])
]

story += heading("Phase 3 - Model Plug-in Architecture")
story += image("pipeline.png", "Measured pipeline flow with TTFA anchored at INPUT_END and stopped at the first emitted audio bytes.")
story += [
    table([
        ["Stage", "Provider", "Models", "Status"],
        ["STT", "Deepgram", "nova-2, nova-3, hosted whisper-large", "Verified"],
        ["STT", "ElevenLabs", "scribe_v1", "Verified earlier; quota/auth limited later"],
        ["TTS", "Deepgram", "aura-2-thalia-en, aura-2-andromeda-en", "Verified"],
        ["TTS", "ElevenLabs", "turbo, multilingual, flash", "Verified earlier; quota limited later"],
        ["TTS", "OpenAI", "tts-1", "NOT_CONFIGURED; not faked"],
        ["LLM", "OpenRouter", "configured chat model", "Verified"],
    ], [0.75 * inch, 1.0 * inch, 2.8 * inch, 1.55 * inch]),
    p("Selections use provider:model[:voice] specs, are validated against allowlists, and are persisted per run. Missing keys mark providers unavailable rather than falling back silently."),
]

story += heading("Phase 4 - Benchmark Execution")
story += [
    table([
        ["Case", "Description", "Expected tools", "Noise"],
        ["TC01", "Direct spoken query", "0", "none"],
        ["TC02", "Direct spoken query", "0", "none"],
        ["TC03", "Weather query", "1", "none"],
        ["TC04", "Web search query", "1", "none"],
        ["TC05", "Noisy knowledge question", "0", "environmental"],
        ["TC06", "Noisy direct query", "0", "environmental"],
    ], [0.6 * inch, 3.1 * inch, 1.0 * inch, 1.2 * inch]),
    p("The repository contains 415 benchmark JSON artifacts. Some named final experiment aggregate files are not present as standalone EXP-FINAL*.json files; raw per-run artifacts remain under docs/benchmark-results/."),
]

story += heading("Phase 5 - Latency Measurement")
story += image("latency-waterfall.png", "Latency waterfall screenshot showing stored stage events and FIRST_AUDIO timing.")
story += [
    bullets([
        "STT latency: provider call start to transcript ready.",
        "LLM TTFT/completion: LangGraph agent start to first token/final response.",
        "Tool latency: measured tool body execution.",
        "TTS first-audio latency: TTS start to first emitted audio chunk.",
        "TTFA: INPUT_END to FIRST_AUDIO.",
        "Total response latency: INPUT_END to TTS completion.",
    ])
]

story += heading("Phase 6 - Compare Models")
story += [
    table([
        ["STT", "LLM", "TTS", "Test", "TTFA"],
        ["Deepgram nova-2", "gpt-4.1-mini", "Aura Thalia", "TC01", "9,582 ms"],
        ["Deepgram nova-3", "gpt-4.1-mini", "Aura Thalia", "TC01", "8,811 ms"],
        ["Deepgram whisper-large", "gpt-4.1-mini", "Aura Thalia", "TC05", "8,910 ms"],
        ["Deepgram nova-3", "gpt-4.1-mini", "Aura Andromeda", "TC01", "7,474 ms"],
        ["Deepgram nova-2", "gpt-4.1-mini", "ElevenLabs turbo", "TC01", "TTS quota failure"],
    ], [1.45 * inch, 1.2 * inch, 1.35 * inch, 0.55 * inch, 1.0 * inch]),
]
story += image("screenshots/model-comparison.png", "Model comparison dashboard using stored run counts and latency metrics.")
story += [p("The earlier EXP-2026-00003 report measured gpt-4.1-mini median TTFA 6,093 ms over 12 runs and gemini-3.8-flash median TTFA 6,945 ms over 12 runs with shared STT/TTS. It also exposed gemini weather-tool over-calling on TC03.")]

story += heading("Phase 7 - Reliability Testing")
story += image("screenshots/reliability-summary.png", "Reliability dashboard with stage-tagged failure counters.")
story += [p("Reliability testing covers provider timeouts, quota failures, invalid audio, tool behavior, LLM failures, TTS failures, and safe error visibility. Failures retain stage, error type, safe detail, provider specs, and timestamps.")]

story += heading("Phase 8 - Basic Concurrency Test")
story += [
    table([
        ["Level", "Success", "TTFA median", "Throughput"],
        ["1 real stack", "2/2", "5,913 ms", "0.30 rps"],
        ["5 real stack", "10/10", "6,896 ms", "1.16 rps"],
        ["10 real stack", "Not claimed", "Not available in captured run", "Not available"],
    ], [1.2 * inch, 1.2 * inch, 1.7 * inch, 1.4 * inch])
]
story += image("screenshots/concurrency-panel.png", "Concurrency panel with capped controls to protect provider quotas.")

story += heading("Phase 9 - Frontend / Observability Dashboard")
story += image("screenshots/dashboard-overview.png", "Dashboard overview emphasizing operational status and recent evidence.")
story += image("screenshots/pipeline-configuration.png", "Pipeline configuration screen with executable provider/model specs.")

story += heading("Phase 10 - Final Summary")
story += [p("The repository contains a working voice-agent observatory with configurable STT, LLM, and TTS stages; event-level instrumentation; TTFA measurement from INPUT_END to FIRST_AUDIO; LangGraph execution; optional tool timing; LangSmith trace IDs; SQLite persistence; model comparison; reliability and concurrency views; security controls; and audit documentation.")]
story += [p("Known limitations are explicit: ElevenLabs free-tier quota exhaustion after earlier verification, OpenAI TTS not configured, hosted whisper cold starts, buffered first-audio semantics for current working TTS providers, no auth for the local dashboard, and no claimed real-stack level-10 concurrency run.")]

story += heading("Phase 11 - Source Code")
story += [
    p("Repository URL: https://github.com/Sudesh-chandra/VoiceAgentDashboard.git"),
    bullets([
        "backend/: FastAPI API, pipeline runner, provider adapters, LangGraph agent, storage, tests, and scripts.",
        "frontend/: React/Vite dashboard.",
        "data/test_cases/: six imported WAV test cases and metadata.",
        "docs/: architecture, assumptions, model reports, audit reports, benchmark artifacts, and this report.",
        "screenshots/: captured dashboard evidence.",
        "AI_USAGE.md: AI assistance and engineering decision log.",
    ]),
    p("Reproduction summary: configure .env from .env.example without committing secrets; run backend tests in MOCK_MODE; start FastAPI and Vite; run benchmarks through the dashboard or backend/benchmark/run_matrix.py."),
]


doc = SimpleDocTemplate(
    str(OUT),
    pagesize=A4,
    leftMargin=0.65 * inch,
    rightMargin=0.65 * inch,
    topMargin=0.8 * inch,
    bottomMargin=0.75 * inch,
    title="Voice Agent Latency and Reliability Observatory",
)
doc.build(story, onFirstPage=page, onLaterPages=page)
print(OUT)
