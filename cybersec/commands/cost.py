"""Cost commands for unified command system.

Commands:
    /cost status              - Show current MTD costs and budget status
    /cost estimate            - Get cost estimate from resource inventory
    /cost delta               - Compare estimate vs actual
    /cost history             - Show cost history
    /cost forecast            - Get AWS forecast
"""

from datetime import datetime
from .parser import ParsedCommand, CommandResult
from .registry import register_command


async def cmd_cost_status(cmd: ParsedCommand) -> CommandResult:
    """Show current MTD costs and budget status.

    Queries the cost monitor's current metrics.

    Options:
        --json, -j  Output as JSON
    """
    from ..cost.config import CostConfig
    from ..cost.aws_client import AWSCostClient
    from ..cost.estimator import ResourceEstimator

    config = CostConfig.from_env()

    if not config.enabled:
        return CommandResult(
            success=False,
            error="Cost monitoring is disabled. Set COST_ENABLED=true to enable.",
        )

    client = AWSCostClient(config)
    estimator = ResourceEstimator(config)

    try:
        account_id = client.get_account_id()
        actual = client.get_mtd_cost()
        forecast = client.get_forecast()
        estimate = estimator.estimate_from_inventory(client)

        by_service = client.get_cost_by_service()

        # Calculate delta if we have actual
        delta = None
        delta_pct = None
        if actual is not None:
            delta = actual - estimate.total_cost
            delta_pct = abs(delta) / actual * 100 if actual > 0 else 0

        # Budget status
        budget_pct = (actual / config.monthly_budget * 100) if actual and config.monthly_budget > 0 else 0

        data = {
            "account_id": account_id,
            "region": config.aws_region,
            "period": datetime.now().strftime('%Y-%m'),
            "actual_mtd": actual,
            "estimate": estimate.total_cost,
            "forecast": forecast,
            "delta": delta,
            "delta_pct": delta_pct,
            "by_service": by_service,
            "budget": {
                "amount": config.monthly_budget,
                "used_pct": budget_pct,
                "threshold": config.alert_threshold * 100,
            },
        }

        formatted = _format_cost_status(data)

        return CommandResult(
            success=True,
            data=data,
            formatted=formatted,
        )

    except Exception as e:
        return CommandResult(
            success=False,
            error=f"Failed to get cost status: {e}",
        )


async def cmd_cost_estimate(cmd: ParsedCommand) -> CommandResult:
    """Get cost estimate from resource inventory.

    Scans current resources and calculates estimated costs.

    Options:
        --verbose   Show per-resource breakdown
        --json, -j  Output as JSON
    """
    from ..cost.config import CostConfig
    from ..cost.aws_client import AWSCostClient
    from ..cost.estimator import ResourceEstimator

    config = CostConfig.from_env()
    client = AWSCostClient(config)
    estimator = ResourceEstimator(config)

    try:
        estimate = estimator.estimate_from_inventory(client)

        data = estimate.to_dict()
        verbose = cmd.options.get("verbose", False)

        formatted = _format_cost_estimate(estimate, verbose)

        return CommandResult(
            success=True,
            data=data,
            formatted=formatted,
        )

    except Exception as e:
        return CommandResult(
            success=False,
            error=f"Failed to estimate costs: {e}",
        )


async def cmd_cost_delta(cmd: ParsedCommand) -> CommandResult:
    """Compare estimate vs actual, update loss metrics.

    Calculates the difference between AWS Cost Explorer actual
    and our resource-based estimate.

    Options:
        --json, -j  Output as JSON
    """
    from ..cost.config import CostConfig, CostDelta
    from ..cost.aws_client import AWSCostClient
    from ..cost.estimator import ResourceEstimator

    config = CostConfig.from_env()
    client = AWSCostClient(config)
    estimator = ResourceEstimator(config)

    try:
        actual = client.get_mtd_cost()
        estimate = estimator.estimate_from_inventory(client)

        if actual is None:
            return CommandResult(
                success=False,
                error="Cost Explorer access denied. Cannot compute delta without actual cost data.",
            )

        delta = actual - estimate.total_cost
        delta_pct = abs(delta) / actual * 100 if actual > 0 else 0

        cost_delta = CostDelta(
            timestamp=datetime.now(),
            period=datetime.now().strftime('%Y-%m'),
            actual=actual,
            estimated=estimate.total_cost,
            delta=delta,
            percentage_error=delta_pct,
        )

        data = cost_delta.to_dict()
        formatted = _format_cost_delta(cost_delta)

        return CommandResult(
            success=True,
            data=data,
            formatted=formatted,
        )

    except Exception as e:
        return CommandResult(
            success=False,
            error=f"Failed to compute cost delta: {e}",
        )


