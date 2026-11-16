# Cloud incident investigation

Implemented and verified offline on November 15, 2026. This is not evidence of a live AWS deployment. The local simulator keeps its separate investigation implementation.

The AWS failure inbox offers **Snapshots** and **Incident groups**, with explicit **Apply filters**, manual refresh, and Previous/Next navigation. Selecting a group opens its indexed members. Snapshot selection remains explicit, persists across pages and filters, and is limited to 25 IDs for a replay plan. Group selection never selects all members for recovery. Viewers can investigate but cannot collect, select, or create recovery plans.

## Grouping and evidence boundaries

Collection atomically writes the snapshot metadata, a copy of that metadata under its incident membership, the incident's collected count, and an audit event. Conditional snapshot/membership writes prevent duplicate collection or a conflicting transaction from increasing counts. Bodies and raw attributes remain in encrypted S3 evidence, outside the investigation lists.

A versioned signature groups the configured queue, consumer version and normalized schema category. Each signature has a separate incident per UTC collection hour. The available categories are `INVALID_SCHEMA` and `UNCLASSIFIED`. A valid shipment body alone does not prove a warehouse timeout, consumer exception, or root cause. Legacy timeout labels are presented as unclassified in message listings. Consumer version is the configured version at collection, not proof of the version that originally failed.

All counts describe **indexed samples**, not a complete DLQ inventory or business recovery totals. Collection receives at most ten messages, can encounter duplicates, changes visibility temporarily, and retains originals. `collectedCount` is a historical count of successful index additions, not a count of currently retained members. Expired metadata is excluded from lists and incident lookup before physical TTL deletion. Member metadata expires at its collection time plus configured retention; the summary expires at the end of its hour plus retention, so a summary may briefly have no remaining members. These timestamps do not establish the original message's DLQ expiry or guarantee S3 lifecycle deletion at an exact time.

There is no automatic backfill of snapshots indexed before incident indexing was added. They remain searchable through `/messages`; only new index transactions create incident membership. This undeployed prototype does not run a migration against an account.

## API contract

All routes require the existing authenticated read capability and use the configured queue scope:

| GET route | Result |
| --- | --- |
| `/api/messages` | Indexed message metadata |
| `/api/incidents` | Incident summaries |
| `/api/incidents/ID` | A retained incident summary |
| `/api/incidents/ID/messages` | Retained member metadata, after checking the incident belongs to this queue |

The list routes accept the same query parameters. Unknown filters and malformed boundaries return HTTP 400.

| Parameter | Meaning |
| --- | --- |
| `limit` | 1–100 returned matches; default 25 |
| `q` | Case-insensitive substring, up to 128 printable characters. Messages search snapshot ID, operation ID, original message ID and normalized category label. Groups search incident ID, label, category and consumer version. Payloads are not searched. |
| `category` | `INVALID_SCHEMA` or `UNCLASSIFIED` |
| `schema` | `valid` or `invalid`; describes schema validation, not replay eligibility |
| `observedFrom`, `observedTo` | Inclusive Unix seconds. Message filters use collection time; group filters use overlap with the UTC collection window. |
| `cursor` | Opaque continuation returned by the previous response; keep the route, queue and filters unchanged |

Filtering happens on the server over bounded DynamoDB partition queries, not a new search index or a complete table scan. Each request makes at most four queries of up to 100 metadata rows each (also subject to the service's page byte limit). Results retain storage-key order, not chronological order. `evaluatedCount` counts fetched rows, including over-fetched rows beyond a returned page boundary. Pagination resumes after the last examined row so matches in an over-fetched page are not skipped.

Responses include `items`, `nextCursor`, `evaluatedCount`, `readBudgetReached`, and `scope: INDEXED_SNAPSHOTS`. An empty page with a continuation is not the end of the search: use Next. The dashboard displays this condition and resets pagination when filters or routes change. Lists are live reads, not a frozen inventory; concurrent inserts and retention can change subsequent pages.

Browsing lists never receives from SQS, invokes a consumer/verifier, or fetches S3 bodies. Inspecting a message deliberately uses the existing masked evidence route. Grouping does not alter approval, operation reservations, replay, or business-verification requirements. [Scoped CloudWatch correlation](cloud-log-evidence.md) is implemented and verified offline as a separate explicit lookup. Retention warnings are described below; complete post-incident reports remain outstanding, and live-account validation is still required.

Message and incident-member lists now make one scoped SQS attributes read per request to add [retention warnings](cloud-retention.md). Incident-summary lists do not. Unknown queue settings leave valid snapshots visible. Snapshot access deadlines are enforced during masked detail reads; original-message expiry is an estimate and does not establish current presence or deletion. Retention warnings are verified offline; complete post-incident reports remain outstanding.

## Verification

Run `.venv/bin/python -m unittest discover -s tests -v`, `npm run test:frontend`, and `npm run build`. The AWS SDK tests require `requirements-aws-test.txt` and use Moto rather than real AWS credentials.

Investigation tests cover schema/version/hour grouping, duplicate and interrupted writes, scoped cursors and membership authorization, sparse search continuation, over-fetched matches, filter validation, retention boundaries, metadata allowlists, and authenticated routes. Frontend client tests cover incident routes and query/cursor transmission. A browser check with an ignored offline HTTP fixture also verified filter resets (including returning to earlier filters), continuation after an empty page, selection persistence, group drill-down, masked inspection and Viewer restrictions. Fixtures and runtime evidence are not committed.
