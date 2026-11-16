"""Read-only standard-SQS expiry estimates, separate from snapshot access TTL."""
import re

from botocore.exceptions import BotoCoreError, ClientError

from cloud.contracts import CloudProblem
from cloud.telemetry import queue_arn


WARNING_SECONDS = 86400
MAX_TIMESTAMP = 253402300799


def timestamp(value):
    return value if type(value) is int and 0 < value <= MAX_TIMESTAMP else None


def sent_seconds(value):
    # Missing or malformed system metadata must not prevent evidence capture.
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,15}", value):
        return 0
    return timestamp(int(value) // 1000) or 0


def queue_retention(repo):
    result = {"status": "UNKNOWN", "checkedAt": int(repo.clock()), "periodSeconds": None}
    expected = queue_arn(repo.settings.dlq_url)
    if not expected or repo.settings.dlq_url.endswith('.fifo'):
        return {**result, "status": "UNSUPPORTED_QUEUE"}
    try:
        attrs = repo.clients.sqs.get_queue_attributes(QueueUrl=repo.settings.dlq_url,
            AttributeNames=["MessageRetentionPeriod", "QueueArn"]).get("Attributes", {})
        if not isinstance(attrs, dict):
            return {**result, "status": "INVALID_METADATA"}
        period = attrs.get("MessageRetentionPeriod")
        if (attrs.get("QueueArn") != expected or not isinstance(period, str)
                or not re.fullmatch(r"[0-9]{2,7}", period) or not 60 <= int(period) <= 1209600):
            return {**result, "status": "INVALID_METADATA"}
        return {"status": "OBSERVED", "checkedAt": int(repo.clock()), "periodSeconds": int(period)}
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        return {**result, "status": "DENIED" if code in {"AccessDenied", "AccessDeniedException"} else
                "MISSING_QUEUE" if code in {"AWS.SimpleQueueService.NonExistentQueue", "QueueDoesNotExist"} else "UNAVAILABLE"}
    except BotoCoreError:
        return {**result, "status": "UNAVAILABLE"}


def snapshot_access(message, now):
    deadline = timestamp(message.get("expires_at"))
    if deadline is None:
        return {"status": "UNKNOWN", "accessExpiresAt": None, "remainingSeconds": None}
    remaining = max(0, deadline - now)
    return {"status": "EXPIRED" if deadline <= now else "NEAR_EXPIRY" if remaining <= WARNING_SECONDS else "WITHIN_ACCESS_WINDOW",
            "accessExpiresAt": deadline, "remainingSeconds": remaining}


def require_snapshot_access(message, now):
    status = snapshot_access(message, now)["status"]
    if status == "EXPIRED":
        raise CloudProblem(410, "Snapshot access retention has expired; payload inspection is disabled")
    if status == "UNKNOWN":
        raise CloudProblem(409, "Snapshot access retention is unknown; payload inspection is disabled")


def describe(message, observation, now):
    sent = timestamp(message.get("sourceSentAt"))
    observed = timestamp(message.get("observedAt"))
    if sent is not None and (sent > now or (observed is not None and sent > observed)):
        sent = None
    original = {"status": "UNKNOWN", "enqueueAgeSeconds": now - sent if sent is not None else None,
                "sentAt": sent, "estimatedExpiresAt": None, "remainingSeconds": None,
                "presence": "UNVERIFIED", "reason": "TIMESTAMP_UNKNOWN" if sent is None else "RETENTION_UNAVAILABLE"}
    period = observation.get("periodSeconds")
    if (sent is not None and observation.get("status") == "OBSERVED"
            and type(period) is int and 60 <= period <= 1209600):
        deadline = sent + period
        remaining = max(0, deadline - now)
        original.update(status="EXPIRY_ESTIMATE_PASSED" if deadline <= now else
                        "NEAR_EXPIRY" if remaining <= WARNING_SECONDS else "ESTIMATED_WINDOW_OPEN",
                        estimatedExpiresAt=deadline, remainingSeconds=remaining, reason=None)
    return {"asOf": now, "warningThresholdSeconds": WARNING_SECONDS, "queue": observation,
            "original": original, "snapshot": {**snapshot_access(message, now), "availability": "NOT_CHECKED"}}
