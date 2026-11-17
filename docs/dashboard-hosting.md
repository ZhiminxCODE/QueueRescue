# Optional AWS dashboard hosting

Status: implemented and verified offline; not deployed. The running local application is independent and remains unchanged. This hosts the explicit AWS dashboard, never the local simulator or its demo persona selector.

## Controls and boundaries

Both Terraform switches default to `false`:

- `dashboard_hosting_enabled` provisions a private, encrypted, versioned S3 bucket, CloudFront Origin Access Control (OAC), exact-route edge function, cache/header policies and distribution. It also adds the exact hosted callback/logout URL to Cognito and the corresponding origin to API CORS.
- `dashboard_delivery_enabled` enables the distribution only when hosting is provisioned. Keep delivery off until a reviewed release is uploaded. Disabling delivery does not delete retained objects or stop all costs.

CloudFront serves the public static shell and six validated public browser settings. API data still requires Cognito access tokens and server-enforced roles; private S3 does not make the delivered shell private. All S3 public access is blocked, ACLs are disabled, and only this distribution receives an explicit service-principal grant to read `index.html`, `connect-origins.json` and `assets/*`. OAC always signs regional REST-origin requests with SigV4 over HTTPS. The bucket denies insecure transport and cannot be automatically emptied by Terraform. See [AWS OAC requirements](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html).

