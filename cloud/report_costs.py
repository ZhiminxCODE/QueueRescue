"""Audited cost observations; never infer a zero bill or provider verification."""
import re

from cloud.aws import new_item
from cloud.contracts import CloudProblem
from cloud.investigation import incident_detail
from cloud.telemetry import reference


COST_FIELDS = ("id", "kind", "amountUSD", "sourceKind", "sourceSha256", "periodStart", "periodEnd", "observedAt", "recordedAt", "recordedByRef")


def public_cost(row):
    return {**{key: row[key] for key in COST_FIELDS if key in row},
            "provenance": "ADMIN_RECORDED", "providerVerified": False,
            "attribution": "ADMIN_ALLOCATED_TO_INCIDENT_WINDOW"}


def record_cost(repo, actor, identifier, data, request):
    incident = incident_detail(repo, identifier)
    fields = {"kind", "amountUSD", "sourceKind", "sourceSha256", "periodStart", "periodEnd", "observedAt"}
    if set(data) != fields:
        raise CloudProblem(400, "Supply a complete, structured cost observation")
    kind, source = data.get("kind"), data.get("sourceKind")
    if (not isinstance(kind, str) or not isinstance(source, str) or
            (kind == "MEASURED" and source != "AWS_BILLING_EXPORT") or
            (kind == "ESTIMATED" and source not in {"AWS_USAGE_EXPORT", "OFFLINE_CALCULATOR", "MANUAL_ESTIMATE"}) or
            kind not in {"MEASURED", "ESTIMATED"}):
        raise CloudProblem(400, "Label billing measurements separately from usage-derived or manual estimates")
    amount, checksum = data.get("amountUSD"), data.get("sourceSha256")
    if (not isinstance(amount, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,8})?", amount)
            or not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum)):
        raise CloudProblem(400, "Cost requires a nonnegative decimal USD string and a source artifact SHA-256")
    start, end, observed = (data.get(key) for key in ("periodStart", "periodEnd", "observedAt"))
    now = int(repo.clock())
    if (any(type(value) is not int for value in (start, end, observed))
            or not 0 <= start < end <= observed <= now
            or end < incident["windowStart"] or start >= incident["windowEnd"]):
        raise CloudProblem(400, "The observed cost window must overlap this incident and cannot extend into the future")
    identifier_cost = "cost-" + request[0][-24:]
    row = {"pk": "INCIDENT#" + identifier, "sk": "COST#" + str(now).zfill(12) + "#" + identifier_cost,
           "id": identifier_cost, **data, "recordedAt": now, "recordedByRef": reference("actor", actor["id"]),
           "expires_at": incident["expires_at"]}
    response = public_cost(row)
    repo.commit([new_item(repo.table, row)], actor["id"], "INCIDENT_COST_RECORDED", identifier,
                detail={"costId": identifier_cost, "kind": kind, "sourceSha256": checksum}, request=request, response=response)
    return response


def cost_section(rows, truncated):
    latest = {}
    for row in rows:
        kind = row.get("kind")
        if kind not in {"MEASURED", "ESTIMATED"} or kind in latest:
            continue
        latest[kind] = public_cost(row)
    return {"measurement": latest.get("MEASURED"), "estimate": latest.get("ESTIMATED"),
            "status": "RECORDED_OBSERVATIONS" if latest else "NOT_MEASURED",
            "historyTruncated": truncated, "incidentTotalUSD": None,
            "notice": "Amounts are admin-recorded allocations for their stated windows, not independently validated AWS bills. Missing amounts are unknown, never zero; estimates are not measurements or spending caps. Retained resources can incur later charges."}
