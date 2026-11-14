import unittest

from cloud.contracts import CloudProblem, actor_from_gateway, cursor_decode, cursor_encode, request_identity, validate_event


class CloudContractTests(unittest.TestCase):
    def event(self, groups):
        return {"requestContext": {"authorizer": {"jwt": {"claims": {"sub": "person-1", "token_use": "access", "cognito:groups": groups}}}}}

    def test_gateway_groups_grant_only_their_capabilities(self):
        for groups in [['Operator'], '["Operator"]', '[Operator]']:
            self.assertEqual(actor_from_gateway(self.event(groups), "operate")["id"], "person-1")
            with self.assertRaises(CloudProblem):
                actor_from_gateway(self.event(groups), "approve")
        with self.assertRaises(CloudProblem):
            actor_from_gateway({"headers": {"X-Role": "Admin"}}, "operate")

    def test_id_tokens_are_rejected(self):
        event = self.event(['Admin'])
        event['requestContext']['authorizer']['jwt']['claims']['token_use'] = 'id'
        with self.assertRaises(CloudProblem):
            actor_from_gateway(event, 'read')

    def test_cursor_roundtrip_and_invalid_shape(self):
        key = {"pk": "QUEUE#source", "sk": "MSG#abc"}
        self.assertEqual(cursor_decode(cursor_encode(key)), key)
        for bad in ['!', cursor_encode({"pk": "other"}), cursor_encode({"pk": [], "sk": "x"})]:
            with self.assertRaises(CloudProblem):
                cursor_decode(bad)

    def test_request_identity_binds_actor_key_and_body(self):
        actor = {"id": "person-1"}
        key, fingerprint = request_identity(actor, '/plans', {'Idempotency-Key': 'request-123'}, {})
        same, changed = request_identity(actor, '/plans', {'idempotency-key': 'request-123'}, {"rate": 2})
        self.assertEqual(key, same)
        self.assertNotEqual(fingerprint, changed)
        other, _ = request_identity({"id": "person-2"}, '/plans', {'Idempotency-Key': 'request-123'}, {})
        self.assertNotEqual(key, other)

    def test_boolean_schema_version_is_not_an_integer_version(self):
        import json
        payload = {"operationId": "shipment:o1", "eventId": "e1", "orderId": "o1", "warehouseId": "w1", "schemaVersion": True, "eventType": "SHIP_ORDER"}
        with self.assertRaises(CloudProblem):
            validate_event(json.dumps(payload))
        payload['schemaVersion'] = 1
        self.assertEqual(validate_event(json.dumps(payload))['operationId'], 'shipment:o1')
