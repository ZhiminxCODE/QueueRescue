"""Shared AWS contracts; pure validation and serialization, no SDK clients."""
from dataclasses import dataclass
import hashlib
import json
import os
import re


MAX_PLAN_MESSAGES = 25
OUTCOMES = {"COMPLETE", "NOT_STARTED", "IN_PROGRESS", "UNKNOWN"}
PERMISSIONS = {
    "Viewer": {"read"}, "Operator": {"read", "operate"},
    "Approver": {"read", "approve"}, "Admin": {"read", "operate", "approve", "admin"},
}


class CloudProblem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode("utf-8")).hexdigest()


def validate_event(body):
    try:
        event = json.loads(body)
        valid = isinstance(event, dict) and type(event.get("schemaVersion")) is int and event["schemaVersion"] == 1 and event.get("eventType") == "SHIP_ORDER"
        valid = valid and all(isinstance(event.get(k), str) and 0 < len(event[k]) <= 256 for k in ("operationId", "eventId", "orderId", "warehouseId"))
        if not valid:
            raise ValueError()
        return event
    except (ValueError, TypeError, KeyError):
        raise CloudProblem(422, "Message does not satisfy the shipment event schema")


def actor_from_gateway(event, permission):
    # Trust only the authorizer context produced by API Gateway, never body/header roles.
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise CloudProblem(401, "An authenticated Cognito access token is required")
    if claims.get("token_use") != "access":
        raise CloudProblem(401, "Use a Cognito access token, not an ID token")
    groups = claims.get("cognito:groups", [])
    if isinstance(groups, str):
        try:
            groups = json.loads(groups)
        except ValueError:
            groups = groups.strip("[]").split(",")
    if not isinstance(groups, list):
        groups = []
    groups = [g.strip() for g in groups if isinstance(g, str)]
    granted = set().union(*(PERMISSIONS.get(g, set()) for g in groups))
    if permission not in granted:
        raise CloudProblem(403, "Identity lacks the required permission")
    return {"id": subject, "groups": sorted(set(groups))}


def request_identity(actor, route, headers, body):
    headers = {k.lower(): v for k, v in headers.items()}
    key = headers.get("idempotency-key", "")
    if not isinstance(key, str) or not 8 <= len(key) <= 200:
        raise CloudProblem(400, "Idempotency-Key must contain 8–200 characters")
    return "REQ#" + sha(actor["id"] + "\n" + key), sha(encode([route, body]))


def cursor_encode(key):
    import base64
    return base64.urlsafe_b64encode(encode(key).encode()).decode() if key else None


def cursor_decode(value):
    import base64
    if not value:
        return None
    try:
        key = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
        if not isinstance(key, dict) or set(key) != {"pk", "sk"} or any(not isinstance(v, str) for v in key.values()):
            raise ValueError()
        return key
    except (ValueError, TypeError):
        raise CloudProblem(400, "Invalid pagination cursor")


@dataclass(frozen=True)
class Settings:
    region: str
    account_id: str
    ledger_table: str
    business_table: str
    audit_table: str
    evidence_bucket: str
    kms_key_arn: str
    source_queue_url: str
    dlq_url: str
    replay_queue_url: str
    verifier_arn: str
    state_machine_arn: str
    consumer_version: str = "shipment-demo-v1"
    retention_days: int = 7
    collector_arn: str = ""

    @classmethod
    def from_env(cls):
        names = ["region", "account_id", "ledger_table", "business_table", "audit_table", "evidence_bucket", "kms_key_arn", "source_queue_url", "dlq_url", "replay_queue_url", "verifier_arn", "state_machine_arn"]
        values = {name: os.environ["QR_" + name.upper()] for name in names}
        settings = cls(**values, collector_arn=os.environ.get("QR_COLLECTOR_ARN", ""),
                       consumer_version=os.environ.get("QR_CONSUMER_VERSION", "shipment-demo-v1"))
        if not re.fullmatch(r"\d{12}", settings.account_id):
            raise ValueError("Invalid target AWS account")
        for url in [settings.source_queue_url, settings.dlq_url, settings.replay_queue_url]:
            expected = "https://sqs." + settings.region + ".amazonaws.com/" + settings.account_id + "/"
            if not url.startswith(expected) or url.endswith(".fifo"):
                raise ValueError("Queue must be an allowlisted standard queue in the target account and region")
        return settings
