# Cybersec System Heuristics

This collection contains diagnostic heuristics for the cybersec data pipeline. Each heuristic follows a four-component structure with FMEA integration:

1. **Symptom** - Brief observable failure description
2. **Cause** - Low-level explanation of why this occurs
3. **Observation** - Detection code and signals
4. **Solution** - Target state and remediation process

## FMEA Integration

Each heuristic includes an FMEA reference with Risk Priority Number:

```
RPN = Severity × Occurrence × Detection (1-1000)

Escalation Tiers:
- TIER_0 (RPN 1-100): Auto-remediate immediately
- TIER_1 (RPN 101-200): Auto-remediate with agent validation
- TIER_2 (RPN 201-400): Require user approval
- MANUAL (RPN 401-1000): Human intervention required
```

## Automation Levels

Each observation and solution is classified:

- **A** (Full Automation) - Agent handles autonomously
- **B** (Partial Automation) - Agent + human option
- **C** (Knowledge Transfer) - Agent provides awareness, human acts

## Categories

- `iceberg/` - PyIceberg catalog, table, and scan issues
- `flink/` - Flink JobManager, TaskManager, job issues
- `infra/` - PostgreSQL, MinIO, Polaris infrastructure
- `data/` - Data quality, staleness, accumulation

## Usage

```
/health              - Run full health check
/health quick        - Run critical checks only (QUICK_CHECKS)
/health iceberg      - Run checks for specific category
/health fix ICE_001  - Attempt automated remediation
```

## Adding New Heuristics

1. Create a markdown file in the appropriate category directory
2. Follow the template format with FMEA reference
3. Add a corresponding failure mode to `cybersec/health/catalog.py`
4. Implement the check function in `cybersec/health/checks/<category>.py`
