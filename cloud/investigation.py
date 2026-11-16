"""Sample-scoped incident indexing and bounded metadata-only investigation reads."""
import base64
import json
import re

from cloud.aws import new_item, wire
from cloud.contracts import CloudProblem, encode, sha


CATEGORIES = {"UNCLASSIFIED": "Unclassified failure", "INVALID_SCHEMA": "Invalid shipment schema"}
WINDOW_SECONDS = 3600
QUERY_PAGE_SIZE = 100
MAX_QUERY_PAGES = 4
MESSAGE_FIELDS = ("id", "operationId", "valid", "error", "errorCategory", "incidentId", "checksum",
                  "evidenceChecksum", "originalMessageId", "observedAt", "sourceSentAt", "expires_at")
INCIDENT_FIELDS = ("id", "signature", "category", "label", "consumerVersion", "windowStart", "windowEnd",
                   "groupingVersion", "expires_at", "collectedCount")


def public_incident(row):
    return {key: row[key] for key in INCIDENT_FIELDS if key in row}


def category(row):
    # A DLQ body does not prove the consumer's exception or root cause. Ignore
    # arbitrary payload/attribute error strings, including legacy timeout labels.
    return "INVALID_SCHEMA" if row.get("valid") is False else "UNCLASSIFIED"


def incident_writes(repo, item):
    """Commit membership and count with the unique snapshot, never separately."""
    start = item["observedAt"] // WINDOW_SECONDS * WINDOW_SECONDS
    signature = sha(encode([1, repo.queue_pk, repo.settings.consumer_version, category(item)]))
    identifier = sha(encode([signature, start]))
    item.update(incidentId=identifier, errorCategory=category(item))
    summary = {"id": identifier, "signature": signature, "category": category(item),
               "label": CATEGORIES[category(item)], "consumerVersion": repo.settings.consumer_version,
               "windowStart": start, "windowEnd": start + WINDOW_SECONDS, "groupingVersion": 1,
               "expires_at": start + WINDOW_SECONDS + repo.settings.retention_days * 86400}
    names = {"#" + key: key for key in summary}
    values = {":" + key: value for key, value in summary.items()}
    values[":one"] = 1
    count_update = {"Update": {"TableName": repo.table,
        "Key": wire({"pk": repo.queue_pk, "sk": "INC#" + identifier}),
        "UpdateExpression": "SET " + ", ".join("#" + key + "=:" + key for key in summary) + " ADD #count :one",
        "ExpressionAttributeNames": {**names, "#count": "collectedCount"},
        "ExpressionAttributeValues": wire(values)}}
    member = {**item, "pk": "INCIDENT#" + identifier}
    return [new_item(repo.table, item), new_item(repo.table, member), count_update]


def incident_detail(repo, identifier):
    if not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise CloudProblem(400, "Invalid incident ID")
    row = repo.get(repo.queue_pk, "INC#" + identifier)
    if not row or row.get("expires_at", 0) <= repo.clock():
        raise CloudProblem(404, "Incident not found or expired")
    return public_incident(row)


def filters_from_query(q):
    if set(q) - {"limit", "cursor", "q", "category", "schema", "observedFrom", "observedTo"}:
        raise CloudProblem(400, "Unsupported investigation filter")
    if any(not isinstance(value, str) for value in q.values()):
        raise CloudProblem(400, "Invalid investigation filter")
    raw_search = q.get("q", "")
    search = raw_search.strip().casefold()
    if len(raw_search) > 128 or any(ord(char) < 32 for char in raw_search):
        raise CloudProblem(400, "Search must contain at most 128 printable characters")
    selected_category, schema = q.get("category", ""), q.get("schema", "")
    if selected_category and selected_category not in CATEGORIES:
        raise CloudProblem(400, "Invalid failure category")
    if schema not in {"", "valid", "invalid"}:
        raise CloudProblem(400, "Invalid schema filter")
    try:
        limit = int(q.get("limit", "25"))
        if not 1 <= limit <= 100:
            raise ValueError()
        times = {key: int(q[key]) if q.get(key) else None for key in ("observedFrom", "observedTo")}
        if any(value is not None and not 0 <= value <= 253402300799 for value in times.values()):
            raise ValueError()
        if all(value is not None for value in times.values()) and times["observedFrom"] > times["observedTo"]:
            raise ValueError()
    except (ValueError, TypeError):
        raise CloudProblem(400, "Invalid page size or observation time range")
    return limit, {"q": search, "category": selected_category, "schema": schema, **times}


