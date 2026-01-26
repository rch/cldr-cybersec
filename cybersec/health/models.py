"""FMEA data models for RPN-based risk assessment.

Adapted from gaius health/fmea/models.py for cybersec toolkit.

This module defines core data structures for Failure Mode and Effects Analysis:
- RPNScore: Risk Priority Number calculation (S × O × D)
- FailureMode: Failure mode catalog entry
- CheckResult: Health check result
- EscalationTier: Remediation escalation levels
- HealthContext: Runtime context for checks
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any, Optional


class EscalationTier(IntEnum):
    """Remediation escalation tiers based on RPN.

    | RPN Range | Tier | Action |
    |-----------|------|--------|
    | 1-100 | TIER_0 | Auto-remediate immediately |
    | 101-200 | TIER_1 | Auto-remediate with agent validation |
    | 201-400 | TIER_2 | Require user approval |
    | 401-1000 | MANUAL | Human intervention required |
    """
    TIER_0 = 0  # Auto-remediate immediately
    TIER_1 = 1  # Auto-remediate with agent validation
    TIER_2 = 2  # Require user approval
    MANUAL = 3  # Manual intervention required


class CheckStatus(Enum):
    """Health check result status."""
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    ERROR = "error"      # Check itself failed
    SKIPPED = "skipped"  # Check not applicable


class AutomationLevel(Enum):
    """Automation level for observations and solutions.

    A: Full automation - local agent handles autonomously
    B: Partial automation - agent + human option
    C: Knowledge transfer - agent provides awareness, human acts
    """
    A = "full"
    B = "partial"
    C = "knowledge"


@dataclass
class RPNScore:
    """Risk Priority Number calculation result.

    RPN = Severity × Occurrence × Detection

    Attributes:
        severity: Impact on system availability (1-10, 10=catastrophic)
        occurrence: Probability of recurrence (1-10, 10=almost certain)
        detection: Ability to detect before impact (1-10, 10=no detection)
        rpn: Calculated RPN = S × O × D (1-1000)
        failure_mode_id: Link to FMEA catalog entry
        context_adjustments: Context that modified base scores
    """
    severity: int
    occurrence: int
    detection: int
    rpn: int
    failure_mode_id: str
    context_adjustments: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate scores and calculate RPN."""
        for name, value in [
            ("severity", self.severity),
            ("occurrence", self.occurrence),
            ("detection", self.detection),
        ]:
            if not 1 <= value <= 10:
                raise ValueError(f"{name} must be between 1 and 10, got {value}")

        expected_rpn = self.severity * self.occurrence * self.detection
        if self.rpn != expected_rpn:
            object.__setattr__(self, "rpn", expected_rpn)

    @property
    def tier(self) -> EscalationTier:
        """Determine escalation tier from RPN."""
        if self.rpn <= 100:
            return EscalationTier.TIER_0
        elif self.rpn <= 200:
            return EscalationTier.TIER_1
        elif self.rpn <= 400:
            return EscalationTier.TIER_2
        else:
            return EscalationTier.MANUAL

    @property
    def requires_approval(self) -> bool:
        """Check if RPN requires user approval."""
        return self.tier >= EscalationTier.TIER_2

    def with_conservative_override(self, reason: str) -> RPNScore:
        """Create a more conservative score with override reason."""
        new_adjustments = {**self.context_adjustments, "conservative_override": reason}
        # Escalate detection to force higher tier
        return RPNScore(
            severity=self.severity,
            occurrence=self.occurrence,
            detection=max(self.detection, 8),  # Force poor detection
            rpn=0,  # Will be recalculated
            failure_mode_id=self.failure_mode_id,
            context_adjustments=new_adjustments,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "severity": self.severity,
            "occurrence": self.occurrence,
            "detection": self.detection,
            "rpn": self.rpn,
            "failure_mode_id": self.failure_mode_id,
            "tier": self.tier.name,
            "tier_value": int(self.tier),
            "requires_approval": self.requires_approval,
            "context_adjustments": self.context_adjustments,
        }


