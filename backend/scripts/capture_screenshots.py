"""Real screenshot capture of the running application via headless Edge/Chrome
CDP (Page.captureScreenshot). No mocked content — every capture is the live UI
rendering real backend data.

Usage: python scripts/capture_screenshots.py <browser-exe> <out-root>
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

APP = "http://localhost:5173"
WIDTH = 1440
HEIGHT = 900


class CDP:
    def __init__(self, ws_url: str) -> None:
        import websockets.sync.client as wsc

        self._ws = wsc.connect(ws_url, max_size=50 * 1024 * 1024)
        self._id = 0

    def call(self, method: str, **params):
        self._id += 1
        mid = self._id
        self._ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self._ws.recv(timeout=60))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def evaluate(self, expr: str):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=True,
                      returnByValue=True)
        return r.get("result", {}).get("value")

    def close(self):
        try:
            self._ws.close()
        except Exception:
            pass


def launch_browser(exe: str, port: int):
    proc = subprocess.Popen([
        exe,
        f"--remote-debugging-port={port}",
        "--user-data-dir=" + str(Path(BACKEND_DIR).parent / "data" / "cdp-profile"),
        "--headless=new",
        "--disable-gpu",
        f"--window-size={WIDTH},{HEIGHT}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # wait for the CDP endpoint
    for _ in range(50):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
                json.loads(r.read())
            return proc
        except Exception:
            time.sleep(0.3)
    raise RuntimeError("browser did not expose CDP")


def page_ws_url(port: int) -> str:
    """Return the webSocketDebuggerUrl of the page target (browser-level WS
    lacks Page domain methods)."""
    for _ in range(50):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1) as r:
                targets = json.loads(r.read())
            pages = [t for t in targets if t.get("type") == "page"]
            if pages:
                return pages[0]["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.3)
    raise RuntimeError("no page target found")


def settle(c: CDP, ms: int = 1200):
    time.sleep(ms / 1000)


def wait_rendered(c: CDP, marker: str, timeout_s: int = 20):
    """Wait until the app has actually rendered content containing `marker`
    (guards against capturing blank pre-hydration frames)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if c.evaluate(f"document.body.innerText.includes({marker!r})"):
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def shot(c: CDP, out: Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    data = c.call("Page.captureScreenshot", format="png")
    out.write_bytes(base64.b64decode(data["data"]))
    print("captured", out.relative_to(out.parents[2]))


def scroll_bottom(c: CDP):
    c.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    settle(c, 500)


def main() -> int:
    exe = sys.argv[1]
    out_root = Path(sys.argv[2])
    port = 9333
    proc = launch_browser(exe, port)
    ws = page_ws_url(port)
    root = out_root
    try:
        c = CDP(ws)
        c.call("Page.enable")
        c.call("Runtime.enable")
        c.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT,
               deviceScaleFactor=1.5, mobile=False)

        # 01 — dashboard overview (benchmark tab, pipeline config)
        c.call("Page.navigate", url=APP)
        wait_rendered(c, "Test cases")
        settle(c, 2500)  # let config/cases data fill in
        shot(c, root / "01-dashboard" / "dashboard-overview.png")
        shot(c, root / "03-pipeline-configuration" / "pipeline-configuration.png")
        scroll_bottom(c)
        shot(c, root / "02-test-cases" / "test-cases-list.png")
        c.evaluate("window.scrollTo(0, 0)")
        settle(c, 300)

        # 04 — provider test section (deepgram nova-3 STT + aura TTS)
        c.evaluate("""(async () => {
          const setNative = (el, v) => {
            const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
            setter.call(el, v);
            el.dispatchEvent(new Event('change', { bubbles: true }));
          };
          const sel = document.getElementById('pt-stt');
          if (sel) setNative(sel, 'deepgram:nova-3');
          const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Run STT test');
          if (btn) btn.click();
          return 'ok';
        })()""")
        settle(c, 6000)
        scroll_bottom(c)
        shot(c, root / "03-pipeline-configuration" / "provider-test-stt-result.png")

        # 05 — live benchmark with real event progression (nova-3, test_01)
        c.evaluate("""(async () => {
          const tabs = Array.from(document.querySelectorAll('[role=tab]'));
          tabs.find(t => t.textContent === 'Live Run').click();
          return 'tab';
        })()""")
        settle(c, 800)
        c.evaluate("""(async () => {
          const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Run live');
          if (btn) btn.click();
          return 'started';
        })()""")
        # capture mid-run (STT/agent likely active) and at completion
        settle(c, 2500)
        shot(c, root / "04-live-benchmark" / "live-benchmark-running.png")
        for _ in range(60):
            val = c.evaluate("document.body.textContent.includes('Run complete') || document.body.textContent.includes('failed')")
            if val:
                break
            time.sleep(1)
        settle(c, 1200)
        shot(c, root / "04-live-benchmark" / "live-benchmark-complete.png")

        # 05b — latency waterfall from the completed live run
        shot(c, root / "05-latency-waterfall" / "latency-waterfall.png")

        # 06 — results table + open run detail
        c.evaluate("""(async () => {
          const tabs = Array.from(document.querySelectorAll('[role=tab]'));
          tabs.find(t => t.textContent === 'Results').click();
          return 'tab';
        })()""")
        settle(c, 1500)
        shot(c, root / "06-results" / "results-table.png")
        c.evaluate("""(async () => {
          const row = document.querySelector('tbody tr.rowlink');
          if (row) row.click();
          return 'opened';
        })()""")
        settle(c, 1200)
        shot(c, root / "06-results" / "benchmark-result-detail.png")
        scroll_bottom(c)
        shot(c, root / "06-results" / "benchmark-result-metadata.png")

        # 07 — model comparison
        c.evaluate("""(async () => {
          const tabs = Array.from(document.querySelectorAll('[role=tab]'));
          tabs.find(t => t.textContent === 'Compare').click();
          return 'tab';
        })()""")
        settle(c, 1500)
        shot(c, root / "07-model-comparison" / "model-comparison.png")

        # 08/09 — reliability + failures
        c.evaluate("""(async () => {
          const tabs = Array.from(document.querySelectorAll('[role=tab]'));
          tabs.find(t => t.textContent.includes('Reliability')).click();
          return 'tab';
        })()""")
        settle(c, 1500)
        shot(c, root / "08-reliability" / "reliability-summary.png")
        scroll_bottom(c)
        shot(c, root / "09-concurrency" / "concurrency-panel.png")

        # 11 — failure detail: open a failed run from Results
        c.evaluate("""(async () => {
          const tabs = Array.from(document.querySelectorAll('[role=tab]'));
          tabs.find(t => t.textContent === 'Results').click();
          return 'tab';
        })()""")
        settle(c, 1500)
        opened = c.evaluate("""(async () => {
          const failRow = Array.from(document.querySelectorAll('tbody tr.rowlink'))
            .find(r => r.textContent.includes('fail'));
          if (failRow) { failRow.click(); return 'opened-failed'; }
          return 'no-failed-run-visible';
        })()""")
        settle(c, 1200)
        shot(c, root / "11-failures" / f"failure-detail-{opened}.png")

        # 10 — LangSmith trace evidence: open a result detail and capture the
        # trace link / provenance block (no top-level Traces tab exists).
        c.evaluate("""(async () => {
          const tabs = Array.from(document.querySelectorAll('[role=tab]'));
          tabs.find(t => t.textContent === 'Results').click();
          return 'tab';
        })()""")
        settle(c, 1500)
        c.evaluate("""(async () => {
          const row = document.querySelector('tbody tr.rowlink');
          if (row) row.click();
          return 'opened';
        })()""")
        settle(c, 1200)
        opened_trace = c.evaluate("""(async () => {
          const el = Array.from(document.querySelectorAll('a, .mono, code, dd, td'))
            .find(e => /smith\.langchain|trace/i.test(e.textContent || ''));
          if (el) { el.scrollIntoView({block: 'center'}); return 'trace-visible'; }
          window.scrollTo(0, document.body.scrollHeight);
          return 'scrolled-bottom';
        })()""")
        settle(c, 500)
        shot(c, root / "10-langsmith-trace" / f"langsmith-trace-{opened_trace}.png")

        # 12 — responsive (tablet-ish width)
        c.call("Emulation.setDeviceMetricsOverride", width=768, height=1024,
               deviceScaleFactor=1.5, mobile=False)
        c.evaluate("""(async () => {
          const tabs = Array.from(document.querySelectorAll('[role=tab]'));
          tabs.find(t => t.textContent === 'Benchmark').click();
          return 'tab';
        })()""")
        settle(c, 1500)
        shot(c, root / "12-responsive" / "responsive-768-benchmark.png")
        c.call("Emulation.setDeviceMetricsOverride", width=390, height=844,
               deviceScaleFactor=2, mobile=True)
        settle(c, 1000)
        shot(c, root / "12-responsive" / "responsive-390-dashboard.png")

        c.close()
    finally:
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
