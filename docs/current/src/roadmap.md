# Roadmap

## Timeline Overview

```d2
direction: right

Q1_2026: {
  label: "Q1 2026\nFoundation"
  style.fill: "#e8f5e9"

  aws_infra: "AWS Infrastructure\n(OpenTofu)"
  flink_ingest: "Flink Ingestion\nPipeline"
  iceberg_tables: "S3 Iceberg\nTables"
}

Q2_2026: {
  label: "Q2 2026\nHybrid"
  style.fill: "#fff3e0"

  onprem_cluster: "On-Prem Cluster\nDeployment"
  replication: "Replication\nManager"
  lakehouse_opt: "Lakehouse\nOptimizer"
}

Q3_2026: {
  label: "Q3 2026\nOperations"
  style.fill: "#e3f2fd"

  monitoring: "Monitoring &\nAlerting"
  glacier_workflow: "Glacier\nRetrieval"
  verification: "Data\nVerification"
}

Q4_2026: {
  label: "Q4 2026\nAnalytics"
  style.fill: "#f3e5f5"

  ml_pipeline: "ML/AI\nPipeline"
  dashboards: "Security\nDashboards"
  automation: "Incident\nAutomation"
}

2027_2028: {
  label: "2027-2028\nDR Transition"
  style.fill: "#fce4ec"

  expand_onprem: "Expand On-Prem\nStorage"
  reduce_glacier: "Reduce Glacier\nDependency"
  full_dr: "Full DR\nCapability"
}

Q1_2026 -> Q2_2026 -> Q3_2026 -> Q4_2026 -> 2027_2028
```

## Phase 1: Foundation (Q1 2026)

### Goals
- Establish AWS infrastructure for CloudTrail ingestion
- Deploy Flink pipeline for real-time processing
- Create S3 Iceberg tables with disabled AWS optimizer

### Deliverables

| Milestone | Target Date | Owner |
|-----------|-------------|-------|
| OpenTofu modules complete | Jan 31 | Cloud Team |
| CloudTrail organization trail | Feb 15 | Security Team |
| Flink cluster on EKS | Feb 28 | Data Platform |
| Iceberg tables (disabled optimizer) | Mar 15 | Data Platform |
| Initial data flowing | Mar 31 | Data Platform |

### Success Criteria
- [ ] CloudTrail events landing in S3 Iceberg < 5 min latency
- [ ] Athena queries working for verification
- [ ] AWS optimizer confirmed disabled
- [ ] Lifecycle policies active (30/90/365/1095 day tiers)

## Phase 2: Hybrid (Q2 2026)

### Goals
- Deploy on-prem Cloudera cluster
- Establish replication from AWS
- Configure Lakehouse Optimizer

### Deliverables

| Milestone | Target Date | Owner |
|-----------|-------------|-------|
| On-prem hardware provisioned | Apr 15 | Infrastructure |
| CDP Private Cloud installed | Apr 30 | Platform Team |
| Replication Manager configured | May 15 | Data Platform |
| Lakehouse Optimizer tuned | May 31 | Data Platform |
| 90-day data replicated | Jun 30 | Data Platform |

### Success Criteria
- [ ] On-prem cluster healthy (6+ worker nodes)
- [ ] Replication lag < 15 minutes
- [ ] Lakehouse Optimizer running on schedule
- [ ] Impala queries returning results

## Phase 3: Operations (Q3 2026)

### Goals
- Establish operational procedures
- Implement monitoring and alerting
- Build Glacier retrieval workflows

### Deliverables

| Milestone | Target Date | Owner |
|-----------|-------------|-------|
| Monitoring dashboards | Jul 15 | Platform Ops |
| Alerting rules configured | Jul 31 | Platform Ops |
| Glacier retrieval scripts | Aug 15 | Data Platform |
| Data verification pipeline | Aug 31 | Data Platform |
| Runbooks documented | Sep 30 | All Teams |

### Success Criteria
- [ ] Daily verification reports automated
- [ ] Alerts firing for replication lag > 15 min
- [ ] Glacier restore tested (1-week dataset)
- [ ] Incident response runbook exercised

## Phase 4: Analytics (Q4 2026)

### Goals
- Enable security analytics use cases
- Deploy ML/AI pipelines
- Build investigation dashboards

### Deliverables

| Milestone | Target Date | Owner |
|-----------|-------------|-------|
| Cloudera AI workbench | Oct 15 | Data Science |
| Anomaly detection model | Oct 31 | Data Science |
| Security dashboards (CDV) | Nov 15 | Security Team |
| Automated incident triage | Nov 30 | Security Team |
| Production ML inference | Dec 31 | Data Science |

### Success Criteria
- [ ] Real-time anomaly alerts generating
- [ ] Security team using dashboards daily
- [ ] At least 3 ML models in production
- [ ] Mean time to detect (MTTD) improved 50%

## Phase 5: DR Transition (2027-2028)

### Goals
- Expand on-prem storage capacity
- Reduce reliance on AWS Glacier
- Achieve full disaster recovery capability

### Year 1 (2027)

| Quarter | Milestone |
|---------|-----------|
| Q1 | Expand to 1-year retention on-prem |
| Q2 | Add DR site (secondary on-prem) |
| Q3 | Implement on-prem archival tier |
| Q4 | Begin reducing Glacier usage |

### Year 2 (2028)

| Quarter | Milestone |
|---------|-----------|
| Q1 | 3-year retention on-prem |
| Q2 | Active-active replication |
| Q3 | Glacier for compliance-only |
| Q4 | Full 7-year on-prem capability |

### Long-Term State

```d2
direction: right

Future: {
  label: "Target State (2028+)"

  OnPrem_Primary: {
    label: "On-Prem Primary"

    hot: "Hot Tier\n(0-90 days)"
    warm: "Warm Tier\n(90 days - 1 year)"
    cold: "Cold Tier\n(1-7 years)"
  }

  OnPrem_DR: {
    label: "On-Prem DR"

    replica: "Active Replica\n(all data)"
  }

  AWS: {
    label: "AWS (Minimal)"

    ingest: "Flink Ingestion\n(staging only)"
    glacier: "Glacier\n(compliance backup)"
  }

  AWS.ingest -> OnPrem_Primary.hot: "Replicate\n(< 5 min)"
  OnPrem_Primary.hot -> OnPrem_DR.replica: "Sync"
  OnPrem_Primary.cold -> AWS.glacier: "Backup only" {style.stroke-dash: 5}
}
```

## Risk Register

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| AWS optimizer conflict | High | Medium | Verify disabled in all envs |
| Replication bandwidth | Medium | Medium | Direct Connect upgrade |
| Glacier restore time | Medium | Low | Pre-stage known audit periods |
| On-prem capacity growth | High | Medium | Quarterly capacity planning |
| Key person dependency | Medium | Medium | Cross-training, documentation |

## Budget Estimates

### AWS Costs (Monthly)

| Component | Phase 1-2 | Phase 3-4 | Long-term |
|-----------|-----------|-----------|-----------|
| S3 Storage | $2,000 | $3,000 | $1,000 |
| Glacier | $200 | $500 | $5,000 |
| Flink (EKS) | $1,500 | $1,500 | $500 |
| Data Transfer | $500 | $1,000 | $200 |
| **Total** | **$4,200** | **$6,000** | **$6,700** |

### On-Prem Costs (One-time + Annual)

| Component | One-time | Annual |
|-----------|----------|--------|
| Hardware (Phase 1) | $250,000 | $25,000 |
| Hardware (Expansion) | $500,000 | $50,000 |
| CDP Licenses | - | $150,000 |
| DR Site | $400,000 | $40,000 |