async def cmd_cost_forecast(cmd: ParsedCommand) -> CommandResult:
    """Get AWS forecast and compare to our projection.

    Options:
        --json, -j  Output as JSON
    """
    from ..cost.config import CostConfig
    from ..cost.aws_client import AWSCostClient

    config = CostConfig.from_env()
    client = AWSCostClient(config)

    try:
        forecast = client.get_forecast()
        actual = client.get_mtd_cost()
        by_service = client.get_cost_by_service()

        if forecast is None and actual is None:
            return CommandResult(
                success=False,
                error="Cost Explorer access denied. Cannot get forecast data.",
            )

        # Calculate projected total based on actual MTD
        now = datetime.now()
        days_in_month = 30  # Approximate
        days_elapsed = now.day
        if actual is not None and days_elapsed > 0:
            projected = (actual / days_elapsed) * days_in_month
        else:
            projected = None

        data = {
            "period": now.strftime('%Y-%m'),
            "aws_forecast": forecast,
            "actual_mtd": actual,
            "projected_total": projected,
            "days_elapsed": days_elapsed,
            "days_in_month": days_in_month,
            "top_services": dict(sorted(by_service.items(), key=lambda x: -x[1])[:5]),
        }

        formatted = _format_cost_forecast(data)

        return CommandResult(
            success=True,
            data=data,
            formatted=formatted,
        )

    except Exception as e:
        return CommandResult(
            success=False,
            error=f"Failed to get forecast: {e}",
        )


# === Formatting helpers ===

