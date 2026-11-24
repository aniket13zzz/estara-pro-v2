# ADR 0014: Native voice mode (Vapi's model calls ESTARA's tools)

Date: 2026-11-13. Status: accepted (default for new clients). Supersedes ADR 0012 as the default;
ADR 0012's custom LLM mode stays available.

## Context

In the custom LLM mode (ADR 0012) every caller turn goes Vapi → ESTARA → Gemini → tools →
number guard → Vapi. Tested on the free Gemini tier it was slow and used many filler lines. Vapi's
own models answer immediately, and Vapi bills the model inside its per-minute price, so a client
needs no separate AI bill for calls.

## Decision

`voice.mode: native` (the new default): the assistant is created in the Vapi dashboard, where
the client chooses the model, voice and transcriber. `python -m app.channels.vapi.sync` adds
only ESTARA's parts: instructions (built from the same rules and scripts as the Sales Brain),
ten **function** tools pointing at one gateway (`POST /api/v1/webhooks/vapi/tools`), the transfer
tool with a fallback plan, the greeting, the events address and the call length.

Function tools, not "API Request" tools, because only function tools send the call id, which is
how the gateway ties a request to the caller. The gateway checks the shared secret, finds the
caller from Vapi's call id and caller ID, stores new turns and applies the fixed rules (budget,
area, BHK, objections, scoring), answers each `toolCallId` once, and returns HTTP 200 with a
`result` or `error` string as Vapi requires.

## Consequences

- Faster, more natural calls; one bill (Vapi) for calls. A small Gemini key is still used for
  WhatsApp, document search and call summaries.
- **Loss:** the number guard cannot check the model's words before they are spoken. Mitigation:
  tools return ready-to-speak strings; the instructions forbid any other number; after each call
  `check_numbers` lists numbers no tool result or caller turn contained, shown as "check numbers"
  on the dashboard for staff to review.
- The model, voice and transcriber may be edited in the Vapi dashboard; the instructions and
  tools must not be (the next sync overwrites them).
- Rollback: `voice.mode: custom_llm` and run the sync.
- Live checks: the tool-call payload fields (`artifact.messages`, `call.customer.number`) and
  the transfer fallback are documented by Vapi but not yet seen on a real call.
