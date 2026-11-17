import unittest
from decimal import Decimal as D

from tools.estimate_aws_demo import estimate


class DemoCostTests(unittest.TestCase):
    def test_capacity_continues_while_idle_without_repeating_demo(self):
        hour = estimate(1)
        month = estimate(720)
        self.assertEqual(hour["line_items_usd"]["dynamodb_capacity"], D("0.01170"))
        self.assertEqual(month["line_items_usd"]["dynamodb_capacity"], D("8.42400"))
        self.assertEqual(estimate()["line_items_usd"]["dynamodb_capacity"], D("0.03510"))
        for meter in ("lambda_compute", "sqs", "http_api", "cognito_lite"):
            self.assertEqual(hour["line_items_usd"][meter], month["line_items_usd"][meter])

    def test_units_and_runtime_sensitivity(self):
        hour, stress = estimate(), estimate(stressed=True)
        self.assertEqual(hour["lambda_invocations"], 570)
        self.assertEqual(hour["lambda_gb_seconds"], D("142.50"))
        self.assertEqual(stress["lambda_gb_seconds"], D("10350.00"))
        self.assertEqual(estimate(720)["line_items_usd"]["s3_storage_versions_markers"],
                         D(2) / 1024 * D("0.023"))
        self.assertEqual(hour["line_items_usd"]["http_api"], D("0.000400"))
        self.assertEqual(hour["line_items_usd"]["sqs"], D("0.0040000"))

    def test_no_unverified_discount_or_hard_cap(self):
        result = estimate()
        self.assertFalse(result["free_allowances_applied"])
        self.assertFalse(result["credits_applied"])
        self.assertFalse(result["audit_logging_included"])
        self.assertFalse(result["spending_cap"])
        self.assertGreater(result["total_usd_before_tax"], D(0))
        self.assertEqual(result["total_usd_before_tax"], sum(result["line_items_usd"].values()))

    def test_invalid_retention_does_not_produce_a_cheap_estimate(self):
        for value in (0, -1, 721, "NaN", "Infinity"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                estimate(value)


if __name__ == "__main__":
    unittest.main()
