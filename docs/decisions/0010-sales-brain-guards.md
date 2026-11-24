# 0010: Sales Brain design: rules first, guarded replies

- Status: accepted (implemented in Stage 3, `app/brain`)
- Date: 2026-10-13

## Context

The brief and CLAUDE.md set hard rules for the Sales Brain. The LLM never produces a number
or date on its own. Scores and states come from backend rules. Replies use the caller's
language, with Hinglish in Devanagari. Customer-facing text lives only in `client/scripts/`.
A prompt can ask a model to follow these rules, but cannot guarantee that it will. Stage 3
also needs one entry point for every channel, `brain.respond(conversation, message)`.

## Decision

1. **Deterministic first, LLM second.**
   - Each inbound message is first read by rules (`app/brain/extraction.py`, built on
     `app/brain/quantities.py`). The rules cover budgets in lakh/crore in en/hi/Hinglish,
     BHK, timeline, loan, purpose, property type and approved localities.
   - The rules save what they find and apply ADR 0007 system transitions before the model
     runs.
   - The model adds what the rules cannot read (for example a place written in Devanagari)
     through `save_requirements`. It passes the budget as the customer's own words
     (`budget_text`), and the same rules parse it.
2. **One prompt template** (`app/brain/prompt.py`) with a language block. It includes the
   language's `sales_script.md` and per-turn backend notes: lead state, missing fields, the
   detected objection with its approved script, the escalation, and "offer a visit now".
   There is one brain, not one per language.
3. **Tools are the phase plan's contract** (`app/brain/tools.py`, 11 tools).
   - Facts arrive as Truth Layer spoken strings (`say`). Anything not servable arrives as
     `status: unavailable` with an instruction to confirm with the sales team.
   - Each tool runs in a database savepoint and returns a structured error on failure.
   - Knowledge snippets have amounts, percentages and month-year dates masked, because
     documents are never a source of commercial facts.
4. **Grounding guard** (`app/brain/grounding.py`).
   - Every amount, area, percentage, clock time, count, duration, date, and any number
     above 12 in a reply must equal, by value, something from one of these sources:
     - this conversation's tool results;
     - the customer's own messages;
     - approved script text. Bare list numbers in scripts do not count.
   - A failing reply goes back to the model once with the reason.
   - If the rewrite still fails, the sentences with ungrounded claims are removed. If
     nothing is left, the customer gets `paused_handoff.md` and a handoff (`unsafe_reply`)
     opens.
5. **Language guard.**
   - Hindi replies must contain at least 15% Devanagari letters. The bar is low because
     Hinglish keeps project names and loanwords in Latin letters. English replies must
     contain almost none.
   - A wrong-language reply gets one rewrite. If the rewrite is still wrong, it is kept and
     flagged (`language_mismatch`) for review. A safe reply in the wrong script is better
     than no reply.
   - Inbound detection uses the same 15% bar.
6. **Escalation is enforced, not just prompted** (`app/brain/signals.py`).
   - Keyword rules in en, hi and Hinglish detect: request for a person, legal question,
     anger, off-topic, objection, and "are you a bot?".
   - A request for a person, a legal question or a second angry message always opens a
     handoff and moves the lead to `handed_off`, even if the model did not call
     `transfer_to_human`. In that case the paused-handoff script is appended so the
     customer is told.
7. **AI disclosure is deterministic.** The client's `ai_disclosure.md` is prepended to the
   first text reply of a conversation, and to any reply after "are you a bot/human?". Voice
   discloses at call start (Stage 4).
8. **Fallbacks reuse existing scripts.** The kill switch, the monthly LLM budget, an LLM
   failure after retries, and an unsafe reply all answer with `paused_handoff.md` and open a
   handoff. No new script file was added; the phase plan's file list is unchanged.
9. **Handoff records carry full context** (`app/handoff/service.py`, brief §8): requirements,
   recent messages, latest matches, score and band, objections, visit, and a recommended next
   action. A visit request also opens one (`visit_requested`) but leaves the lead in
   `visit_requested`, as ADR 0007 says.
10. **Thinking-model budget.** Replies are requested with up to 4,096 output tokens, because
    Gemini and OpenAI count reasoning tokens as output. The prompt keeps the visible reply to
    three sentences on chat channels.

## Consequences

- The guard is conservative.
  - A model that rounds ("about 75 lakh") or adds a date from general knowledge gets
    rewritten or trimmed.
  - The evals report how often first drafts needed the guard, so a model is never scored on
    the guard's work.
- Hinglish customers who type in Roman script get replies in Devanagari (CLAUDE.md). If a
  client wants Roman-script Hinglish on WhatsApp, that is a new config option and a new ADR,
  not a code edit.
- Places typed in Devanagari ("न्यू टाउन") are not read by the rules. The model must pass them
  in English letters to `save_requirements` (the tool's description says so). A
  transliteration table for localities could move this into the rules later.
- Keyword signals miss phrasings they do not list. The model's own `transfer_to_human` is
  the second line of defence, and evals measure the combination.

## Amendment (2026-11-02): Gemini temperature and thinking level

A live eval request (en_01, turn 1) got no answer within 120 s, twice. Request size is not
the issue: the system prompt is about 2.8k characters and the 11 tool definitions about
4.7k, roughly 2k tokens. Two settings went against Google's Gemini 3 guidance:
- temperature 0.3 (Google recommends the default 1.0 and warns lower values can loop);
- the default (medium) thinking level, where Google recommends low for lower latency.

`client.config.yaml` now has `temperature: 1.0` and a new `llm.thinking_level: low`
(`low | medium | high | null`; Gemini only). `python -m app.brain.probe` measures the effect
on the user's network. If replies get worse at low thinking, the eval will show it and
`thinking_level` can go back to `medium` or `null` in config, with no code change.


The default model itself changed afterwards: see ADR 0011 (`gemini-3.6-flash`).
