# Security Audit — Voice Agent Latency & Reliability Observatory

Date: 2026-09-22. Method: manual source-code security review + executed attack
probes. The Cloudflare security-audit skill is NOT available in this
environment; nothing below pretends otherwise.

## Secrets

- All provider keys load from `.env` (gitignored — verified in `.gitignore`).
- Repo-wide scan for key-shaped strings (`sk-…`, key fragments, `Token <hex>`)
  across `backend/app`, `frontend/src`, built `dist/assets/*.js`, docs: **clean**.
- `/api/config` and `/api/availability` responses asserted key-free by an
  executed check against live env values (test: `test_no_secrets_in_config_endpoint`
  + a manual scan of every env secret against the response body).
- Frontend has zero `import.meta.env` secret usage (only the two documented
  `VITE_API_BASE_URL`/`VITE_WS_URL` build-time endpoints exist in `.env.example`).
- Errors are provider-safe strings, truncated; chaos tests confirmed no key
  material in any error path (bad-credential probe leaked nothing).

## File upload security (verified by tests, `test_security_and_api.py`)

- Non-wav rejected (magic bytes) — `MZ` header → 400.
- Wav-extension spoof with garbage content → 400.
- Oversize → 413 with streaming size cap (no full-buffer DoS).
- Path traversal via crafted ids blocked by strict `Path.relative_to`
  sandboxing (`resolve_audio_path`); traversal probe → 404/422.
- Filenames sanitized (`[^A-Za-z0-9._-]` → `_`), storage sandboxed under
  `data/test_cases/`.
- No command execution anywhere in the upload path; uploads are never executed.

## API security

- CORS: restricted origin allowlist (localhost dev defaults), credentials
  disabled, methods limited to GET/POST.
- Input validation: Pydantic models with regex-validated model specs; unknown
  providers/models → 409 via factory allowlist (never silently mapped);
  malformed specs → 422. LLM choices whitelist-validated.
- SQL injection: parameterized queries only (sqlite3 `?` placeholders); no
  string-built SQL with user data.
- SSRF: the only outbound fetches target fixed provider URLs; the weather tool
  geocodes via fixed Open-Meteo endpoints with the city as a query param —
  no user-controlled base URL.
- No auth by design (single-user, localhost tool) — documented as an accepted
  limitation; server binds `BACKEND_HOST` (default 0.0.0.0 — recommend 127.0.0.1
  when exposing beyond localhost).

## AI-specific security

- Prompt injection: transcripts (user audio content) and tool outputs enter the
  LLM as data; the system prompt instructs one-sentence answers; tools are
  read-only lookups (weather/search) with no destructive capability, so tool
  injection cannot escalate privileges. Tool outputs are truncated (search
  snippets ≤200 chars) before entering context.
- Data exfiltration: no URL-fetching tool exists; the agent cannot be coerced
  into arbitrary network calls.
- LangSmith privacy: transcripts/responses cross the trace boundary only as
  sha256 prefixes unless `LANGSMITH_SEND_TRANSCRIPTS=true` (verified by test);
  audio bytes never leave the server.

## Logging security

- `RedactingFilter` redacts `sk-*`, `dsk-*`, `api_key=/token=/secret=` patterns
  from every log record (unit-tested).
- No raw audio is ever logged; benchmark events carry metadata only.

## Dependency security

- `requirements.txt` reviewed: httpx, fastapi, pydantic-settings, langchain-core,
  langgraph, langchain-openai, langsmith, numpy — all directly used; no unused
  or duplicated deps introduced. No blind upgrades performed during the audit
  (deliberate: stability over patch churn at release time).

## Findings summary

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| 1 | No authentication on the API (single-user localhost tool) | Low (accepted) | Documented |
| 2 | Default bind 0.0.0.0 exposes the API on LAN | Low | Documented; set BACKEND_HOST=127.0.0.1 to restrict |
| 3 | Live-session registry is in-memory | Info | Accepted for single-instance deployment |
| 4 | Provider quota errors include provider message text (truncated 200–300 chars) | Info | Safe — no key material; truncation bounds leakage |
