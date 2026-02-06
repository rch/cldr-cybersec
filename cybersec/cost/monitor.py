"""Cost monitor process - runs as devenv background service.

This module provides the main cost-monitor process that:
1. Polls AWS cost data on a regular interval
2. Updates Prometheus metrics for scraping
3. Exposes /metrics endpoint for Prometheus
"""

import logging
import threading
import time
from datetime import datetime

from flask import Flask

from .config import CostConfig, CostDelta
from .aws_client import AWSCostClient
from .estimator import ResourceEstimator
from .metrics import (
    AWS_COST_MTD,
    AWS_COST_ESTIMATE,
    AWS_COST_DELTA,
    AWS_COST_DELTA_PCT,
    AWS_COST_BY_SERVICE,
    AWS_COST_FORECAST,
    AWS_BUDGET_USED_PCT,
    AWS_BUDGET_AMOUNT,
    COST_UPDATE_TIMESTAMP,
    COST_POLL_ERRORS,
    COST_POLL_DURATION,
    COST_POLL_SUCCESS,
)

logger = logging.getLogger(__name__)

app = Flask(__name__)


class CostMonitor:
    """AWS cost monitor with Prometheus metrics export.

    Polls AWS cost APIs and updates Prometheus gauges for scraping.
    Handles graceful degradation when Cost Explorer access is denied.
    """

    def __init__(self, config: CostConfig):
        """Initialize cost monitor.

        Args:
            config: Cost tracking configuration
        """
        self.config = config
        self.aws = AWSCostClient(config)
        self.estimator = ResourceEstimator(config)
        self.account_id = None
        self._last_delta: CostDelta | None = None

    def poll_costs(self) -> dict:
        """Single poll cycle - called every POLL_INTERVAL.

        Updates all Prometheus metrics with current cost data.

        Returns:
            Dictionary with poll results
        """
        start_time = time.time()
        results = {
            "timestamp": datetime.now().isoformat(),
            "success": False,
            "actual": None,
            "estimate": None,
            "delta": None,
        }

        try:
            # Get account ID on first poll
            if not self.account_id:
                self.account_id = self.aws.get_account_id()

            region = self.config.aws_region

            # Get actual cost from Cost Explorer (if permitted)
            actual = self.aws.get_mtd_cost()
            results["actual"] = actual

            if actual is not None:
                AWS_COST_MTD.labels(
                    account=self.account_id,
                    region=region,
                ).set(actual)

                # Update budget metrics
                AWS_BUDGET_AMOUNT.labels(account=self.account_id).set(
                    self.config.monthly_budget
                )
                budget_pct = (actual / self.config.monthly_budget * 100) if self.config.monthly_budget > 0 else 0
                AWS_BUDGET_USED_PCT.labels(account=self.account_id).set(budget_pct)

            # Get cost by service breakdown
            by_service = self.aws.get_cost_by_service()
            for service, cost in by_service.items():
                AWS_COST_BY_SERVICE.labels(
                    account=self.account_id,
                    service=service,
                ).set(cost)

            # Get AWS forecast
            forecast = self.aws.get_forecast()
            if forecast:
                period = datetime.now().strftime('%Y-%m')
                AWS_COST_FORECAST.labels(
                    account=self.account_id,
                    period=period,
                ).set(forecast)

            # Calculate our estimate from resource inventory
            estimate = self.estimator.estimate_from_inventory(self.aws)
            results["estimate"] = estimate.total_cost

            AWS_COST_ESTIMATE.labels(
                account=self.account_id,
                source="inventory",
            ).set(estimate.total_cost)

            # Calculate delta (loss function) if we have actual data
            if actual is not None:
                delta = actual - estimate.total_cost
                delta_pct = abs(delta) / actual * 100 if actual > 0 else 0
                period = datetime.now().strftime('%Y-%m')

                AWS_COST_DELTA.labels(
                    account=self.account_id,
                    period=period,
                ).set(delta)

                AWS_COST_DELTA_PCT.labels(
                    account=self.account_id,
                    period=period,
                ).set(delta_pct)

                self._last_delta = CostDelta(
                    timestamp=datetime.now(),
                    period=period,
                    actual=actual,
                    estimated=estimate.total_cost,
                    delta=delta,
                    percentage_error=delta_pct,
                )
                results["delta"] = delta

            # Update monitor health metrics
            COST_UPDATE_TIMESTAMP.set(time.time())
            COST_POLL_SUCCESS.inc()

            duration = time.time() - start_time
            COST_POLL_DURATION.set(duration)

            results["success"] = True
            results["duration"] = duration

            logger.info(
                f"Cost poll complete: actual=${actual}, "
                f"estimate=${estimate.total_cost:.2f}, "
                f"duration={duration:.2f}s"
            )

        except Exception as e:
            error_type = "access_denied" if "AccessDenied" in str(e) else "api_error"
            COST_POLL_ERRORS.labels(error_type=error_type).inc()
            logger.warning(f"Cost poll error: {e}")
            results["error"] = str(e)

        return results

    def get_last_delta(self) -> CostDelta | None:
        """Get the last computed cost delta.

        Returns:
            CostDelta if available, None otherwise
        """
        return self._last_delta


# Global monitor instance
_monitor: CostMonitor | None = None


def get_monitor() -> CostMonitor | None:
    """Get the global monitor instance."""
    return _monitor


@app.route('/health')
def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "cost-monitor"}


@app.route('/status')
def status():
    """Status endpoint with current cost data."""
    monitor = get_monitor()
    if monitor is None:
        return {"error": "Monitor not initialized"}, 503

    return {
        "account_id": monitor.account_id,
        "config": monitor.config.to_dict(),
        "last_delta": monitor._last_delta.to_dict() if monitor._last_delta else None,
    }


def run_monitor():
    """Main entry point for cost-monitor process.

    Starts Flask server and background polling thread.
    """
    global _monitor

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )

    # Load configuration from environment
    config = CostConfig.from_env()

    if not config.enabled:
        logger.info("Cost monitoring disabled")
        return

    logger.info(f"Starting AWS cost monitor on port {config.metrics_port}")
    logger.info(f"Poll interval: {config.poll_interval_seconds} seconds")
    logger.info(f"AWS profile: {config.aws_profile}")
    logger.info(f"AWS region: {config.aws_region}")

    # Create monitor
    _monitor = CostMonitor(config)

    # Add prometheus WSGI app for /metrics endpoint
    from prometheus_client import make_wsgi_app
    from werkzeug.middleware.dispatcher import DispatcherMiddleware

    app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {
        '/metrics': make_wsgi_app()
    })

    # Start background polling thread
    def poll_loop():
        while True:
            try:
                _monitor.poll_costs()
            except Exception as e:
                logger.error(f"Poll loop error: {e}")
            time.sleep(config.poll_interval_seconds)

    poller = threading.Thread(target=poll_loop, daemon=True)
    poller.start()

    # Run initial poll
    _monitor.poll_costs()

    # Run Flask server
    logger.info(f"Metrics endpoint: http://localhost:{config.metrics_port}/metrics")
    app.run(
        host='0.0.0.0',
        port=config.metrics_port,
        threaded=True,
        use_reloader=False,  # Disable reloader in production
    )


if __name__ == '__main__':
    run_monitor()
