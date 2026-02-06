"""Prometheus metric definitions for AWS cost monitoring.

These metrics are exported via /metrics endpoint and scraped by Prometheus.
"""

from prometheus_client import Gauge, Counter, Info

# =============================================================================
# Cost Metrics (updated every poll interval)
# =============================================================================

AWS_COST_MTD = Gauge(
    'aws_cost_mtd_usd',
    'Month-to-date AWS cost in USD from Cost Explorer',
    ['account', 'region']
)

AWS_COST_ESTIMATE = Gauge(
    'aws_cost_estimate_usd',
    'Estimated cost based on resource inventory',
    ['account', 'source']  # source: "inventory" or "pricing_api"
)

AWS_COST_DELTA = Gauge(
    'aws_cost_delta_usd',
    'Difference between actual and estimated (actual - estimated)',
    ['account', 'period']
)

AWS_COST_DELTA_PCT = Gauge(
    'aws_cost_delta_pct',
    'Percentage difference: |actual-estimated|/actual * 100',
    ['account', 'period']
)

AWS_COST_BY_SERVICE = Gauge(
    'aws_cost_by_service_usd',
    'Cost breakdown by AWS service',
    ['account', 'service']
)

AWS_COST_FORECAST = Gauge(
    'aws_cost_forecast_usd',
    'AWS cost forecast for current billing period',
    ['account', 'period']
)

# =============================================================================
# Budget Metrics
# =============================================================================

AWS_BUDGET_USED_PCT = Gauge(
    'aws_budget_used_pct',
    'Percentage of monthly budget used',
    ['account']
)

AWS_BUDGET_AMOUNT = Gauge(
    'aws_budget_amount_usd',
    'Configured monthly budget amount in USD',
    ['account']
)

# =============================================================================
# Monitor Health Metrics
# =============================================================================

COST_UPDATE_TIMESTAMP = Gauge(
    'aws_cost_update_timestamp_seconds',
    'Unix timestamp of last successful cost update'
)

COST_POLL_ERRORS = Counter(
    'aws_cost_poll_errors_total',
    'Total number of AWS API errors during cost polling',
    ['error_type']  # "access_denied", "rate_limit", "api_error", "timeout"
)

COST_POLL_DURATION = Gauge(
    'aws_cost_poll_duration_seconds',
    'Time taken to poll AWS cost data'
)

COST_POLL_SUCCESS = Counter(
    'aws_cost_poll_success_total',
    'Total number of successful cost polls'
)

# =============================================================================
# Estimation Accuracy Metrics (Loss Function)
# =============================================================================

COST_ESTIMATION_MAE = Gauge(
    'aws_cost_estimation_mae_usd',
    'Mean Absolute Error of cost estimation over time',
    ['account']
)

COST_ESTIMATION_RMSE = Gauge(
    'aws_cost_estimation_rmse_usd',
    'Root Mean Squared Error of cost estimation',
    ['account']
)

COST_ESTIMATION_BIAS = Gauge(
    'aws_cost_estimation_bias_usd',
    'Systematic over/under estimation bias',
    ['account']
)

# =============================================================================
# Resource Inventory Metrics
# =============================================================================

AWS_RESOURCE_COUNT = Gauge(
    'aws_resource_count',
    'Number of AWS resources by type',
    ['account', 'region', 'service', 'resource_type']
)

AWS_RESOURCE_COST_HOURLY = Gauge(
    'aws_resource_cost_hourly_usd',
    'Estimated hourly cost by resource type',
    ['account', 'region', 'service', 'resource_type']
)


def reset_all_metrics():
    """Reset all cost metrics to initial state.

    Useful for testing or when credentials change.
    """
    # Note: Prometheus counters cannot be reset, only gauges
    pass
