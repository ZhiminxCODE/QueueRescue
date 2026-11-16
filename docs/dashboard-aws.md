# AWS dashboard integration

The dashboard supports two explicit build configurations. Local simulation is the default. AWS mode uses the existing cloud API and Cognito public app client; it never sends demo session tokens or AWS root/IAM credentials. The AWS backend is still undeployed. Browser authentication and request contracts are tested offline, not evidence of a successful Cognito login or recovery in a live account.

## Connect a reviewed deployment

Finish the [cost and account readiness review](aws-cost-controls.md) and [deployment runbook](aws-deployment.md) before provisioning anything. Dashboard development does not require an AWS deployment. It does not resolve account quotas or establish Free Tier eligibility.

After an approved deployment, save its non-secret outputs locally:

```sh
mkdir -p .data
terraform -chdir=infra/aws output -json deployment > .data/aws-deployment.json
node tools/configure_dashboard.mjs
npm run dev
```

Open **http://localhost:5173/**. The helper validates the regional API Gateway and Cognito HTTPS domains and writes only six allowlisted public settings to ignored `.env.local`. It refuses to overwrite an existing file. The public app client must not have a client secret. Never put AWS keys, tokens, passwords, or client secrets in a `VITE_` variable: browser build configuration is public.

For a built dashboard served by `npm start` on port 8000, choose an unused output file or remove an obsolete `.env.local` after review, then generate the desired callback:

```sh
node tools/configure_dashboard.mjs --redirect-uri http://localhost:8000/
npm run build
npm start
```

The Terraform defaults include `http://localhost:5173/`, `http://localhost:8000/`, and the CLI helper's port-8765 callback. For an existing deployment, review and apply callback/CORS changes before using a newly added origin. Other frontend origins need explicit `callback_urls` entries, which also drive API CORS; HTTPS is required except for localhost development. Open the exact configured origin/path. `127.0.0.1` is not interchangeable with `localhost` in OAuth. There is no frontend hosting or production deployment added by this integration.

Copy [.env.example](../.env.example) to inspect the configuration schema. Restart Vite or rebuild after changing it. Missing/invalid AWS settings show a connection-configuration error and never silently fall back to simulated data. To return to local simulation, remove the AWS overrides from `.env.local` or set `VITE_QR_MODE=local`, then restart/rebuild.

## Application sign-in and approval

Create the Operator and separate Approver in the deployed Cognito pool as described in the deployment runbook. These application users are separate from the `queuerescue-dev` IAM deployment identity. The browser redirects to Cognito; passwords and MFA stay in its sign-in flow.

The app uses authorization code + PKCE (S256), a random single-use state, a ten-minute sign-in window, and an exact configured callback. It removes callback credentials from browser history before exchanging the code. Only the short-lived access token is retained in tab-scoped `sessionStorage`; ID and refresh tokens are discarded. Roles come from authenticated `/session`, not decoded browser claims or a role picker. Logout clears the local access session and visits Cognito logout. The user signs in again on expiry; access tokens can remain valid server-side until they expire. This prototype has no browser refresh-token/BFF session service.

Production builds emit `connect-origins.json` with only the validated public API and Cognito origins. The loopback static server uses that file for its Content Security Policy, allowing API requests and token exchange while keeping other connections blocked. Rebuild after changing dashboard settings; deploy the manifest together with the matching assets. Local-mode builds allow only same-origin connections. Vite development hosting does not use the loopback server's CSP.

After creating a plan, sign out/change application account, then sign in as the separate Approver. The selected plan and page are retained in the same tab/workspace so the approver can review the same hash. Self-approval is disabled in the UI and independently rejected by the backend. Sign back in as Operator to execute. No demo identity selector, injected crash option, arbitrary destination, or root/IAM sign-in appears in AWS workflows.

## Read and mutation behavior

- Reads happen on opening a view, explicit pagination, successful actions, and manual refresh. There is no background polling or automatic collection/replay. Job progress requires **Refresh view**.
- Messages and incident groups use server-side metadata search, schema/category/time filters, and opaque pagination. Apply filters explicitly; changing filters resets the page. An empty bounded search page can still have a Next page. Group drill-down lists indexed members and preserves explicit snapshot selection. The UI never infers a total queue size, confirmed root cause or business outcome from indexed metadata. See the [investigation contract](cloud-investigation.md).
- Collection requires explicit acknowledgment that receive operations change visibility. At most ten sampled IDs are returned; duplicates and incomplete queue inventory are possible. Originals remain retained.
- Plans submit 1–25 explicit snapshot IDs with an integer rate of 1–25. Decision reasons and the complete immutable document/hash are available for review/export.
- Catalog endpoints return IDs, not complete plan/job objects. The dashboard fetches selected details separately and uses the backend's field names and state transitions.
- Mutation intent (route, body, actor, key, and timestamp) is saved before sending. An unavailable/ambiguous response blocks new mutations and offers **Retry same request**. Reloads and re-authentication retain the original key; a different actor cannot retry it. Requests older than six days require administrator reconciliation rather than risking the server's seven-day cache expiry. Do not clear this storage to bypass an unresolved request.
- Calls use the pinned regional HTTPS endpoint, omit cookies, refuse redirects, and bound network waits. A 401 returns to sign-in. No tokens appear in URLs, exports, or diagnostic notices.
- Snapshot inspection and each replay-attempt row offer explicit CloudWatch evidence lookup. Opening a dialog does not query logs. Lookups accept a bounded local-time window, display per-source coverage and inconclusive results, and link only to returned log streams. Log evidence never changes business-verification counts. [Contract and offline verification](cloud-log-evidence.md).
- The inbox and snapshot dialog show original enqueue age/DLQ expiry estimates separately from snapshot access deadlines, with warnings within 24 hours. Estimates identify their observation time and require manual refresh. Unknown settings and original-message presence remain explicit; failed inspection refresh closes the prior payload view. [Retention contract and offline verification](cloud-retention.md).
- Incident groups offer **Incident report**, with recorded outcome counts, verification coverage, job duration, findings, bounded optional log sampling and JSON/Markdown export. Missing history and unmeasured costs remain explicit. Only Admins can append audited cost observations; estimates are separate from declared billing measurements and neither is independently validated by the application. [Reporting contract](cloud-incident-reports.md).
- Audit exports contain the current server page; job exports are ledger snapshots, not complete incident reports. The UI does not claim cloud hash-chain validation, complete inventory, distributed traces, independently confirmed root causes, or live worker availability.

## Verification

```sh
npm run test:frontend
npm run build
.venv/bin/python -m unittest discover -s tests -v
```

Frontend tests use native Node test tools and mocked HTTP responses. They exercise PKCE, state and expiry rejection, credential scrubbing, workspace/endpoint isolation, public-only configuration export, ambiguous mutation recovery after reload, actor binding, and denied routes. CI runs them without AWS credentials. Real Cognito redirect/CORS behavior and the full cloud recovery path remain deployment validation tasks.
