# 0004 — Bilingual English/Hindi with Hinglish as the norm

- Status: accepted
- Date: 2026-10-11

## Context

Buyers in India mix Hindi and English freely. Formal textbook Hindi sounds wrong on a sales
call. Prices and dates are where voice agents most often fail (reading "85,00,000" literally,
or inventing numbers).

## Decision

- Supported languages come only from `agent.languages` (`[en, hi]` for Pro). Code never lists
  languages.
- Language is per-conversation state owned by the backend, stored on `conversations.language`,
  per message, and as `customers.preferred_language`. Mid-call switching is allowed and logged
  (`messages.language_switched`). Returning callers are greeted in their last language.
- Hindi output is natural spoken Hinglish in Devanagari; project names, "BHK" and common English
  loanwords stay in English.
- All customer-facing text lives in `client/scripts/en/` and `client/scripts/hi/` with the exact
  file names in the phase plan's "Script files". There is no other location for customer-facing
  text. A missing or empty translation, or a WhatsApp template present in one language only,
  stops the app at startup. Nothing silently falls back to English.
- Numbers, money and dates are formatted by the Truth Layer (Stage 2): lakh/crore, Indian digit
  grouping, Hindi number words, Hindi month names.
- The dashboard UI is English; transcripts are shown as spoken with an English summary.
- WhatsApp opt-out triggers only when the whole message equals a configured keyword, so a common
  word like "बंद" inside a sentence never unsubscribes anyone.
- Every conversation test suite has English, Hindi and Hinglish cases.

## Consequences

- Every new customer-facing text costs two files; the validator enforces it.
- Speech-to-text quality on Hinglish numbers and localities is a real risk; Stage 4 benchmarks
  transcribers on consented recordings before choosing one (ADR 0006).
