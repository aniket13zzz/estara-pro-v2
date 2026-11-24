# ADR 0013: WhatsApp consent, replies and once-only notices

Date: 2026-11-03. Status: accepted.

## Decision

- **Replies** to customer messages go through the Sales Brain, like every channel, and are sent
  as plain text (always inside WhatsApp's 24-hour window, because the customer just wrote).
- **Consent.** Writing to us on WhatsApp is consent to replies and visit notices. On a call the
  agent asks "May we send the visit details on WhatsApp?" and stores the answer
  (`request_site_visit.whatsapp_ok` → `customers.whatsapp_opt_in_at`). No consent, no message.
- **Opt-out** only when the whole message equals a configured keyword (STOP, बंद करो, ...);
  then nothing more is sent. A later normal message from the customer is a new consent.
- **Notices** (visit requested, confirmed, reminder the day before from 09:00, due follow-ups)
  are sent by the worker every minute, each at most once (`site_visits.*_notice_at`,
  `follow_ups.status`). Inside the 24-hour window they go as text, otherwise as the approved
  template of the same name. A notice not allowed is marked handled and logged, so it is never
  sent later by surprise; a temporary WhatsApp failure is retried next minute.

## Why

Sending from the worker keeps calls fast (no WhatsApp request during a call), survives a
WhatsApp outage, and makes "exactly once" simple. Asking on the call respects WhatsApp's opt-in
rule; a lawyer should still review the wording (docs/before-first-customer.md).

## Later

Voice notes (common in India) are answered with a polite request to type; transcribing them is
a later improvement.
