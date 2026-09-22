"""Register evaluator-supplied WAVs as test cases.

Usage:
    python scripts/seed_test_cases.py path/to/six/wav/files/ [--as test_0X]

Convention reminder: ~1s silence + speech + ~1s silence is recommended but not
required — the platform measures leading silence and reports it; TTFA never
depends on it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import TEST_CASES_DIR  # noqa: E402
from app.registry import upsert_metadata, _load_meta  # noqa: E402

DEFAULTS = [
    ("test_01", "Spoken question — direct answer", 0, "none"),
    ("test_02", "Spoken question — direct answer", 0, "none two"),
    ("test_03", "Weather query — one tool call", 1, "none"),
    ("test_04", "Search query — one tool call", 1, "none"),
    ("test_05", "Spoken query with background noise", 1, "tv"),
    ("test_06", "Spoken query with background noise", 0, "white_noise"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", help="directory containing the six .wav files")
    ap.add_argument("--names", default="test_01.wav,test_02.wav,test_03.wav,test_04.wav,test_05.wav,test_06.wav",
                    help="comma-separated wav filenames in order")
    args = ap.parse_args()

    src = Path(args.dir)
    if not src.is_dir():
        print(f"not a directory: {src}")
        return 1

    names = [n.strip() for n in args.names.split(",") if n.strip()]
    for name, (tc_id, label, tools, noise) in zip(names, DEFAULTS):
        p = src / name
        if not p.exists():
            print(f"missing: {p}")
            return 1
        data = p.read_bytes()
        if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            print(f"not a wav: {p}")
            return 1
        (TEST_CASES_DIR / name).write_bytes(data)
        upsert_metadata({"id": tc_id, "name": label, "description": label,
                         "expected_tool_calls": tools, "noise_type": noise, "audio_path": name})
        print(f"registered {tc_id} <- {name}")

    print(json.dumps(_load_meta(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
