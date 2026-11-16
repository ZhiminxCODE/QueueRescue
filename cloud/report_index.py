"""Incident-to-plan/job references committed with recovery actions, not scans."""
import re

from cloud.aws import new_item


def incident_links(repo, messages):
    groups = {}
    for message in messages:
        identifier = message.get("incidentId")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
            continue
        group = groups.setdefault(identifier, {"incidentId": identifier, "messageIds": [],
            "expires_at": message["observedAt"] // 3600 * 3600 + 3600 + repo.settings.retention_days * 86400})
        group["messageIds"].append(message["id"])
    return list(groups.values())


def link_writes(repo, links, kind, identifier, plan_id=None):
    return [new_item(repo.table, {"pk": "INCIDENT#" + link["incidentId"], "sk": kind + "#" + identifier,
        "id": identifier, "messageIds": link["messageIds"], "expires_at": link["expires_at"],
        "reportIndexVersion": 1, **({"planId": plan_id} if plan_id else {})}) for link in links]
