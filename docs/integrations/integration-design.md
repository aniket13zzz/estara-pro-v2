# Integration design

Every external service sits behind an adapter in `backend/app/providers/` (`llm`, `voice`,
`whatsapp`, `email`, `embeddings`). Business logic imports the adapter interface, never a vendor
SDK. Provider choice comes from config (`llm.provider`, `embeddings.provider`, `voice.*`);
credentials come from `.env`.

> **Status: nothing in this file is integrated yet.** Stage 1 only defines the boundaries.
> Payload shapes below are deliberately not written down: each stage must read the provider's
> current documentation, save real or documented payloads as fixtures, and cite the doc pages
> here. Until a real round trip is verified, an integration is labelled **mock** or
> **externally blocked**, never "live".

## Vapi (voice) — Stage 4

- Vapi handles telephony, speech-to-text, turn-taking and text-to-speech. The backend owns the
  assistant definition, tools and all facts.
- The assistant is returned per call from the `assistant-request` server event (ADR 0003),
  built from config: model from `llm`, Cartesia voice from `voice.tts`, transcriber from
  `voice.stt`, first message from `scripts/{lang}/greeting.md`, disclosures, tools.
- While any of `voice.tts.model`, `voice.tts.voice_id`, `voice.stt.provider`, `voice.stt.model`
  is `null`, the handler must not build a live assistant; it returns a transfer to
  `escalation.human_transfer_number`. (`ClientConfig.voice.is_ready` exposes this.)
- Tools: `POST /api/v1/vapi/tools` (contract in `docs/roadmap/phase-plan.md`), each taking the
  call ID and a `language`, responding in < ~300 tokens with Truth Layer spoken strings, p95 <
  800 ms. Failures return a structured `unavailable`.
- `end-of-call-report` stores recording URL, transcript, English summary, duration, outcome
  and cost.
- Webhook path prefix: `/api/v1/webhooks/` (exempt from session auth, signature-verified,
  replay-protected via `webhook_events`).
- To record in Stage 4: Vapi doc pages consulted, fixture files, webhook authentication method.

## Speech-to-text — Stage 4

Chosen by benchmark on 15–20 consented Hindi/Hinglish recordings (word error rate, numbers,
lakh/crore amounts, locality names). Result in ADR 0006; values in `voice.stt`.

## LLM — Stage 3 (implemented; live calls not yet verified)

`app/providers/llm/`: one interface for every provider. `chat(system, turns, tools, ...)`
returns text and/or tool calls. `extract` (JSON validated against a Pydantic model, one
retry) and `summarize` are built on `chat`. `get_llm(config, settings)` picks
`llm.provider`; the key comes from `.env`.

- **Timeouts:** calls are not streamed, so the whole answer, thinking included, must arrive
  within `LLM_TIMEOUT_SECONDS` (default 25 s, because a customer is waiting). Connecting and
  sending are capped separately at 10 s. A timeout (`LLMTimeout`) is retried once
  (`LLM_TIMEOUT_ATTEMPTS=2`), not like other transient errors: the provider has usually
  processed, and counted, the slow request already. The eval uses 120 s (`--llm-timeout`).
- **Retries:** `InstrumentedLLM` retries transient failures (timeouts, 408/409/429/5xx/529)
  with exponential backoff and jitter, up to `LLM_MAX_ATTEMPTS` (default 3). It never
  retries a 400 or 401.