The canonical browser path is `https://DISTRIBUTION.cloudfront.net/callback`. `/`, `/index.html` and `/callback/` redirect there without copying query parameters. Only the exact `/callback` origin request rewrites to `/index.html`; the browser retains its PKCE code/state for the existing authentication code. Unknown paths and missing assets retain their errors: no blanket error-to-HTML fallback. Cognito requires an [exact registered callback](https://docs.aws.amazon.com/cognito/latest/developerguide/authorization-endpoint.html). Existing localhost callbacks remain registered; remove them in a separately reviewed configuration if no longer needed.

Shell cache TTLs are all zero, with `Cache-Control: no-store`. Hashed assets permit immutable one-year browser caching. Neither behavior forwards cookies or query strings to S3 or includes them in cache keys. Standard distribution access logging is not configured, and the edge function emits no logs, to avoid capturing OAuth query credentials. Review any account-level/real-time logging separately before enabling it. HTTP redirects to HTTPS. This profile uses the managed CloudFront hostname/certificate and PriceClass_100, without custom DNS or ACM resources. AWS [automatically selects its TLSv1 security policy for the default certificate](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/DownloadDistValuesGeneral.html); this does not enforce a TLS 1.2 minimum. A stricter minimum needs a separately reviewed custom-domain/certificate profile.

Response headers deny framing, suppress referrers, enable nosniff and HSTS. The release HTML supplies a CSP with same-origin scripts, no objects/base/forms, and exact API/Cognito connection origins. Inline styles are allowed for existing React styles. The response CSP header supplies `frame-ancestors` because HTML meta policies cannot enforce it. Keeping the infrastructure header independent of API/Cognito avoids a dependency cycle through the distribution hostname, API CORS and Cognito callbacks. This remains a browser-token prototype with sessionStorage tokens; CSP does not replace API authorization.

## Prepare a release offline

After a separately reviewed provisioning apply, save private Terraform deployment outputs to ignored `.data/aws-deployment.json`. The following command only builds files; it makes no AWS calls:

```sh
node tools/prepare_dashboard_release.mjs \
  --deployment .data/aws-deployment.json \
  --output .data/dashboard-release-001
```

The tool validates the hosted callback, service URLs, account and dedicated bucket, then builds into new ignored storage. It refuses existing output and symlinked parent directories. It does not change `.env.local`, `dist/`, source files or running processes. It disables local Vite config/plugins, `.env` loading, public-directory copying and source maps. Inherited `VITE_`, `AWS_` and `QR_` variables are removed from its own process before the six allowlisted public Vite settings are supplied; Vite otherwise gives [existing environment variables priority](https://vite.dev/guide/env-and-mode.html).

`site/` contains only the CSP-bearing `index.html`, matching `connect-origins.json` and hashed JavaScript/CSS. The adjacent `manifest.json` records each file's SHA-256, size, content type and cache metadata, plus the target bucket/distribution/callback. Keep the entire release ignored/private; upload only reviewed manifest-listed files from `site/`. Never sync the repository, `.data/`, manifests, source maps or local settings to S3. Preparation verifies file/configuration contracts, not live Cognito or CDN behavior.

## Reviewed publication and live checks

Do not publish until the [account readiness and cost gates](aws-deployment.md) are resolved. The baseline calculator explicitly reports `dashboard_hosting_included=false`; revise the estimate for S3 versions/requests/storage, CDN requests/transfer, edge invocations, any logging, invalidations and shared allowances. Verify actual account plan, credits, expiration, covered services, unrelated usage and notification delivery. A disabled distribution or budget alert is not a spending cap. No new Lambda reservation is introduced by hosting.

1. Review a concrete saved plan with hosting on, delivery off, consumers/replay off and audit switches explicitly chosen. Check scoped S3/CloudFront deployment permissions, exact account/region, resource counts and the complete cost worksheet before apply. Provisioning can incur charges even with delivery off.
2. Export fresh outputs, prepare the offline release and review its manifest/settings. Verify local hashes immediately before upload. Confirm the authenticated AWS identity and exact target bucket/distribution against the saved outputs. Use an authorized publishing identity restricted to this bucket and manifest keys; this implementation does not create a broad publishing role.
3. Upload manifest-listed hashed assets first with their recorded content type and `public,max-age=31536000,immutable`; upload `connect-origins.json` next and `index.html` last, both with `no-store` and their recorded content types. Do not use `--delete` or upload the adjacent manifest. Verify remote metadata and contents before activation. Upload ordering retains assets needed by both old and new shells; this is not an atomic multi-object release.
4. Review/apply delivery on with workers still off. Wait for CloudFront status `Deployed`; a successful Terraform plan is not proof of edge propagation. No automatic invalidation is provided. Shell caching is disabled; assets have content hashes. Review any separately requested invalidation's scope and cost.
5. In a real browser, confirm the root redirect, exact `/callback` refresh, missing-path/asset errors, HTTPS and response/CSP/cache headers; verify direct S3 access fails. Confirm the HTML and assets match the release. Test real Cognito PKCE, MFA, sign-out, token expiry, API CORS, unauthorized rejection and distinct Operator/Approver identities. Verify no OAuth query credentials appear in enabled logging. Keep resulting evidence and personal URLs/account settings ignored. Only then proceed with the separately reviewed bounded recovery demo.

Offline evidence: 33 frontend tests (including a real isolated Vite release build and environment/output guards), 26 mocked Terraform plans (four hosting cases), Terraform schema validation and 186 Python regressions. CI contains these test commands, but no remote CI result is claimed here. Service acceptance, actual headers/policies, propagation, authorization and billing remain live verification work.

## Rollback, idle and teardown

Keep a previous complete release while it is needed. For rollback, verify its manifest and target, restore its assets/configuration and publish its shell last using the same metadata. Disabling delivery is a separate reviewed Terraform change and may take time to propagate; it does not revoke already downloaded static assets or tokens. Keep consumer polling and replay disabled while idle; optional audit capture has its own switch and retention costs.

S3 versioning protects prior object versions; the lifecycle rule expires noncurrent versions after 14 days. Older hashed assets with different keys remain current objects and require a reviewed cleanup inventory. Retention is finite, and lifecycle expiry limits rollback history. No automatic object deletion is provided. Before teardown, disable delivery, review retained evidence/callbacks, inventory current objects, all versions and delete markers, and approve any deletion. The bucket's `force_destroy=false` deliberately blocks removal while it contains objects. Removing hosting also removes its Cognito callback and API CORS origin; export any required configuration first. Follow the separate [audit retention runbook](cloud-audit.md) for audit resources. Confirm remaining resources and costs after teardown.
