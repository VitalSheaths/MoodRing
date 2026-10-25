# One-request SEC smoke plan — pending approval

This is a review plan, not execution authorization. No real SEC request has been made.
Merge PR #25 only after maintainer approval, both required checks green on the
reviewed head, and no unresolved review threads.

## Approval record (required before execution)

- Exact canonical ten-digit CIK: **pending maintainer choice**.
- Named operator: **pending maintainer choice**.
- Exact identified User-Agent (product token + operator contact email): **pending private approval**.
- Reviewed local universe manifest containing only that issuer and pinned SIC
  taxonomy paths/hashes: **pending review**. Do not reuse synthetic identities as real evidence.
- Reviewed commit SHA and execution window: **pending**.
- Budget: one operator, one invocation, one CIK, one attempt, zero retries.

Keep operator contact and local paths out of public comments. Record approval
privately before populating SEC_USER_AGENT. Do not execute the command below now.

## Preflight and proposed command

Verify offline regression and synthetic demo, the approved manifest/taxonomy,
verified trust store and that no other operator invocation is running. No API,
scheduler, database writer or downstream consumer is started. Use the reviewed
checkout and environment documented in SEC_ACCESS.md. Only after approval:

```bash
PYTHONPATH=src python -B examples/sec_metadata.py --allow-network --one-request \
  --cik APPROVED_CIK --universe APPROVED_MANIFEST --taxonomy APPROVED_TAXONOMY
```

Provide the exact approved SEC_USER_AGENT through the local environment; never
commit it or put it in command history. HTTPS destination is generated exclusively
as `https://data.sec.gov/submissions/CIK##########.json`. No redirects or linked files.
Five-second whole-attempt bound, 30-second overall deadline, one MiB wire/decoded
limit, TLS verification and isolated proxy-free worker remain enforced.

## Stop and review

Any failure, timeout, redirect, rate limit, body-limit or normalization error ends
this smoke: do not retry, change CIK or relax controls. A successful transport may
still report quarantined filings because publication availability is unknown;
current SIC is not historical membership evidence.

Review only sanitized status/counts, attempt count, exit status and reviewed commit.
Do not print/export raw bodies, receipts, headers, contact details or private paths.
Responses stay in memory; no durable payload/cache/database writes, downloads to
files, trading, universe integration or model use. Normal interpreter reads and
process execution are not an application persistence feature.

Report the sanitized outcome for separate review. A second CIK, rerun, comparison,
persistence or automatic operation requires new explicit approval.
