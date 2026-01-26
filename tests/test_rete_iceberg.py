"""Tests for Iceberg optimization using RETE rules."""

import pytest

from cybersec.rete.iceberg import IcebergOptimizer, TableStats, ICEBERG_RULES


class TestIcebergOptimizer:
    """Test Iceberg table optimization."""

    def test_detect_small_files_needing_compaction(self):
        """Test detection of tables with small files."""
        optimizer = IcebergOptimizer()

        optimizer.add_table(
            TableStats(
                table_name="cloudtrail",
                row_count=10_000_000,
                file_count=500,
                total_size_mb=2048,
                avg_file_size_mb=4,  # Very small files
                partition_count=30,
            )
        )

        recommendations = optimizer.analyze()

        # Should recommend compaction
        compaction_recs = [r for r in recommendations if r.action_type == "compaction"]
        assert len(compaction_recs) > 0
        assert "cloudtrail" in compaction_recs[0].table_name

    def test_no_compaction_for_optimal_files(self):
        """Test that optimal file sizes don't trigger compaction."""
        optimizer = IcebergOptimizer()

        optimizer.add_table(
            TableStats(
                table_name="events",
                row_count=1_000_000,
                file_count=10,
                total_size_mb=1280,
                avg_file_size_mb=128,  # Optimal size
                partition_count=10,
            )
        )

        recommendations = optimizer.analyze()

        # Should NOT recommend compaction
        compaction_recs = [r for r in recommendations if r.action_type == "compaction"]
        # Filter to only this table
        table_compaction = [r for r in compaction_recs if r.table_name == "events"]
        assert len(table_compaction) == 0

    def test_detect_too_many_snapshots(self):
        """Test detection of tables with too many snapshots."""
        optimizer = IcebergOptimizer()

        optimizer.add_table(
            TableStats(
                table_name="logs",
                row_count=5_000_000,
                file_count=50,
                total_size_mb=5000,
                avg_file_size_mb=100,
                partition_count=50,
                snapshot_count=100,  # Too many snapshots
            )
        )

        recommendations = optimizer.analyze()

        # Should recommend snapshot expiration
        expire_recs = [r for r in recommendations if r.action_type == "expire_snapshots"]
        assert len(expire_recs) > 0

    def test_suggest_partitioning_for_large_table(self):
        """Test partition evolution suggestion for large unpartitioned table."""
        optimizer = IcebergOptimizer()

        optimizer.add_table(
            TableStats(
                table_name="events",
                row_count=15_000_000,  # Large table
                file_count=100,
                total_size_mb=10000,
                avg_file_size_mb=100,
                partition_count=1,  # Unpartitioned
                partition_spec="",  # No partition spec
            )
        )

        # Add query pattern indicating time-range queries
        optimizer.add_query_pattern(
            "events",
            filters_on_timestamp=True,
            uses_time_range=True,
        )

        recommendations = optimizer.analyze()

        # Should suggest partitioning
        partition_recs = [r for r in recommendations if r.action_type == "partition_evolution"]
        assert len(partition_recs) > 0
        assert "hours" in partition_recs[0].command.lower()  # Hourly for 10M+ rows

    def test_alert_on_full_table_scan(self):
        """Test alert when query pattern indicates full table scans."""
        optimizer = IcebergOptimizer()

        optimizer.add_table(
            TableStats(
                table_name="audit_logs",
                row_count=500_000,
                file_count=200,
                total_size_mb=1000,
                avg_file_size_mb=5,
                partition_count=20,
            )
        )

        # Add query pattern indicating full scans
        optimizer.add_query_pattern(
            "audit_logs",
            filters_on_partition=False,
            filters_on_timestamp=False,
            avg_files_scanned=200,  # Scanning all files
        )

        recommendations = optimizer.analyze()

        # Should alert about full table scan
        alerts = [r for r in recommendations if r.action_type == "alert"]
        full_scan_alerts = [a for a in alerts if "full" in a.description.lower() or "scan" in a.description.lower()]
        assert len(full_scan_alerts) > 0

    def test_explain_rule(self):
        """Test rule explanation for debugging."""
        optimizer = IcebergOptimizer()

        optimizer.add_table(
            TableStats(
                table_name="test",
                row_count=100,
                file_count=5,
                total_size_mb=10,
                avg_file_size_mb=2,
                partition_count=1,
            )
        )

        explanation = optimizer.explain_rule("compact_small_files")

        assert "rule_id" in explanation
        assert "conditions" in explanation
        # With only 5 files, the rule shouldn't fire (need >10 files)
        # Check if conditions show the actual values
        assert len(explanation["conditions"]) > 0

    def test_multiple_recommendations_sorted_by_priority(self):
        """Test that multiple recommendations are sorted by priority."""
        optimizer = IcebergOptimizer()

        # Table with multiple issues
        optimizer.add_table(
            TableStats(
                table_name="problematic",
                row_count=20_000_000,
                file_count=2000,
                total_size_mb=10000,
                avg_file_size_mb=5,  # Small files
                partition_count=50,
                snapshot_count=100,  # Too many snapshots
                days_since_compaction=30,
            )
        )

        optimizer.add_query_pattern(
            "problematic",
            filters_on_partition=False,
            uses_time_range=True,
            avg_files_scanned=500,
        )

        recommendations = optimizer.analyze()

        # Should have multiple recommendations
        assert len(recommendations) > 1

        # Should be sorted by priority (highest first)
        priorities = [r.priority for r in recommendations]
        assert priorities == sorted(priorities, reverse=True)


