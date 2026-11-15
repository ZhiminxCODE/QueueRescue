"""Offline cost model for the documented 25-message minimal AWS demonstration.

Rates reviewed 2026-11-15, us-east-1. No AWS calls, credits, or free allowances.
Usage assumptions and sources: docs/aws-cost-assessment.md. This is not a cap.
"""

import argparse
import json
from decimal import Decimal


D = Decimal
GIB_PER_MIB = D(1) / 1024
HOURS_PER_MONTH = D(720)  # Explicit 30-day comparison, not every calendar month.
# Invocations budgeted across the whole demo, not per hour of retained storage.
FUNCTIONS = {
    "api": (200, 120),
    "collector": (10, 60),
    "consumer": (100, 30),
    "replay": (10, 60),
    "verifier": (250, 30),
}


def estimate(resource_hours=D(3), stressed=False):
    """Return unrounded USD line items. Retention scenarios do not repeat traffic.

    Default lifetime includes an hour each for setup, active demo, and cleanup.
    Longer scenarios add idle resource time, without repeating the workload.
    """
    hours = D(str(resource_hours))
    if not hours.is_finite() or hours < 1 or hours > 720:
        raise ValueError("resource_hours must be between 1 and 720 inclusive")
    invocations = sum(count for count, _ in FUNCTIONS.values())
    # Sensitivity assumption only: configured timeout + 10 billed seconds per
    # invocation for initialization/overhead. This is not an execution bound.
    seconds = sum(count * (timeout + 10 if stressed else 1)
                  for count, timeout in FUNCTIONS.values())
    gb_seconds = D(seconds) * D("0.25")
    months = hours / HOURS_PER_MONTH
    # Budget up to two days of archive occupancy, despite one-day log retention;
    # ignore compression savings. Explicit teardown removes groups earlier.
    log_months = min(hours, D(48)) / HOURS_PER_MONTH
    lines = {
        "http_api": D(400) * D("0.000001"),
        "lambda_requests": D(invocations) * D("0.0000002"),
        "lambda_compute": gb_seconds * D("0.0000133334"),
        "sqs": D(10000) * D("0.0000004"),
        "step_functions": D(100) * D("0.000025"),
        "dynamodb_capacity": D(15) * (D("0.00065") + D("0.00013")) * hours,
        "dynamodb_storage": D(10) * GIB_PER_MIB * months * D("0.25"),
        "s3_writes_list": D(100) * D("0.000005"),
        "s3_reads": D(2000) * D("0.0000004"),
        "s3_storage_versions_markers": D(2) * GIB_PER_MIB * months * D("0.023"),
        "logs_ingestion": D(10) * GIB_PER_MIB * D("0.50"),
        "logs_archive": D(10) * GIB_PER_MIB * log_months * D("0.03"),
        "cognito_lite": D(2) * D("0.0055"),
        "sns_requests": D(100) * D("0.0000005"),
        "sns_email": D(10) * D("0.00002"),
        # Conservatively reserve SES sending/data charges for Cognito email.
        # AWS-managed default delivery may not bill this to the account; no
        # account-specific exemption is assumed by this gross estimate.
        "cognito_email_reserve": D(10) * D("0.0001") + GIB_PER_MIB * D("0.12"),
        "internet_data_out": D(10) * GIB_PER_MIB * D("0.09"),
        "budgets_notifications": D(0),
        "iam_and_configured_service_owned_encryption": D(0),
    }
    return {
        "region": "us-east-1",
        "pricing_reviewed": "2026-11-15",
        "messages": 25,
        "active_hours": 1,
        "resource_hours": hours,
        "runtime_scenario": "timeout_plus_10s_sensitivity" if stressed else "1s_average_illustration",
        "lambda_invocations": invocations,
        "lambda_gb_seconds": gb_seconds,
        "free_allowances_applied": False,
        "credits_applied": False,
        "spending_cap": False,
        "line_items_usd": lines,
        "total_usd_before_tax": sum(lines.values(), D(0)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-hours", type=D, default=D(3))
    parser.add_argument("--stressed", action="store_true")
    args = parser.parse_args()
    try:
        result = estimate(args.resource_hours, args.stressed)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
