# 0009 — Brief reconciliation: amenities in the Truth Layer, manual entry, lead creation

- Status: accepted
- Date: 2026-10-13

## Context

The brief (`ESTARA_PRO_BUILD_PROMPT.md`) reached the build on 2026-10-13, after Stages 1 and 2.
Checking the spec and ADR 0007 against it line by line found three gaps that affect code. (The
full list of differences, including documentation-only ones, is in section 8 of
`ESTARA_MASTER_SPEC.md`.)

## Decisions

1. **Amenities are a Truth Layer fact type.** §7 names the Truth Layer the "authoritative source
   for approved prices, availability, offers, payment plans, possession dates, amenities".
   CLAUDE.md and the phase plan list six fact types without amenities (and add areas, which the
   brief does not list). This is an omission rather than a contradiction: CLAUDE.md does not
   allow amenities from anywhere else. So the one fact-type enum gains `amenity`;
   `TruthService.amenities()` serves approved, valid, non-stale amenity rows with spoken en/hi
   strings and sources, or `unavailable` with the usual reason codes; the sample
   `truth.block_on_stale` lists all seven types. Amenity names are data and stay as uploaded
   (usually English); only the joining words are translated ("and" / "और"). Amenities in
   brochures are not used as facts: the agent tells amenities only from the Truth Layer.
2. **Manual entry (§6, "where practical")** is scheduled with the full dashboard in Stage 7. It
   goes through the same validate → preview → approve path as uploads, so it creates versioned
   drafts with a source batch, never facts that skip approval.
3. **Lead creation (§9 lists it as a backend tool).** There is no separate tool: the backend
   creates a lead (`new_enquiry`) on the first message or call from a customer with no open
   lead, and `save_requirements` also ensures one exists. Lead creation stays a deterministic
   backend rule, as ADR 0007 requires, and the model cannot create duplicate leads.

## Consequences

- `CLAUDE.md` ("All six fact types are blocked when stale") and `docs/roadmap/phase-plan.md`
  (the fact-type enum line) are now one type behind. They are the user's documents and were not
  edited; the suggested wording is "all seven fact types" and adding `amenity` to the enum list.
- Existing client configs keep working: `block_on_stale` is a list, so a config without
  `amenity` serves stale amenities, as it would any type left out of that list.