def matches(row, filters, now, incidents):
    if row.get("expires_at", 0) <= now:
        return False
    selected = row.get("category") if incidents else category(row)
    if filters["category"] and selected != filters["category"]:
        return False
    if filters["schema"] and (selected == "INVALID_SCHEMA") != (filters["schema"] == "invalid"):
        return False
    start = row["windowStart"] if incidents else row["observedAt"]
    end = row["windowEnd"] - 1 if incidents else start
    if filters["observedFrom"] is not None and end < filters["observedFrom"]:
        return False
    if filters["observedTo"] is not None and start > filters["observedTo"]:
        return False
    fields = ("id", "label", "category", "consumerVersion") if incidents else ("id", "operationId", "originalMessageId")
    text = " ".join(str(row.get(key) or "") for key in fields) + " " + CATEGORIES.get(selected, "")
    return filters["q"] in text.casefold()


def read_cursor(value, scope, pk, prefix):
    if not value:
        return None
    try:
        if len(value) > 4096:
            raise ValueError()
        cursor = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
        key = cursor["key"]
        if (set(cursor) != {"v", "scope", "key"} or cursor["v"] != 1 or cursor["scope"] != scope
                or not isinstance(key, dict) or set(key) != {"pk", "sk"} or key["pk"] != pk
                or not isinstance(key["sk"], str) or not key["sk"].startswith(prefix)):
            raise ValueError()
        return key
    except (ValueError, TypeError, KeyError):
        raise CloudProblem(400, "Pagination cursor does not match this investigation and its filters")


def browse(repo, q, incidents=False, incident_id=None):
    limit, filters = filters_from_query(q)
    pk, prefix = repo.queue_pk, "INC#" if incidents else "MSG#"
    if incident_id:
        incident_detail(repo, incident_id)  # Queue-scoped authorization before reading membership.
        pk = "INCIDENT#" + incident_id
    scope = sha(encode([pk, prefix, filters]))
    cursor = read_cursor(q.get("cursor"), scope, pk, prefix)
    items, evaluated = [], 0
    now = repo.clock()
    next_key = None
    for _ in range(MAX_QUERY_PAGES):
        rows, continuation = repo.query(pk, prefix, limit=QUERY_PAGE_SIZE, cursor=cursor)
        evaluated += len(rows)
        for index, row in enumerate(rows):
            if matches(row, filters, now, incidents):
                public = public_incident(row) if incidents else {
                    key: row[key] for key in MESSAGE_FIELDS if key in row}
                if not incidents:
                    public["errorCategory"] = category(row)
                    public["error"] = "InvalidSchema" if category(row) == "INVALID_SCHEMA" else "UnclassifiedFailure"
                items.append(public)
            if len(items) == limit:
                # Resume after the last examined row, not the fetched page end;
                # otherwise matching rows in an over-fetched page would be lost.
                next_key = {"pk": row["pk"], "sk": row["sk"]} if index < len(rows) - 1 or continuation else None
                break
        if len(items) == limit:
            break
        cursor = next_key = continuation
        if not continuation:
            break
    token = base64.urlsafe_b64encode(encode({"v": 1, "scope": scope, "key": next_key}).encode()).decode() if next_key else None
    return {"items": items, "nextCursor": token, "evaluatedCount": evaluated,
            "readBudgetReached": bool(token and len(items) < limit), "scope": "INDEXED_SNAPSHOTS",
            "grouping": "Queue, schema category, consumer version and UTC collection hour; not confirmed root cause"}