- **Rate limits (HTTP 429):** a separate retry budget (`LLM_RATE_LIMIT_ATTEMPTS`, default 3).
  Each wait is what the provider asks for: the `Retry-After` header (seconds or HTTP date), or
  Google's `google.rpc.RetryInfo.retryDelay` in the error body when present. The Gemini error
  docs (https://ai.google.dev/gemini-api/docs/api-errors, checked 2026-10-13) describe 429 as
  per-minute limits (`rate_limit_exceeded`, retry with backoff) or a daily quota
  (`quota_exceeded`, wait for the reset) but do not show the body. Without a hint, the wrapper
  backs off exponentially. A daily quota, or a wait longer than
  `LLM_MAX_RATE_LIMIT_WAIT_SECONDS` (default 10 s, because a customer is waiting), fails at
  once: the customer gets the paused-handoff script, and the reply's `meta.llm_error` says
  `rate_limited`.
- **Temperature and thinking (Gemini 3):** Google's Gemini 3 prompting guide
  (https://docs.cloud.google.com/vertex-ai/generative-ai/docs/start/gemini-3-prompting-guide,
  checked 2026-11-02) recommends keeping temperature at the default 1.0, because lower
  values "may lead to unexpected behavior, looping, or degraded performance". For lower
  latency it recommends a low thinking level. The sample config uses `temperature: 1.0` and
  `llm.thinking_level: low`; the adapter sends
  `generationConfig.thinkingConfig.thinkingLevel` (REST example with `gemini-3.8-flash` on
  https://ai.google.dev/gemini-api/docs/generate-content/text-generation). Other providers
  ignore `thinking_level`.
- **Provider unavailable (HTTP 5xx):** `LLMUnavailable`, with the provider's own message
  (Gemini: 503 UNAVAILABLE "This model is currently experiencing high demand"). It is retried
  with `max_attempts` and backoff, or the provider's `Retry-After` when sent. When the
  retries run out, the customer gets the paused-handoff script and the reply's
  `meta.llm_error` says `provider_unavailable`, so reports can tell outages from Sales Brain
  failures. Production keeps short waits; the eval waits about 10, 20 and 40 s.
- **Latency probe:** `python -m app.brain.probe` times a models lookup, a tiny request, the
  brain's real first request and its tool follow-up, with Gemini's token counts. Use it to
  tell network latency from request cost.
- **Pacing:** `Pacer` keeps a minimum gap between call starts (`LLM_MIN_INTERVAL_SECONDS`,
  default 0) across turns and conversations, and holds later calls after a 429 that asked for
  a wait. The eval runner uses 10 s by default for free tiers.
- **Usage:** every successful call writes a `usage_records` row with kind `llm_tokens`,
  input/output tokens (thinking tokens count as output, as providers bill them), latency,
  model and purpose. Cost comes from `llm.pricing` in the client config; providers return
  tokens, not prices, and the cost is left empty if no pricing is set.
- **Provider-native turns:** an assistant turn's original content is echoed back exactly
  (`Turn.raw`). Gemini 3 needs its `thoughtSignature` back, or the next request fails with
  400. OpenAI needs its reasoning items back.

| Provider | Request (provider docs, checked 2026-10-13) | Status |
| --- | --- | --- |
| `gemini` (default; sample model `gemini-3.6-flash`, ADR 0011) | `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`, header `x-goog-api-key`; `systemInstruction`, `contents`, `tools.functionDeclarations` (no `parameters` for tools without arguments), `generationConfig`; `functionCall` / `functionResponse` parts; `usageMetadata` prompt, candidates and thoughts token counts. Sources: https://ai.google.dev/api/generate-content, https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures | request/response handling **tested** (mocked transport, 17 adapter tests); live call **externally blocked** here (the workspace network policy blocks the API host). The user runs `make eval` with a key |
| `claude` | `POST https://api.anthropic.com/v1/messages`; `tools[].input_schema`; `tool_use` / `tool_result` blocks. Source: https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools | **lightly tested** (mocked); headers and usage fields to confirm with a real key |
| `openai` | `POST https://api.openai.com/v1/responses` (newest models need the Responses API for tools); `function_call` / `function_call_output` items; `store: false` with encrypted reasoning echoed. Source: https://developers.openai.com/api/docs/guides/function-calling | **lightly tested** (mocked); usage fields to confirm with a real key |
| `offline` | `OfflineLLM`: a labelled plumbing stand-in that calls tools in a fixed order and echoes their spoken strings (`make chat CHAT_LLM=offline`, `make eval EVAL_ARGS="--llm offline"`) | **tested**; never a customer-facing or eval result |

### Sales Brain tool loop

`SalesBrain.respond` (ADR 0010) runs at most 6 tool rounds per reply. Tool results go back
in the provider's native shape. If the model ends without text, or the LLM fails after
retries, the customer gets the `paused_handoff.md` script and a handoff with reason
`ai_failure` is opened. Stage 4 serves the same tool handlers at `POST /api/v1/vapi/tools`.

## Embeddings — Stage 2 (implemented, live calls not yet verified)

`app/providers/embeddings/` — `get_embedder(config, settings)` picks the provider from
`embeddings.provider`. All return 768 L2-normalised dimensions; used only for document chunks.

| Provider | Request (from the provider docs, checked 2026-10-12) | Status |
| --- | --- | --- |
| `gemini` | `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents`, header `x-goog-api-key`, each request `{"model": "models/{model}", "content": {"parts": [{"text": …}]}, "embedContentConfig": {"taskType": "RETRIEVAL_DOCUMENT" or "RETRIEVAL_QUERY", "outputDimensionality": 768}}`; response `embeddings[].values`. Source: https://ai.google.dev/api/embeddings. 768 is a recommended size for `gemini-embedding-001`, and reduced sizes must be normalised by the client (https://ai.google.dev/gemini-api/docs/embeddings) | request shape **tested** with a mocked transport; live call **externally blocked** (needs `GEMINI_API_KEY`) |
| `openai` | `POST https://api.openai.com/v1/embeddings`, `Authorization: Bearer`, body `{"input": [...], "model": …, "dimensions": 768, "encoding_format": "float"}`; response `data[].embedding` ordered by `index`. Source: https://developers.openai.com/api/reference/resources/embeddings/methods/create. `dimensions` needs a `text-embedding-3` model | request shape **tested** (mocked); live call **externally blocked** (needs `OPENAI_API_KEY`) |
| `local` | Offline feature-hashing embedder (words + character trigrams). Matches on shared words, not meaning | **tested**; for tests and keyless development only |

With the sample config (`provider: gemini`) and no `GEMINI_API_KEY`, document uploads are refused
with `embeddings_not_configured`; data (CSV/XLSX) uploads are unaffected.

## WhatsApp Cloud API — Stage 6

Webhook verification challenge, signature validation (`WHATSAPP_APP_SECRET`), dedupe on message
ID, retries with backoff, delivery status, approved templates in en and hi outside the 24-hour
window (`scripts/{lang}/whatsapp/*.md`), opt-out by whole-message keyword match.

## Email — Stage 6

Gmail/Microsoft OAuth or IMAP/SMTP with an app password; inbound polling or push; threading into
the customer's conversation; provider-confirmed outbound status.

## Mocks

Until verified with real credentials, adapters use clearly labelled mocks under
`backend/tests/fixtures/mock_*`.