def _format_cost_status(data: dict) -> str:
    """Format cost status for human display."""
    lines = []
    lines.append("AWS Cost Status")
    lines.append("=" * 50)
    lines.append("")
    lines.append(f"Account: {data.get('account_id', 'unknown')}")
    lines.append(f"Region: {data.get('region', 'unknown')}")
    lines.append(f"Period: {data.get('period', 'unknown')}")
    lines.append("")

    actual = data.get("actual_mtd")
    estimate = data.get("estimate")
    forecast = data.get("forecast")

    if actual is not None:
        lines.append(f"Actual MTD:    ${actual:,.2f}")
    else:
        lines.append("Actual MTD:    (Cost Explorer access denied)")

    if estimate is not None:
        lines.append(f"Estimate:      ${estimate:,.2f}")

    if forecast is not None:
        lines.append(f"Forecast:      ${forecast:,.2f}")

    # Delta
    delta = data.get("delta")
    delta_pct = data.get("delta_pct")
    if delta is not None:
        direction = "over" if delta > 0 else "under"
        lines.append("")
        lines.append(f"Delta:         ${abs(delta):,.2f} {direction} estimate ({delta_pct:.1f}%)")

    # Budget
    budget = data.get("budget", {})
    if budget.get("amount"):
        lines.append("")
        lines.append("Budget:")
        lines.append(f"  Monthly:     ${budget['amount']:,.2f}")
        lines.append(f"  Used:        {budget.get('used_pct', 0):.1f}%")
        if budget.get('used_pct', 0) >= budget.get('threshold', 80):
            lines.append(f"  ⚠ Warning: Approaching budget threshold!")

    # Top services
    by_service = data.get("by_service", {})
    if by_service:
        lines.append("")
        lines.append("Top Services:")
        for service, cost in sorted(by_service.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  {service[:30]:<30} ${cost:,.2f}")

    return "\n".join(lines)


def _format_cost_estimate(estimate, verbose: bool = False) -> str:
    """Format cost estimate for human display."""
    lines = []
    lines.append("AWS Cost Estimate (Resource Inventory)")
    lines.append("=" * 50)
    lines.append("")
    lines.append(f"Account: {estimate.account_id}")
    lines.append(f"Region: {estimate.region}")
    lines.append(f"Period: {estimate.period_start.strftime('%Y-%m-%d')} to {estimate.period_end.strftime('%Y-%m-%d')}")
    lines.append(f"Confidence: {estimate.confidence}")
    lines.append("")
    lines.append(f"Total Estimate: ${estimate.total_cost:,.2f}")
    lines.append("")

    # By service
    lines.append("By Service:")
    for service, cost in sorted(estimate.by_service.items(), key=lambda x: -x[1]):
        lines.append(f"  {service:<20} ${cost:,.2f}")

    if verbose and estimate.resources:
        lines.append("")
        lines.append("Resource Breakdown:")
        for r in sorted(estimate.resources, key=lambda x: -x.total_cost):
            lines.append(f"  {r.service}/{r.resource_type:<20} {r.hours:.1f}h @ ${r.unit_cost_hourly:.4f}/h = ${r.total_cost:,.2f}")

    if estimate.notes:
        lines.append("")
        lines.append(f"Note: {estimate.notes}")

    return "\n".join(lines)


def _format_cost_delta(delta) -> str:
    """Format cost delta for human display."""
    lines = []
    lines.append("AWS Cost Delta Analysis")
    lines.append("=" * 50)
    lines.append("")
    lines.append(f"Period: {delta.period}")
    lines.append("")
    lines.append(f"Actual (CE):   ${delta.actual:,.2f}")
    lines.append(f"Estimated:     ${delta.estimated:,.2f}")
    lines.append("")

    direction = "over" if delta.delta > 0 else "under"
    lines.append(f"Delta:         ${abs(delta.delta):,.2f} ({direction} estimate)")
    lines.append(f"Error:         {delta.percentage_error:.1f}%")
    lines.append("")

    lines.append("Loss Metrics:")
    lines.append(f"  L1 (MAE):    ${delta.absolute_loss:,.2f}")
    lines.append(f"  L2 (MSE):    ${delta.squared_loss:,.2f}")

    if delta.percentage_error > 20:
        lines.append("")
        lines.append("⚠ High estimation error - review resource inventory")

    return "\n".join(lines)


def _format_cost_forecast(data: dict) -> str:
    """Format cost forecast for human display."""
    lines = []
    lines.append("AWS Cost Forecast")
    lines.append("=" * 50)
    lines.append("")
    lines.append(f"Period: {data.get('period', 'unknown')}")
    lines.append(f"Days elapsed: {data.get('days_elapsed', 0)}/{data.get('days_in_month', 30)}")
    lines.append("")

    actual = data.get("actual_mtd")
    forecast = data.get("aws_forecast")
    projected = data.get("projected_total")

    if actual is not None:
        lines.append(f"Actual MTD:      ${actual:,.2f}")

    if forecast is not None:
        lines.append(f"AWS Forecast:    ${forecast:,.2f}")
    else:
        lines.append("AWS Forecast:    (Not available)")

    if projected is not None:
        lines.append(f"Linear Project:  ${projected:,.2f}")

    # Top services
    top = data.get("top_services", {})
    if top:
        lines.append("")
        lines.append("Top Services (MTD):")
        for service, cost in top.items():
            lines.append(f"  {service[:30]:<30} ${cost:,.2f}")

    return "\n".join(lines)


# === Registration ===

def register_cost_commands():
    """Register all cost commands."""
    register_command(
        "cost.status",
        cmd_cost_status,
        description="Show current MTD costs and budget status",
        options=[
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/cost status",
            "/cost status --json",
        ],
    )

    register_command(
        "cost.estimate",
        cmd_cost_estimate,
        description="Get cost estimate from resource inventory",
        options=[
            {"name": "verbose", "description": "Show per-resource breakdown"},
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/cost estimate",
            "/cost estimate --verbose",
        ],
    )

    register_command(
        "cost.delta",
        cmd_cost_delta,
        description="Compare estimate vs actual (loss function)",
        options=[
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/cost delta",
        ],
    )

    register_command(
        "cost.forecast",
        cmd_cost_forecast,
        description="Get AWS forecast and projections",
        options=[
            {"name": "json", "short": "j", "description": "Output as JSON"},
        ],
        examples=[
            "/cost forecast",
        ],
    )
