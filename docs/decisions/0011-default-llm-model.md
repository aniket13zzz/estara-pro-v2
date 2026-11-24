# 0011: Default LLM model is gemini-3.6-flash

- Status: accepted
- Date: 2026-11-02
- Related: ADR 0010 (Sales Brain; amendment on Gemini temperature and thinking level)

## Context

The sample config used `gemini-3.8-flash`. Google's models page listed it as the stable Flash
model recommended for new projects, with a free tier (checked 2026-10-13). On the user's
machine, live use was not workable:

- The eval runs timed out: en_01, turn 1, no answer within 120 s, twice.
- In the latency probe (`python -m app.brain.probe`) on 2026-11-02, the network was fine
  (0.4 s to Google). `gemini-3.8-flash` first answered 503 "This model is currently
  experiencing high demand" and then hung for 120 s.
- In later probes reported by the user, `gemini-3.8-flash` took 90–120 s per request, with
  temperature 1.0 and thinking level low.
- In the same probes, `gemini-3.6-flash` answered in about 1 s and made correct tool calls.

Those later probe outputs were reported, not attached, so the timings above are the user's.

A sales agent cannot wait 90 s for a reply. Chat customers leave, and a voice caller hears
silence (the phase plan's latency budget is about 1.5 s per turn). Google's models page lists
`gemini-3.6-flash` as stable too. The pricing page lists it with a free tier and the same paid
price as 3.8 Flash: USD 0.75 input / 3.75 output per million tokens through 2026-12-31, then
1.50 / 7.50. The thinking page lists low as a supported thinking level for it (default
medium).

## Decision

- The sample `client/client.config.yaml` uses `llm.model: gemini-3.6-flash`, with temperature
  1.0 and `thinking_level: low` as in ADR 0010's amendment. The `pricing` block is unchanged
  because the prices are the same.
- The model stays a config value. No code depends on it, so a client or a later template
  version can switch back to `gemini-3.8-flash` (or another model) by changing one line, after
  the probe and the eval show it answers in time.

## Consequences

- The eval and `make chat` now use `gemini-3.6-flash`. Its reply quality in English, Hindi and
  Hinglish (grounding, language, visit offers) is not measured yet. The next eval run is the
  first measurement, and its results decide whether this default stands.
- The 3.8 Flash slowness may be temporary (Google's 503 said "Spikes in demand are usually
  temporary"). Re-probe it before a client goes live, and record the result here if the
  default changes.
- Stage 4 (voice) needs per-turn latency far below these numbers whichever model is used. It
  measures real turn latency from call logs, as the phase plan requires.
