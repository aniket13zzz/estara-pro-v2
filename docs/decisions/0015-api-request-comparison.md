# ADR 0015: A second assistant with Vapi API Request tools, for comparison

Date: 2026-11-15. Status: accepted (experiment).

## Context

Assistant A (ADR 0014) uses Vapi function tools through one gateway. On live calls the owner
heard 10 to 15 second pauses and wants to compare it with Vapi's "API Request" tools, set up by
hand in the Vapi dashboard, before choosing the main demo.

## Decision

- ESTARA also answers `POST /api/v1/webhooks/vapi/api/{tool}`: the body is the model's
  arguments, the answer is the tool result as JSON. Same secret check, same tools, same
  pause/limit rules as the gateway.
- The call is identified by `call_id={{ call.id }}` (and `caller={{ customer.number }}`) as
  static parameters (or in the URL), which Vapi fills in at run time
  (docs.vapi.ai/tools/static-variables-and-aliases/static-parameters, read 2026-11-15). No call
  id means no lookup.
- `python -m app.channels.vapi.api_tools` creates or updates the ten API Request tools and
  assistant B in Vapi (v1.6.0; v1.5.0 printed a sheet to type by hand). It copies A's model,
  voice and transcriber and only reads A. The A sync never touches B.
- The demo site calls B when opened with `?v=b`.

## Differences to know when comparing

- API Request tools do not carry the conversation, so B's lead only gets requirements from
  `save_requirements`/`search_properties` during the call (A also reads the caller's words); the
  full transcript still arrives at the end of the call.
- No `toolCallId`, so a retried request is not de-duplicated (visits are still one per lead and
  slot: a repeat returns `already_requested`).
- No background save: `save_requirements` waits for ESTARA's answer.
- After every new tunnel address, run the B command too (it updates the tool URLs).

Whichever wins, the other is removed (code and setup) once the owner chooses.
