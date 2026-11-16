"""Bounded CloudWatch correlation against configured groups, never arbitrary logs."""
import json
import re
from urllib.parse import quote

from botocore.exceptions import BotoCoreError, ClientError

from cloud.contracts import CloudProblem, encode
from cloud.telemetry import message_refs, queue_arn, reference, sanitized


MAX_WINDOW_SECONDS = 86400
MAX_PAGES_PER_GROUP = 2
PAGE_SIZE = 25


def console_link(region, group, stream):
    if not re.fullmatch(r"[a-z]{2}-[a-z]+-\d+", region):
        return None
    if not isinstance(stream, str) or not 0 < len(stream) <= 512 or any(ord(c) < 32 for c in stream):
        return None
    escape = lambda text: quote(quote(text, safe=""), safe="").replace("%", "$")
    return (f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}"
            f"#logsV2:log-groups/log-group/{escape(group)}/log-events/{escape(stream)}")


def context_for(repo, identifier, query):
    if not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise CloudProblem(400, "Invalid snapshot ID")
    if (set(query) - {"jobId", "attemptId", "from", "to"}
            or any(not isinstance(value, str) for value in query.values())):
        raise CloudProblem(400, "Unsupported evidence lookup parameter")
    message = repo.get(repo.queue_pk, "MSG#" + identifier)
    now = int(repo.clock())
    if not message or message.get("expires_at", 0) <= now:
        raise CloudProblem(404, "Snapshot not found or expired")
    refs = message_refs(message.get("originalMessageId"), message.get("operationId"))
    correlation = message.get("correlationRef")
    if isinstance(correlation, str) and re.fullmatch(r"[a-f0-9]{64}", correlation):
        refs["correlationRef"] = correlation
    observed = message["observedAt"]
    sent = message.get("sourceSentAt")
    source_start = sent if type(sent) is int and 0 < sent <= observed else observed
    lower, upper = max(0, source_start - 300), min(now, observed + 300)
    mode = "SOURCE_MESSAGE"
    groups = [("consumer", repo.settings.consumer_log_group)]
    expected = {"queueRef": reference("queue", queue_arn(repo.settings.source_queue_url)), "messageRef": refs.get("messageRef")}
    if "jobId" in query or "attemptId" in query:
        job_id, attempt_id = query.get("jobId", ""), query.get("attemptId", "")
        if not re.fullmatch(r"job-[a-f0-9]{24}", job_id) or not re.fullmatch(r"attempt-[a-f0-9]{24}", attempt_id):
            raise CloudProblem(400, "Select an exact job and attempt pair")
        job = repo.get("JOB#" + job_id)
        attempt = repo.get("JOB#" + job_id, "ATTEMPT#" + identifier)
        plan = repo.get("PLAN#" + job["planId"]) if job else None
        decisions = plan.get("document", {}).get("decisions", []) if plan else []
        belongs = any(d.get("messageId") == identifier and d.get("checksum") == message["checksum"]
                      and d.get("evidenceChecksum") == message["evidenceChecksum"] for d in decisions)
        if (not attempt or attempt.get("id") != attempt_id or attempt.get("operationId") != message.get("operationId")
                or not belongs or plan["document"].get("destination") != repo.settings.replay_queue_url):
            raise CloudProblem(404, "Replay attempt does not belong to this snapshot and queue")
        mode = "REPLAY_ATTEMPT"
        lower, upper = max(0, job["createdAt"] - 300), now
        expected = {"queueRef": reference("queue", queue_arn(repo.settings.replay_queue_url)),
                    "operationRef": reference("operation", attempt["operationId"]),
                    "jobRef": reference("job", job_id), "attemptRef": reference("attempt", attempt_id)}
        refs.update(expected)
        groups.append(("replay", repo.settings.replay_log_group))
    if any(value is None for value in expected.values()):
        raise CloudProblem(409, "Snapshot lacks usable correlation metadata")
    try:
        end = int(query["to"]) if "to" in query else upper
        start = int(query["from"]) if "from" in query else max(lower, end - MAX_WINDOW_SECONDS)
        if not lower <= start <= end <= upper or end - start > MAX_WINDOW_SECONDS:
            raise ValueError()
    except ValueError:
        raise CloudProblem(400, "Evidence window must be within the resource lifetime and at most 24 hours")
    return mode, refs, expected, groups, start * 1000, end * 1000, start > lower or end < upper


def lookup(repo, identifier, query):
    mode, refs, expected, groups, start, end, clipped = context_for(repo, identifier, query)
    # Hash-only exact-match predicates prevent filter syntax injection. Raw
    # correlation attributes are never a sufficient membership boundary.
    pattern = "{ " + " && ".join(f"$.{key} = {encode(value)}" for key, value in expected.items()) + " }"
    sources, items, seen = [], [], set()
    for component, group in groups:
        source = {"component": component, "status": "NOT_CONFIGURED", "pagesRead": 0}
        sources.append(source)
        if not isinstance(group, str) or not re.fullmatch(r"/aws/lambda/[A-Za-z0-9_-]{1,64}", group) or repo.clients.logs is None:
            continue
        source["logGroup"] = group
        token = None
        try:
            for _ in range(MAX_PAGES_PER_GROUP):
                result = repo.clients.logs.filter_log_events(logGroupName=group, startTime=start, endTime=end,
                    filterPattern=pattern, limit=PAGE_SIZE, unmask=False, **({"nextToken": token} if token else {}))
                source["pagesRead"] += 1
                for event in result.get("events", [])[:PAGE_SIZE]:
                    try:
                        raw = event.get("message", "")
                        row = sanitized(json.loads(raw)) if isinstance(raw, str) and len(raw) <= 8192 else None
                    except (ValueError, TypeError, RecursionError):
                        row = None
                    timestamp = event.get("timestamp")
                    if (not row or row["component"] != component or type(timestamp) is not int
                            or not start <= timestamp <= end or not start <= row["at"] <= end
                            or any(row.get(key) != value for key, value in expected.items())):
                        continue
                    event_id = event.get("eventId")
                    identity = (group, event_id) if isinstance(event_id, str) else (group, event.get("logStreamName"), timestamp, encode(row))
                    if identity in seen:
                        continue
                    seen.add(identity)
                    link = console_link(repo.settings.region, group, event.get("logStreamName"))
                    items.append({**row, "observedAt": timestamp, **({"logUrl": link} if link else {})})
                next_token = result.get("nextToken")
                if not next_token:
                    source["status"] = "COMPLETE"
                    break
                source["status"] = "TRUNCATED"
                if next_token == token:
                    break
                token = next_token
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            source["status"] = ("DENIED" if code in {"AccessDenied", "AccessDeniedException", "UnrecognizedClientException"}
                                else "MISSING" if code == "ResourceNotFoundException" else "UNAVAILABLE")
        except BotoCoreError:
            source["status"] = "UNAVAILABLE"
    items.sort(key=lambda row: row["observedAt"])
    complete = not clipped and all(source["status"] == "COMPLETE" for source in sources)
    return {"status": "FOUND" if items else "INCONCLUSIVE", "mode": mode, "items": items, "sources": sources,
            "correlation": refs, "from": start // 1000, "to": end // 1000, "windowClipped": clipped,
            "searchComplete": complete, "failureObserved": any(row["event"] == "CONSUMER_FAILED" for row in items),
            "traceStatus": "NOT_INSTRUMENTED", "businessVerification": "NOT_ASSESSED",
            "notice": "Logs are diagnostic evidence only. Missing logs do not prove success; accepted sends and handler returns do not prove business recovery."}
