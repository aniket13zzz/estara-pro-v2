# Vapi sample payloads (test fixtures)

Shapes follow Vapi's docs as read on 2026-11-03:

- `assistant_request.json`, `end_of_call_report.json`: docs.vapi.ai/server-url/events
  (`message.type`, `call`, `endedReason`, `artifact.transcript`, `artifact.recording`,
  `artifact.messages`). The docs do not list the fields inside `artifact.recording`; the
  code takes the first http(s) URL it finds there.
- `custom_llm_turn.json`: an OpenAI chat-completions request (Vapi's custom LLM is
  "OpenAI compatible", docs.vapi.ai/assistants/custom-llm). **Not confirmed by the docs:**
  that Vapi adds `call.id` and `call.customer.number` to this request. The first live test
  call confirms it; if the call id is missing, the backend logs the request's top-level keys
  and hands the caller to a person (docs/demo.md, "Live checks").

- `tool_calls.json` (native mode, ADR 0014): a `tool-calls` message as shown in
  docs.vapi.ai/tools/custom-tools (read 2026-11-13): `message.toolCallList[]` with `id` and
  `function.name`/`function.arguments`, and `message.call.id`. **Not confirmed by the docs:**
  that `message.artifact.messages` (role `user`/`bot`, text in `message`) and
  `call.customer.number` are present on tool calls. Without them the tools still work; the
  caller's words are then stored from the end-of-call report instead.

Replace these with real payloads captured from the first live call.
