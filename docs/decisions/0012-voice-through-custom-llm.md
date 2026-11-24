# ADR 0012: Calls are answered by the Sales Brain through Vapi's custom LLM

Date: 2026-11-03. Status: accepted. Amends ADR 0003.

## Context

ADR 0003 planned for Vapi to call the LLM itself, with ESTARA exposing tools at
`/api/v1/vapi/tools`. In that design Vapi speaks whatever the model writes. The checks that make
ESTARA trustworthy run in the Sales Brain, after the model drafts a reply: the number guard
(no price, date or figure that no tool gave), the reply-language check, the escalation rules and
the privacy rules. On calls they would not run. Calls are the main channel, so they need these
checks most.

## Decision

- One Vapi assistant per client, built from config and created or updated through the Vapi API
  (`python -m app.channels.vapi.sync`). Nobody edits it by hand.
- The assistant's model is Vapi's **custom LLM** pointed at
  `/api/v1/webhooks/vapi/llm/chat/completions`. Each caller turn goes through
  `SalesBrain.respond`, exactly like WhatsApp and chat. The tools run inside the brain and are
  never exposed to Vapi.
- Server messages (`assistant-request`, `end-of-call-report`) go to
  `/api/v1/webhooks/vapi/events`. A phone number configured to ask us gets our assistant, or is
  forwarded to the human number when the agent is paused or voice is not set up.
- Every request carries a shared secret (`VAPI_WEBHOOK_SECRET`): Vapi's Bearer server
  credential and the Custom LLM API key.
- Browser test calls use Vapi's web SDK with the public key and the assistant id; no secret
  reaches the browser.

## What the docs do not confirm (live checks in docs/demo.md)

- That Vapi adds `call.id` (and `call.customer.number`) to custom-LLM requests. Without the call
  id the caller is handed to a person and the request's keys are logged.
- That a custom LLM can trigger Vapi's `transferCall` tool. If not, the caller hears that the
  team will get back to them, and the handoff is already recorded.

## Consequences

- One brain and one set of guards for every channel; calls are as safe as chat.
- A turn can be slower than with Vapi calling the model directly, because the brain may make
  several model calls. A filler line is spoken if no answer is ready after 1.5 seconds.
  Measure real latency in the live checks.
- `gemini-3.6-flash` (ADR 0011) answered in about 1 s per request in probes, which keeps most
  turns short.
