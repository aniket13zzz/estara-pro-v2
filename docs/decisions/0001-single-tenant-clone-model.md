# 0001 — Single-tenant clone model

- Status: accepted
- Date: 2026-10-11

## Context

ESTARA Pro serves real-estate developers who each want their own number, voice, scripts, sales
team and data, and who treat their inventory and leads as confidential. Volumes per client are
modest (thousands of calls a month, not millions).

## Decision

Each client gets its own deployment cloned from one template: one database, one Vapi assistant,
one dashboard. There is no `tenant_id` and no row-level security.

- Template code is identical for every client.
- Only `client/` (config, scripts, branding), `.env` and uploaded data differ.
- A client need that would require a code edit is turned into a config field instead.
- Template updates are pulled into client repos without conflicts because client-owned paths
  never overlap template code (procedure in Stage 8).

## Consequences

- Strong isolation: no cross-client query is possible; a client's data can be exported, backed
  up or deleted as a whole.
- Simpler code: no tenant filters to forget.
- More deployments to operate. Mitigated by `setup_client.sh`, the clone guide and identical
  template versions (Stage 8).
- Schema changes ship as template migrations applied to every clone.
- Per-client customisation is limited to what config and scripts express. This is intended:
  Pro-tier scope, no custom code per client.
