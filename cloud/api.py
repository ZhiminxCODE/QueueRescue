"""JWT-authenticated AWS API. The local demo session endpoint does not exist here."""
import base64
import json

from botocore.exceptions import BotoCoreError, ClientError

from backend.service import mask
from cloud.aws import runtime
from cloud.contracts import CloudProblem, actor_from_gateway, cursor_decode, cursor_encode, encode, request_identity
from cloud.recovery import approve_plan, control_job, create_plan, ensure_execution, job_detail, required, start_job, verify_job


def route(repo, event):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "").removeprefix("/api")
    permission = "read" if method == "GET" else "approve" if path.endswith("/approve") else "operate"
    actor = actor_from_gateway(event, permission)
    q = event.get("queryStringParameters") or {}
    if method == "GET":
        if path == "/session":
            return actor
        if path == "/connections":
            return {"items":[{"id":repo.queue_pk,"dlqUrl":repo.settings.dlq_url,"destination":repo.settings.replay_queue_url,
                              "region":repo.settings.region,"mode":"AWS_SANDBOX","consumerVersion":repo.settings.consumer_version}]}
        if path in {"/messages","/replay-plans","/replay-jobs","/audit"}:
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
            identifier = path.split("/")[2]
            message = repo.get(repo.queue_pk, "MSG#" + identifier)
            if not message:
                raise CloudProblem(404, "Snapshot not found")
            snapshot = repo.evidence(message["evidenceKey"], message["evidenceChecksum"])
            try:
                payload = mask(json.loads(snapshot["body"]))
            except ValueError:
                payload = "[MALFORMED PAYLOAD REDACTED]"
            return {**message,"payload":payload,"originalsRetained":True}
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
    return {"statusCode":code,"headers":{"content-type":"application/json","cache-control":"no-store"},"body":encode(result)}