@dataclass
class FailureMode:
    """Failure mode catalog entry.

    Attributes:
        failure_mode_id: Unique identifier (e.g., ICE_001, FLINK_002)
        category: Category (iceberg, flink, infra, data)
        name: Human-readable name
        description: Detailed description
        base_severity: Default severity score (1-10)
        base_occurrence: Default occurrence score (1-10)
        base_detection: Default detection score (1-10)
        symptom: Observable failure description
        cause: Root cause explanation
        detection_method: How this failure is detected
        remediation_steps: List of remediation actions
        observation_level: Automation level for observation (A/B/C)
        solution_level: Automation level for solution (A/B/C)
    """
    failure_mode_id: str
    category: str
    name: str
    description: str
    base_severity: int
    base_occurrence: int
    base_detection: int
    symptom: str = ""
    cause: str = ""
    detection_method: str = ""
    remediation_steps: list[str] = field(default_factory=list)
    observation_level: AutomationLevel = AutomationLevel.A
    solution_level: AutomationLevel = AutomationLevel.B

    @property
    def base_rpn(self) -> int:
        """Calculate base RPN from default scores."""
        return self.base_severity * self.base_occurrence * self.base_detection

    @property
    def base_tier(self) -> EscalationTier:
        """Get base escalation tier."""
        rpn = self.base_rpn
        if rpn <= 100:
            return EscalationTier.TIER_0
        elif rpn <= 200:
            return EscalationTier.TIER_1
        elif rpn <= 400:
            return EscalationTier.TIER_2
        else:
            return EscalationTier.MANUAL

    def calculate_rpn(self, context: dict[str, Any] = None) -> RPNScore:
        """Calculate RPN with optional context adjustments."""
        s, o, d = self.base_severity, self.base_occurrence, self.base_detection
        adjustments = {}

        if context:
            # Example adjustments based on context
            if context.get("repeated_failure"):
                o = min(10, o + 2)
                adjustments["repeated_failure"] = "+2 occurrence"
            if context.get("poor_observability"):
                d = min(10, d + 2)
                adjustments["poor_observability"] = "+2 detection"

        return RPNScore(
            severity=s,
            occurrence=o,
            detection=d,
            rpn=s * o * d,
            failure_mode_id=self.failure_mode_id,
            context_adjustments=adjustments,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "failure_mode_id": self.failure_mode_id,
            "category": self.category,
            "name": self.name,
            "description": self.description,
            "base_severity": self.base_severity,
            "base_occurrence": self.base_occurrence,
            "base_detection": self.base_detection,
            "base_rpn": self.base_rpn,
            "base_tier": self.base_tier.name,
            "symptom": self.symptom,
            "cause": self.cause,
            "detection_method": self.detection_method,
            "remediation_steps": self.remediation_steps,
            "observation_level": self.observation_level.value,
            "solution_level": self.solution_level.value,
        }


