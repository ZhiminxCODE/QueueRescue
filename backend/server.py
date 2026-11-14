"""Loopback-only demo API and static frontend host. Python standard library only."""
import argparse
import json
import mimetypes
import math
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from backend.service import DESTINATION, QUEUE, Problem, Store, Worker, canonical, digest, require
from backend.simulator import ShipmentConsumer

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = {
    "Operator": {"name": "Alex Chen", "role": "Operator"},
    "Approver": {"name": "Morgan Lee", "role": "Approver"},
    "Viewer": {"name": "Sam Rivera", "role": "Viewer"},
    "Admin": {"name": "Taylor Brooks", "role": "Admin"},
}


def make_handler(store, port):
    sessions = {}

    class Handler(BaseHTTPRequestHandler):
        server_version = "QueueRescueLocal/0.1"

        def log_message(self, format, *args):
            # Default HTTP logs are disabled: paths and request bodies may be sensitive.
            pass

        def send_json(self, data, status=200):
            body = canonical(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def actor(self):
            auth = self.headers.get("Authorization", "")
            token = auth.removeprefix("Bearer ")
            session = sessions.get(token)
            if not auth.startswith("Bearer ") or not session or session["expires"] < time.time():
                raise Problem(401, "Select a local demo identity to continue.")
            return session["actor"]

        def validate_host(self):
            if self.headers.get("Host", "") not in {"127.0.0.1:" + str(port), "localhost:" + str(port), "localhost:5173", "127.0.0.1:5173"}:
                raise Problem(403, "Local demo accepts only loopback hosts.")

        def do_GET(self):
            try:
                self.validate_host()
                parsed = urlparse(self.path)
                path = parsed.path.removeprefix("/api")
                q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                if parsed.path == "/api/health":
                    return self.send_json({"status": "ok", "mode": "simulation"})
                if not parsed.path.startswith("/api/"):
                    return self.static(parsed.path)
                actor = self.actor()
                require(actor, "read")
                if path == "/session":
                    result = actor
                elif path == "/overview":
                    result = store.summary()
                elif path == "/connections":
                    result = {"items": [{"id": QUEUE, "name": QUEUE, "source": "shipment-orders",
                              "destination": DESTINATION, "region": "us-east-1", "type": "Standard",
                              "worker": "shipment-worker", "logGroup": "/aws/lambda/shipment-worker",
                              "verifier": "Local shipment status adapter", "consumerVersion": "shipment-simulator-v1",
                              "status": "Simulated", "idempotency": "operationId-unique", "environment": "Local demo"}]}
                elif path == "/incidents":
                    cursor, limit = self.pagination(q)
                    rows = store.incidents({k: v for k, v in q.items() if k not in {"cursor", "limit"}})
                    result = self.page(rows, cursor, limit)
                elif path == "/messages":
                    cursor, limit = self.pagination(q)
                    rows = store.selection({k: v for k, v in q.items() if k not in {"cursor", "limit"}})
                    result = {"items": [{"id": r["id"], "operationId": r["operation_id"], "error": r["error"],
                                         "observedAt": r["observed_at"], "outcome": store.outcome(r["operation_id"]),
                                         "decision": store.classify(r)[0]} for r in rows[cursor:cursor + limit]],
                              "total": len(rows), "nextCursor": str(cursor + limit) if cursor + limit < len(rows) else None}
                elif path.startswith("/messages/"):
                    result = store.message(path.split("/")[2])
                elif path == "/replay-plans":
                    result = {"items": [store.plan(r["id"]) for r in store.read("SELECT id FROM plans ORDER BY created_at DESC LIMIT 100")]}
                elif path.startswith("/replay-plans/"):
                    result = store.plan(path.split("/")[2])
                elif path == "/replay-jobs":
                    result = {"items": [store.job(r["id"]) for r in store.read("SELECT id FROM jobs ORDER BY created_at DESC LIMIT 100")]}
                elif path.startswith("/replay-jobs/"):
                    identifier = path.split("/")[2]
                    result = store.report(identifier) if path.endswith("/report") else store.job(identifier)
                elif path == "/audit":
                    cursor, limit = self.pagination(q)
                    rows = store.read("SELECT * FROM audit ORDER BY sequence DESC")
                    if q.get("search"):
                        needle = q["search"].lower()
                        rows = [r for r in rows if needle in canonical(r).lower()]
                    result = self.page(rows, cursor, limit)
                    result["integrity"] = self.audit_integrity()
                else:
                    raise Problem(404, "API route not found.")
                self.send_json(result)
            except Problem as error:
                self.send_json({"error": error.message}, error.status)
            except (ValueError, TypeError, KeyError):
                self.send_json({"error": "Invalid request."}, 400)
            except Exception:
                self.send_json({"error": "Internal error; no message payloads were logged."}, 500)

        @staticmethod
        def pagination(q):
            try:
                cursor, limit = int(q.get("cursor", 0)), int(q.get("limit", 25))
            except ValueError:
                raise Problem(400, "Invalid pagination cursor.")
            if cursor < 0 or not 1 <= limit <= 100:
                raise Problem(400, "Limit must be 1–100 and cursor must be non-negative.")
            return cursor, limit

        @staticmethod
        def page(rows, cursor, limit):
            return {"items": rows[cursor:cursor + limit], "total": len(rows),
                    "nextCursor": str(cursor + limit) if cursor + limit < len(rows) else None}

        @staticmethod
        def audit_integrity():
            previous = "0" * 64
            for row in store.read("SELECT * FROM audit ORDER BY sequence"):
                values = [row[k] for k in ["at", "actor", "role", "action", "resource", "detail", "previous_hash"]]
                if row["previous_hash"] != previous or digest(canonical(values)) != row["hash"]:
                    return "INVALID"
                previous = row["hash"]
            return "VALID"

        def do_POST(self):
            try:
                self.validate_host()
                origin = self.headers.get("Origin")
                if origin and origin not in {"http://localhost:" + str(port), "http://127.0.0.1:" + str(port),
                                              "http://localhost:5173", "http://127.0.0.1:5173"}:
                    raise Problem(403, "Cross-origin changes are not permitted.")
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    raise Problem(415, "Content-Type must be application/json.")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise Problem(400, "Request body must be 1–65536 bytes.")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise Problem(400, "Request body must be a JSON object.")
                path = urlparse(self.path).path.removeprefix("/api")
                if path == "/demo/session":
                    persona = data.get("role")
                    if persona not in PERSONAS:
                        raise Problem(400, "Unknown demo identity.")
                    token = secrets.token_urlsafe(32)
                    sessions[token] = {"actor": PERSONAS[persona], "expires": time.time() + 8 * 3600}
                    return self.send_json({"token": token, "actor": PERSONAS[persona], "mode": "LOCAL_DEMO_ONLY"})
                actor = self.actor()

                def action():
                    if path == "/replay-plans":
                        return store.create_plan(actor, data)
                    if path.startswith("/replay-plans/") and path.endswith("/approve"):
                        return store.approve(actor, path.split("/")[2], data)
                    if path == "/replay-jobs":
                        return store.start(actor, data)
                    if path.startswith("/replay-jobs/") and path.split("/")[-1] in {"pause", "resume", "cancel"}:
                        return store.control(actor, path.split("/")[2], path.split("/")[-1], data)
                    if path == "/verifications":
                        return store.verify(actor, str(data.get("jobId", "")))
                    raise Problem(404, "API route not found.")

                try:
                    # Authorize cached requests too, and audit denied capabilities.
                    require(actor, "approve" if path.endswith("/approve") else "operate")
                    result = store.mutate(actor, self.headers.get("Idempotency-Key"), path, data, action)
                except Problem as error:
                    with store.transaction():
                        store.audit(actor, "REQUEST_REJECTED", path, {"status": error.status, "reason": error.message})
                    raise
                self.send_json(result)
            except Problem as error:
                self.send_json({"error": error.message}, error.status)
            except (ValueError, TypeError, KeyError):
                self.send_json({"error": "Invalid JSON or request parameters."}, 400)
            except Exception:
                self.send_json({"error": "Internal error; no message payloads were logged."}, 500)

        def static(self, path):
            base = ROOT / "dist"
            file = (base / path.lstrip("/")).resolve()
            if not file.is_relative_to(base.resolve()):
                raise Problem(403, "Invalid file path.")
            if not file.is_file():
                file = base / "index.html"
            if not file.exists():
                return self.send_json({"error": "Frontend not built. Run npm install, then npm run build. For development, run npm run dev in another terminal."}, 503)
            body = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(str(file))[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="QueueRescue local simulation; no AWS resources are used")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=str(ROOT / ".data" / "queuerescue.sqlite3"))
    parser.add_argument("--seed", type=int, default=1000)
    parser.add_argument("--consumer-delay", type=float, default=.25, help="Delay before each synthetic delivery can be consumed (seconds)")
    parser.add_argument("--consumer-failures", type=int, default=0, help="Injected failures per delivery before shipment creation")
    parser.add_argument("--consumer-max-attempts", type=int, default=3)
    parser.add_argument("--consumer-disabled", action="store_true", help="Leave accepted deliveries queued without processing them")
    args = parser.parse_args()
    if not 1 <= args.seed <= 100000:
        parser.error("--seed must be between 1 and 100000")
    if not math.isfinite(args.consumer_delay) or args.consumer_delay < 0:
        parser.error("--consumer-delay must be finite and non-negative")
    if args.consumer_failures < 0 or args.consumer_max_attempts < 1:
        parser.error("--consumer-failures must be non-negative and --consumer-max-attempts must be positive")
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    store = Store(args.db, args.seed, consumer_delay=args.consumer_delay)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store, args.port))
    worker = Worker(store)
    consumer = ShipmentConsumer(store, transient_failures=args.consumer_failures, max_attempts=args.consumer_max_attempts)
    worker.start()
    if not args.consumer_disabled:
        consumer.start()
    print("QueueRescue local demo: http://localhost:" + str(args.port), flush=True)
    print("Synthetic data only. Demo personas are not production authentication.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        worker.stopping.set()
        consumer.stopping.set()
        worker.join(timeout=5)
        if consumer.is_alive():
            consumer.join(timeout=5)
        server.server_close()
        store.db.close()


if __name__ == "__main__":
    main()