class TestTableStats:
    """Test TableStats dataclass."""

    def test_to_fact_conversion(self):
        """Test conversion to RETE fact."""
        stats = TableStats(
            table_name="test_table",
            row_count=1000000,
            file_count=100,
            total_size_mb=500,
            avg_file_size_mb=5,
            partition_count=10,
        )

        fact = stats.to_fact()

        assert fact.fact_type == "table"
        assert fact.fact_id == "test_table"
        assert fact.attributes["row_count"] == 1000000
        assert fact.attributes["needs_compaction"] is True  # avg < 32MB

    def test_computed_attributes(self):
        """Test computed attributes in table fact."""
        stats = TableStats(
            table_name="test",
            row_count=1000,
            file_count=100,
            total_size_mb=100,
            avg_file_size_mb=1,
            partition_count=10,
        )

        fact = stats.to_fact()

        # files_per_partition = 100 / 10 = 10
        assert fact.attributes["files_per_partition"] == 10
        # needs_compaction = True (avg_file_size < 32 or files > partition * 10)
        assert fact.attributes["needs_compaction"] is True


class TestIcebergRules:
    """Test the built-in Iceberg optimization rules."""

    def test_all_rules_have_required_fields(self):
        """Test that all rules have required fields."""
        for rule in ICEBERG_RULES:
            assert rule.rule_id, "Rule must have rule_id"
            assert rule.conditions, "Rule must have conditions"
            assert rule.actions, "Rule must have actions"
            assert rule.priority > 0, "Rule must have positive priority"
            assert rule.category, "Rule should have category"
            assert rule.description, "Rule should have description"

    def test_rule_categories(self):
        """Test that rules are properly categorized."""
        categories = {rule.category for rule in ICEBERG_RULES}

        expected = {"compaction", "metadata", "maintenance", "partitioning", "query", "optimization"}
        assert categories == expected

    def test_rule_priorities_in_range(self):
        """Test that rule priorities are in expected ranges."""
        for rule in ICEBERG_RULES:
            assert 1 <= rule.priority <= 1000, f"Rule {rule.rule_id} priority out of range"

            # Query alerts should be high priority
            if rule.category == "query":
                assert rule.priority >= 800, f"Query rule {rule.rule_id} should have high priority"
