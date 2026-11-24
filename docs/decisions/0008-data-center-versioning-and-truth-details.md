# 0008 — Data Center versioning and Truth Layer details

- Status: accepted
- Date: 2026-10-12

## Context

Stage 2 had to turn the plan's rules (versions archived not deleted, staleness computed, four
`unavailable` reason codes, en/hi spoken strings) into concrete behaviour. Several choices were
not fixed by the plan or stage prompt.

## Decisions

1. **Drafts are written at validation, approved in place.** A file with no errors writes every
   row as a new `draft` version (version = previous max + 1, `source_batch_id` = the batch).
   Approval flips those rows to `approved` and archives the approved versions they replace in the
   same transaction. Reject archives the drafts; Archive withdraws an approved batch's
   still-current rows. Nothing is deleted. This is what makes the `unapproved` reason code real:
   a fact that exists only as a draft is refused as `unapproved`.
2. **A file with any error writes nothing.** The batch is `failed`, and the preview lists every
   finding by sheet, row and column. Warnings (unchanged rows, unknown columns or sheets,
   references to unapproved parents) do not block.
3. **Re-importing identical data is allowed and refreshes freshness.** Such rows are shown as
   "unchanged", become a new approved version, and get a new `approved_at`. This is the only way
   staleness is cleared, matching "re-approval clears it".
4. **Parents are resolved by code, not by row id.** Foreign keys point at the parent version that
   was current when the child was written. Every Truth Layer query joins through the parent's
   *code*, so a new project version does not orphan its units.
5. **Last approval wins.** If two pending batches touch the same key, whichever is approved last
   becomes current. More than one approved version of a key is treated as a database
   inconsistency and refused as `contradictory`, never guessed at.
6. **Contradictions.** Within one upload, the same key with different values, a unit price that
   differs from its unit price list, or a unit BHK that differs from its unit type are errors.
   Across uploads the Truth Layer refuses a unit whose own price and approved unit price list
   disagree (`contradictory`). A pending (draft) unit price list makes that unit's price
   `unapproved` rather than silently serving the old price.
7. **Not-yet-valid rows** (`valid_from` in the future) are `missing` with the detail "not valid
   until …", not `stale`. Validity dates use the client's timezone (`company.timezone`).
8. **Spoken amounts always end with the currency word** ("1.2 to 1.5 crore rupees",
   "… डेढ़ करोड़ रुपये"), and English amounts are never rounded: if lakh/crore with two decimals
   is not exact, the full form is used ("85 lakh 37 thousand 500 rupees"). Hindi uses सवा, डेढ़,
   ढाई, साढ़े and पौने only where they are exact. The Hindi wording needs a native speaker's
   review before production.
9. **Embeddings.** Adapters for Gemini (`batchEmbedContents`, 768 dims, client-side L2
   normalisation as the Gemini docs require for reduced dimensions), OpenAI
   (`text-embedding-3-*` with `dimensions: 768`) and an offline `local` hashing embedder for
   tests and keyless development (word overlap, not semantic). Live Gemini/OpenAI calls are not
   verified yet (no keys).
10. **Uploads** are identified by content (magic bytes), not the file name; XLSX/DOCX are
    checked for macros, entry counts and compression ratios before parsing; files are stored by
    SHA-256. Limits: 10 MB data, 25 MB documents, 5,000 rows per sheet, 200 PDF pages.
11. **Dashboard proxy body limit.** Next.js buffers proxied request bodies up to 10 MB by
    default and silently truncates beyond that. `experimental.proxyClientMaxBodySize` is set to
    30 MB so the backend's own limits apply. The option is marked experimental in Next.js 16.4;
    recheck it on upgrades.
12. **Schema additions (migration 0002):** `alerts` (one open alert per kind),
    `validation_errors.severity` and `.entity`, `import_batches.filename`, `.summary`,
    `.archived_at`, and an `archived` batch status.

## Consequences

- Freshness can only be restored by re-uploading and approving, which keeps a human in the loop
  for every fact the agent speaks.
- Truth Layer queries do a few more joins (through parent codes); inventories are small and the
  queries use the plan's indexes. Stage 4's p95 < 800 ms test will measure this.
- Towers are in the schema but not in the Stage 2 templates (the stage prompt does not list
  them); units carry floor and facing directly.
