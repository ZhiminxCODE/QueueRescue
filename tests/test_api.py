"""Real HTTP contract tests against a temporary loopback API and database."""
import http.client
import json
import threading
import time
import unittest
import uuid
from http.server import ThreadingHTTPServer

from backend.server import make_handler
from backend.service import Store, Worker


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store(seed_count=100)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.store, 0))
        cls.port = cls.server.server_port
        cls.server.RequestHandlerClass = make_handler(cls.store, cls.port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()
        cls.store.db.close()

    def request(self, method, path, data=None, token=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        request_headers = {"Content-Type": "application/json"}
        if token:
            request_headers["Authorization"] = "Bearer " + token
        request_headers.update(headers or {})
        connection.request(method, "/api" + path, json.dumps(data) if data is not None else None, request_headers)
        response = connection.getresponse()
        result = json.loads(response.read())
        connection.close()
        return response.status, result

    def login(self, role):
        status, result = self.request("POST", "/demo/session", {"role": role})
        self.assertEqual(status, 200)
        return result["token"]

    def post(self, path, data, token, key=None):
        return self.request("POST", path, data, token, {"Idempotency-Key": key or str(uuid.uuid4())})

    def test_health_and_authenticated_reads(self):
        self.assertEqual(self.request("GET", "/health")[0], 200)
        self.assertEqual(self.request("GET", "/overview")[0], 401)
        self.assertEqual(self.request("GET", "/connections", token="forged")[0], 401)
        token = self.login("Viewer")
        self.assertEqual(self.request("GET", "/overview", token=token)[1]["total"], 100)
        self.assertEqual(self.request("GET", "/connections", token=token)[1]["items"][0]["type"], "Standard")

    def test_server_rejects_spoofed_role_header(self):
        token = self.login("Viewer")
        status, _ = self.request("POST", "/replay-plans", {}, token, {"Idempotency-Key": str(uuid.uuid4()), "X-Role": "Admin"})
        self.assertEqual(status, 403)

    def test_pagination_and_allowlist(self):
        token = self.login("Viewer")
        status, first = self.request("GET", "/messages?limit=25", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(len(first["items"]), 25)
        status, second = self.request("GET", "/messages?cursor=" + first["nextCursor"], token=token)
        self.assertEqual(status, 200)
        self.assertFalse(set(x["id"] for x in first["items"]) & set(x["id"] for x in second["items"]))
        self.assertEqual(self.request("GET", "/messages?queue=unapproved", token=token)[0], 403)
        self.assertEqual(self.request("GET", "/messages?cursor=-1", token=token)[0], 400)
        self.assertEqual(self.request("GET", "/messages?limit=1000", token=token)[0], 400)

    def test_mutation_requires_idempotency_key(self):
        token = self.login("Operator")
        self.assertEqual(self.request("POST", "/replay-plans", {}, token)[0], 400)

    def test_http_idempotency_and_hash_approval(self):
        operator, approver = self.login("Operator"), self.login("Approver")
        key = str(uuid.uuid4())
        status, plan = self.post("/replay-plans", {}, operator, key)
        self.assertEqual(status, 200)
        self.assertEqual(self.post("/replay-plans", {}, operator, key)[1]["id"], plan["id"])
        self.assertEqual(self.post("/replay-plans", {"rate": 50}, operator, key)[0], 409)
        self.assertEqual(self.post("/replay-jobs", {"approvedPlanId": plan["id"]}, operator)[0], 409)
        self.assertEqual(self.post("/replay-plans/" + plan["id"] + "/approve", {"planHash": "changed", "note": "Reviewed."}, approver)[0], 409)
        status, approved = self.post("/replay-plans/" + plan["id"] + "/approve", {"planHash": plan["hash"], "note": "Reviewed."}, approver)
        self.assertEqual(status, 200)
        self.assertEqual(approved["approved_by"], "Morgan Lee")

    def test_cross_origin_mutation_rejected(self):
        status, _ = self.request("POST", "/demo/session", {"role": "Admin"}, headers={"Origin": "https://untrusted.example"})
        self.assertEqual(status, 403)

    def test_untrusted_host_rejected(self):
        self.assertEqual(self.request("GET", "/health", headers={"Host": "untrusted.example"})[0], 403)

    def test_payload_masked_over_http(self):
        token = self.login("Viewer")
        status, message = self.request("GET", "/messages/msg-000011", token=token)
        self.assertEqual(status, 200)
        self.assertNotIn("customer@example.com", json.dumps(message))
        self.assertEqual(message["payload"]["customer"]["email"], "[REDACTED]")

    def test_actual_worker_honors_rate_and_reconciliation(self):
        operator, approver = self.login("Operator"), self.login("Approver")
        _, plan = self.post("/replay-plans", {"filters": {"search": "shipment:ORD-1201"}, "rate": 5}, operator)
        self.post("/replay-plans/" + plan["id"] + "/approve", {"planHash": plan["hash"], "note": "Reviewed."}, approver)
        status, job = self.post("/replay-jobs", {"approvedPlanId": plan["id"]}, operator)
        self.assertEqual(status, 200)
        worker = Worker(self.store)
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                _, current = self.request("GET", "/replay-jobs/" + job["id"], token=operator)
                if current["state"] == "VERIFYING":
                    break
                time.sleep(.02)
            self.assertEqual(current["state"], "VERIFYING")
            deliveries = self.store.read("SELECT created_at FROM deliveries ORDER BY created_at")
            self.assertEqual(len(deliveries), 10)
            self.assertTrue(all(b["created_at"] - a["created_at"] >= .19 for a, b in zip(deliveries, deliveries[1:])))
            _, verified = self.post("/verifications", {"jobId": job["id"]}, operator)
            self.assertEqual(verified["state"], "COMPLETED")
            self.assertEqual(verified["counts"]["VERIFIED"], 10)
            self.assertEqual(self.request("GET", "/replay-jobs/" + job["id"] + "/report", token=operator)[1]["recovered"], 10)
        finally:
            worker.stopping.set()
            worker.join(timeout=3)

    def test_audit_chain_and_rejection_record(self):
        token = self.login("Operator")
        self.post("/replay-plans", {"destination": "not-allowlisted"}, token)
        status, audit = self.request("GET", "/audit?search=REQUEST_REJECTED", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(audit["integrity"], "VALID")
        self.assertGreater(audit["total"], 0)


if __name__ == "__main__":
    unittest.main()
