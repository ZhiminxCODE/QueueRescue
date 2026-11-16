"""JWT-authenticated AWS API. The local demo session endpoint does not exist here."""
import base64
import json

from botocore.exceptions import BotoCoreError, ClientError

from backend.service import mask
from cloud.aws import runtime
from cloud.contracts import CloudProblem, actor_from_gateway, cursor_decode, cursor_encode, encode, request_identity
from cloud.investigation import browse, incident_detail
from cloud.log_evidence import lookup
from cloud.recovery import approve_plan, control_job, create_plan, ensure_execution, job_detail, required, start_job, verify_job
from cloud.telemetry import emit, reference
from cloud.retention import describe, queue_retention, require_snapshot_access
from cloud.reports import report as incident_report
from cloud.report_costs import record_cost


def route(repo, event):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "").removeprefix("/api")
    permission = "read" if method == "GET" else "admin" if path.startswith("/incidents/") and path.endswith("/cost-observations") else "approve" if path.endswith("/approve") else "operate"
    actor = actor_from_gateway(event, permission)
    q = event.get("queryStringParameters") or {}
    if method == "GET":
        if path == "/session":
            return actor
        if path == "/connections":
            return {"items":[{"id":repo.queue_pk,"dlqUrl":repo.settings.dlq_url,"destination":repo.settings.replay_queue_url,
                              "region":repo.settings.region,"mode":"AWS_SANDBOX","consumerVersion":repo.settings.consumer_version}]}
        if path in {"/messages", "/incidents"}:
            return browse(repo, q, incidents=path == "/incidents")
        if path.startswith("/incidents/"):
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[2] == "messages":
                return browse(repo, q, incident_id=parts[1])
            if len(parts) == 3 and parts[2] == "report":
                return incident_report(repo, parts[1], q)
            if len(parts) == 2:
                return incident_detail(repo, parts[1])
            raise CloudProblem(404, "Route not found")
        if path in {"/replay-plans","/replay-jobs","/audit"}:
            try:
                limit = int(q.get("limit", 25))
            except (ValueError, TypeError):
                raise CloudProblem(400, "Invalid page size")
            pk, prefix, table = repo.queue_pk, "MSG#", None
            if path == "/replay-plans":
                pk, prefix = "CATALOG#PLAN", "plan-"
            elif path == "/replay-jobs":
                pk, prefix = "CATALOG#JOB", "job-"
            elif path == "/audit":
                pk, prefix, table = "AUDIT", "", repo.settings.audit_table
            rows, cursor = repo.query(pk, prefix, limit=limit, cursor=cursor_decode(q.get("cursor")), table=table)
            return {"items":rows,"nextCursor":cursor_encode(cursor)}
        if path.startswith("/messages/"):
            parts = path.strip("/").split("/")
            identifier = parts[1]
            if len(parts) == 3 and parts[2] == "evidence":
                return lookup(repo, identifier, q)
            if len(parts) != 2:
                raise CloudProblem(404, "Route not found")
            message = repo.get(repo.queue_pk, "MSG#" + identifier)
            if not message:
                raise CloudProblem(404, "Snapshot not found")
            require_snapshot_access(message, int(repo.clock()))
            try:
                snapshot = repo.evidence(message["evidenceKey"], message["evidenceChecksum"])
            except ClientError as error:
                code = error.response.get("Error", {}).get("Code")
                if code in {"NoSuchKey", "NoSuchVersion", "404"}:
                    raise CloudProblem(410, "Snapshot evidence is no longer available in storage") from error
                if code in {"AccessDenied", "AccessDeniedException", "403"}:
                    raise CloudProblem(503, "Snapshot storage access is unavailable; payload inspection could not be authorized") from error
                raise
            require_snapshot_access(message, int(repo.clock()))
            try:
                payload = mask(json.loads(snapshot["body"]))
            except ValueError:
                payload = "[MALFORMED PAYLOAD REDACTED]"
            observation = queue_retention(repo)
            now = int(repo.clock())
            require_snapshot_access(message, now)
            retention = describe(message, observation, now)
            retention["snapshot"]["availability"] = "READ_SUCCEEDED"
            return {**message,"payload":payload,"sourceDeletionPerformed":False,"originalPresence":"UNVERIFIED","retention":retention}
        if path.startswith("/replay-plans/"):
            return required(repo, "PLAN#" + path.split("/")[2])
        if path.startswith("/replay-jobs/"):
            return job_detail(repo, path.split("/")[2])
        raise CloudProblem(404, "Route not found")
    if method != "POST":
        raise CloudProblem(405, "Method not allowed")
    raw = event.get("body") or "{}"
    try:
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw, validate=True).decode()
        if len(raw.encode()) > 65536:
            raise ValueError()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError()
    except (ValueError, TypeError, UnicodeError):
        raise CloudProblem(400, "Body must be a JSON object of at most 64 KiB")
    request = request_identity(actor, path, event.get("headers") or {}, data)
    cached = repo.cached(*request)
    if cached is not None:
        if path == "/replay-jobs" or path.endswith("/resume"):
            cached["executionArn"] = ensure_execution(repo, cached)
        return cached
    if path.startswith("/incidents/") and path.endswith("/cost-observations"):
        parts = path.strip("/").split("/")
        if len(parts) != 3:
            raise CloudProblem(404, "Route not found")
        return record_cost(repo, actor, parts[1], data, request)
    if path == "/collections":
        if data.get("acknowledgeVisibilityChange") is not True:
            raise CloudProblem(400, "Explicitly acknowledge collection visibility changes")
        if not repo.settings.collector_arn:
            raise CloudProblem(503, "Collector Lambda is not configured")
        invoked = repo.clients.verifier.invoke(FunctionName=repo.settings.collector_arn, InvocationType="RequestResponse",
            Payload=encode({"acknowledgeVisibilityChange":True}).encode())
        if invoked.get("FunctionError"):
            raise CloudProblem(503, "Collector unavailable; retry with the same Idempotency-Key")
        result = json.loads(invoked["Payload"].read())
        repo.commit([], actor["id"], "COLLECTION_REQUESTED", repo.queue_pk, request=request, response=result)
        return result
    if path == "/replay-plans":
        return create_plan(repo, actor, data, request)
    if path.startswith("/replay-plans/") and path.endswith("/approve"):
        return approve_plan(repo, actor, path.split("/")[2], data, request)
    if path == "/replay-jobs":
        result = start_job(repo, actor, data, request)
        result["executionArn"] = ensure_execution(repo, result)
        return result
    if path.startswith("/replay-jobs/") and path.split("/")[-1] in {"pause","resume","cancel"}:
        action = path.split("/")[-1]
        result = control_job(repo, actor, path.split("/")[2], action, data, request)
        if action == "resume":
            result["executionArn"] = ensure_execution(repo, result)
        return result
    if path == "/verifications":
        return verify_job(repo, actor, str(data.get("jobId", "")), request)
    raise CloudProblem(404, "Route not found")


def handler(event, context):
    try:
        result = route(runtime(), event)
        code = 200
    except CloudProblem as error:
        result, code = {"error":error.message}, error.status
    except (BotoCoreError, ClientError):
        # The request may have committed; clients must retry with the same key.
        result, code = {"error":"AWS request unavailable; retry with the same Idempotency-Key"}, 503
    emit("api", "API_COMPLETED", statusCode=code, requestRef=reference("request", getattr(context, "aws_request_id", None)))
    return {"statusCode":code,"headers":{"content-type":"application/json","cache-control":"no-store"},"body":encode(result)}
