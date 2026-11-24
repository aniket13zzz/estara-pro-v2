# 0007 — Lead state transitions

- Status: accepted (implemented in Stage 3, `app/leads`)
- Date: 2026-10-12; checked line by line against the brief (§1, §7, §8) on 2026-10-13
- Numbering: 0006 is reserved for the transcriber choice (Stage 4), as the phase plan and stage
  prompts already refer to "ADR 0006". This ADR takes the next free number.

## Context

The brief (§8) asks for a "validated lead lifecycle with documented states and transitions" and
lists nine states, but not the transitions: new enquiry, engaged, qualified, visit requested,
visit confirmed, handed off, nurturing, won, lost. Stage 3 must implement "only the documented
transitions", so they are written down here.

Constraints from the brief and CLAUDE.md:

- Scores and qualification come from backend logic, never from the LLM (§7, §8, CLAUDE.md).
- The AI may request a site visit; only an authorised person confirms it (§8).
- After the visit is booked, the lead is handed to a human salesperson with full context, and
  human salespeople close the deal (§1).
- Leads who did not book get follow-ups (§8).

## Decision

### Meaning of each state

| State | Meaning |
| --- | --- |
| `new_enquiry` | First contact recorded; nothing useful learned yet |
| `engaged` | The customer is talking and at least one requirement is known |
| `qualified` | Every field in `lead_scoring.qualified_requires` is known (sample config: budget, location, property type) |
| `visit_requested` | The AI (or staff) requested a site visit; waiting for staff confirmation |
| `visit_confirmed` | A staff user confirmed the visit |
| `handed_off` | A human now owns the lead: the caller was transferred, the conversation escalated (asked for a person, angry, legal question, out of scope, AI failure, kill switch or usage limit), or the assigned salesperson accepted the handoff |
| `nurturing` | Not ready now; follow-ups run per `follow_up` config, respecting opt-outs |
| `won` | Staff recorded a booking or sale. Terminal |
| `lost` | Staff closed the lead (not interested, bought elsewhere, spam, wrong number). Terminal |

`handed_off` describes who owns the lead, not whether a `handoffs` row exists. A visit request
also creates a handoff record for the assigned salesperson (Stage 5), but the lead stays
`visit_requested` (or `visit_confirmed`) until the salesperson accepts the handoff, so its
progress toward a visit stays visible. The usual path is therefore
`qualified → visit_requested → visit_confirmed → handed_off → won/lost` (§1, §8).

### Who may trigger transitions

- **system**: backend rules run on facts from tool calls and events (requirements saved, visit
  requested, transfer, follow-up timer, visit cancelled or no-show). The LLM supplies data
  through tools; it never names a target state.
- **staff**: an authenticated `client_admin` or `sales_staff` user, audited.
- Only staff can move a lead to `visit_confirmed`, `won` or `lost`.

### Allowed transitions

| From | To | Trigger | By |
| --- | --- | --- | --- |
| `new_enquiry` | `engaged` | First requirement saved | system |
| | `qualified` | One message provides every required field | system |
| | `handed_off` | Transfer or escalation | system, staff |
| | `lost` | Spam or wrong number | staff |
| `engaged` | `qualified` | All `qualified_requires` fields known | system |
| | `nurturing` | No reply within the first follow-up window, or customer says not now | system, staff |
| | `handed_off` | Transfer or escalation | system, staff |
| | `lost` | Closed | staff |
| `qualified` | `visit_requested` | `request_site_visit` succeeded | system, staff |
| | `nurturing` | No visit booked within the first follow-up window, or customer says not now | system, staff |
| | `handed_off` | Transfer or escalation | system, staff |
| | `won`, `lost` | Closed | staff |
| `visit_requested` | `visit_confirmed` | Visit confirmed | staff |
| | `nurturing` | Visit cancelled before confirmation | system, staff |
| | `handed_off` | Transfer or escalation; or the assigned salesperson accepts the handoff | system, staff |
| | `won`, `lost` | Closed | staff |
| `visit_confirmed` | `visit_requested` | Visit rescheduled (needs confirming again) | system, staff |
| | `nurturing` | Visit cancelled or no-show | system, staff |
| | `handed_off` | Transfer or escalation; or the assigned salesperson accepts the handoff | system, staff |
| | `won`, `lost` | Closed (typically after the visit is completed) | staff |
| `handed_off` | `visit_requested` | A visit is requested while a human owns the lead | system, staff |
| | `visit_confirmed` | Staff confirms a visit | staff |
| | `nurturing` | Human hands the lead back for follow-ups | staff |
| | `won`, `lost` | Closed | staff |
| `nurturing` | `engaged` | Customer replies | system |
| | `qualified` | Customer replies and all required fields are known | system |
| | `visit_requested` | A visit is requested | system, staff |
| | `handed_off` | Transfer or escalation | system, staff |
| | `won`, `lost` | Closed | staff |
| `won`, `lost` | — | Terminal | — |

Anything not in this table is rejected and logged; the lead keeps its state.

### Rules

1. Every transition writes a `lead_events` row with `from_state`, `to_state`, `actor_type`,
   `actor_user_id` (for staff) and the trigger in `details`. Staff transitions are also
   audited.
2. Transitions only move forward, except `visit_confirmed → visit_requested` (reschedule) and
   the explicit moves into and out of `nurturing` and `handed_off` above. Requirements going
   missing never moves a lead backwards.
3. A completed visit does not change the lead state by itself; staff decide `won`, `lost` or
   `nurturing` afterwards.
4. A WhatsApp opt-out stops follow-ups but does not change the lead state.
5. When a customer whose only leads are `won` or `lost` makes new contact, the system creates a
   new lead in `new_enquiry` instead of reopening the old one. The old leads stay on the
   customer record for context.
6. One open (non-terminal) lead per customer at a time. New contact attaches to it.
7. Scoring is separate from state: `evaluate_lead` updates score, band and reason without
   changing state.

## Check against the brief (2026-10-13)

| Brief | This ADR |
| --- | --- |
| §8 states: new enquiry, engaged, qualified, visit requested, visit confirmed, handed off, nurturing, won, lost | Same nine states, same order |
| §8 "validated lead lifecycle" | Transitions outside the table are rejected and logged |
| §7, §8 qualification and scores from backend logic | Rule 7, and only system or staff actors; the LLM never names a state |
| §8 AI may request a visit, only an authorised person confirms | Only staff reach `visit_confirmed` |
| §1 hand the lead to a human salesperson; humans close the deal | Added: salesperson accepting the handoff → `handed_off`; only staff reach `won`/`lost` |
| §8 follow-ups for leads who did not book | `nurturing` transitions |

## Consequences

- Stage 3 implements this table as data in `app/leads`, with tests for every allowed
  transition and a sample of rejected ones, including that system actors cannot reach `won`,
  `lost` or `visit_confirmed`.
- Stage 5 calls the "salesperson accepts the handoff" trigger when a handoff is acknowledged.
- "No reply within the first follow-up window" uses the first value of
  `follow_up.schedule_days`; Stage 5 runs that timer.
- If the brief or a client later needs a different flow, this ADR is superseded by a new one.
  Transitions are not a per-client config setting, so every clone behaves the same.