@dataclass
class CheckResult:
    """Health check result.

    Attributes:
        status: Check result status (ok, warning, critical, error, skipped)
        message: Human-readable result message
        failure_mode_id: Link to failure mode if issue detected
        rpn: RPN score if issue detected
        details: Additional diagnostic details
        remediation: Suggested remediation if issue detected
        duration_ms: Check execution time in milliseconds
    """
    status: CheckStatus
    message: str
    failure_mode_id: str = ""
    rpn: Optional[RPNScore] = None
    details: dict[str, Any] = field(default_factory=dict)
    remediation: str = ""
    duration_ms: int = 0

    @classmethod
    def ok(cls, message: str = "OK", **details) -> CheckResult:
        """Create an OK result."""
        return cls(status=CheckStatus.OK, message=message, details=details)

    @classmethod
    def warning(
        cls,
        message: str,
        failure_mode_id: str = "",
        rpn: Optional[RPNScore] = None,
        remediation: str = "",
        **details,
    ) -> CheckResult:
        """Create a WARNING result."""
        return cls(
            status=CheckStatus.WARNING,
            message=message,
            failure_mode_id=failure_mode_id,
            rpn=rpn,
            remediation=remediation,
            details=details,
        )

    @classmethod
    def critical(
        cls,
        message: str,
        failure_mode_id: str = "",
        rpn: Optional[RPNScore] = None,
        remediation: str = "",
        **details,
    ) -> CheckResult:
        """Create a CRITICAL result."""
        return cls(
            status=CheckStatus.CRITICAL,
            message=message,
            failure_mode_id=failure_mode_id,
            rpn=rpn,
            remediation=remediation,
            details=details,
        )

    @classmethod
    def error(cls, message: str, **details) -> CheckResult:
        """Create an ERROR result (check itself failed)."""
        return cls(status=CheckStatus.ERROR, message=message, details=details)

    @classmethod
    def skipped(cls, reason: str) -> CheckResult:
        """Create a SKIPPED result."""
        return cls(status=CheckStatus.SKIPPED, message=reason)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "status": self.status.value,
            "message": self.message,
            "details": self.details,
            "duration_ms": self.duration_ms,
        }
        if self.failure_mode_id:
            result["failure_mode_id"] = self.failure_mode_id
        if self.rpn:
            result["rpn"] = self.rpn.to_dict()
        if self.remediation:
            result["remediation"] = self.remediation
        return result


@dataclass
class HealthContext:
    """Runtime context for health checks.

    Provides access to configuration, catalog, and service endpoints.
    """
    config: Any  # BootstrapConfig
    catalog: Any = None  # PyIceberg catalog
    flink_url: str = "http://localhost:8081"
    minio_endpoint: str = "http://localhost:9010"
    polaris_url: str = "http://localhost:8181"
    postgres_port: int = 5438
    browser_port: int = 5050
    nifi_url: str = "http://localhost:8450"
    nifi_otlp_port: int = 4319


@dataclass
class HealthReport:
    """Complete health check report.

    Attributes:
        status: Overall status (healthy, degraded, critical)
        timestamp: When the report was generated
        checks: Check results grouped by category
        issues: List of detected issues with RPN scores
        recommendations: Prioritized remediation recommendations
    """
    status: str  # "healthy", "degraded", "critical"
    timestamp: datetime
    checks: dict[str, list[CheckResult]]
    issues: list[CheckResult]
    recommendations: list[str] = field(default_factory=list)

    @classmethod
    def from_results(cls, results: dict[str, list[CheckResult]]) -> HealthReport:
        """Create a report from categorized check results."""
        issues = []
        for category_results in results.values():
            for result in category_results:
                if result.status in (CheckStatus.WARNING, CheckStatus.CRITICAL):
                    issues.append(result)

        # Sort issues by RPN (highest first)
        issues.sort(key=lambda r: r.rpn.rpn if r.rpn else 0, reverse=True)

        # Determine overall status
        if any(r.status == CheckStatus.CRITICAL for r in issues):
            status = "critical"
        elif issues:
            status = "degraded"
        else:
            status = "healthy"

        # Generate recommendations
        recommendations = []
        for i, issue in enumerate(issues, 1):
            rpn_str = f"RPN {issue.rpn.rpn}" if issue.rpn else ""
            recommendations.append(
                f"{i}. {issue.failure_mode_id} ({rpn_str}): {issue.remediation or issue.message}"
            )

        return cls(
            status=status,
            timestamp=datetime.now(),
            checks=results,
            issues=issues,
            recommendations=recommendations,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        # Count totals across all categories
        total = 0
        healthy = 0
        for results in self.checks.values():
            for r in results:
                total += 1
                if r.status == CheckStatus.OK:
                    healthy += 1

        return {
            "status": self.status,
            "timestamp": self.timestamp.isoformat(),
            "checks": {
                cat: [r.to_dict() for r in results]
                for cat, results in self.checks.items()
            },
            "issues": [r.to_dict() for r in self.issues],
            "recommendations": self.recommendations,
            "summary": {
                "total": total,
                "healthy": healthy,
                "issues": len(self.issues),
            },
        }
