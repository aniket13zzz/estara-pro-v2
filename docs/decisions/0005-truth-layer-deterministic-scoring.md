# 0005 — Truth Layer and deterministic scoring

- Status: accepted (Truth Layer built in Stage 2, scoring in Stage 3)
- Date: 2026-10-11

## Context

An AI sales agent that states a wrong price, a lapsed offer or an invented possession date
creates legal and commercial risk. LLMs produce plausible numbers. Lead qualification decided by
an LLM is not reproducible or explainable.

## Decision

**Truth Layer**

- Prices, availability, offers, payment plans, possession dates and areas are served only by
  `app/truth/` from `approved`, non-stale, non-contradicted rows.
- One fact-type enum everywhere: `price`, `availability`, `offer`, `payment_plan`,
  `possession`, `area` (`app.core.enums.FactType`).
- Staleness is computed, never stored: stale when `approved_at` is older than
  `truth.stale_after_days` or `valid_to` has passed; re-approval clears it. All six types are
  blocked when stale (amended by ADR 0009: `amenity` is a seventh type, also blocked). The
  schema has no "stale" status (a test enforces this).
- Anything else returns `unavailable` with a reason code (`missing`, `stale`, `unapproved`,
  `contradictory`); the agent says the sales team will confirm.
- Tools return pre-formatted spoken strings per language. The LLM never formats or invents a
  number or date.
- pgvector is for documents only, never prices or availability.
- The database enforces that approved rows name their approver.

**Deterministic scoring and state**

- Lead score = weighted sum of rule checks from `lead_scoring.weights` (keys validated at
  startup), banded by `thresholds` (validated `0 < warm < hot <= 100`). The stored reason
  lists which rules matched.
- Qualification requires the fields in `lead_scoring.qualified_requires`.
- Lead and visit transitions follow fixed tables; every transition is logged.
- Only an authenticated `client_admin` or `sales_staff` user can confirm a site visit; the AI
  can only request one.

## Consequences

- Some answers will be "I'll confirm with the team" when data is stale or missing. This is the
  intended failure mode and pushes clients to keep data fresh (staleness alerts in Stage 2).
- Scores are explainable and reproducible, and testable without an LLM.
- Evaluations (Stage 3 and 8) check that no number or date appears in a reply unless it came
  from a tool result.
