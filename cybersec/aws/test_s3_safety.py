"""Tests for S3 safety module."""

from unittest.mock import patch, MagicMock
import json
import pytest

from .s3_safety import (
    BucketVerificationResult,
    get_bucket_tags,
    verify_bucket_ownership,
)


class TestBucketVerificationResult:
    """Tests for BucketVerificationResult dataclass."""

    def test_verified_when_all_conditions_met(self):
        """Verified should be True when exists, accessible, and tags match."""
        result = BucketVerificationResult(
            bucket_name="test-bucket",
            exists=True,
            accessible=True,
            tags_match=True,
        )
        assert result.verified is True

    def test_not_verified_when_not_exists(self):
        """Verified should be False when bucket doesn't exist."""
        result = BucketVerificationResult(
            bucket_name="test-bucket",
            exists=False,
            accessible=False,
            tags_match=False,
        )
        assert result.verified is False

    def test_not_verified_when_not_accessible(self):
        """Verified should be False when bucket not accessible."""
        result = BucketVerificationResult(
            bucket_name="test-bucket",
            exists=True,
            accessible=False,
            tags_match=False,
        )
        assert result.verified is False

    def test_not_verified_when_tags_mismatch(self):
        """Verified should be False when tags don't match."""
        result = BucketVerificationResult(
            bucket_name="test-bucket",
            exists=True,
            accessible=True,
            tags_match=False,
            mismatched_tags={"Owner": ("expected@example.com", "other@example.com")},
        )
        assert result.verified is False

    def test_format_report_bucket_not_exists(self):
        """Report should indicate bucket doesn't exist."""
        result = BucketVerificationResult(
            bucket_name="test-bucket",
            exists=False,
        )
        report = result.format_report()
        assert "does not exist" in report

    def test_format_report_tags_match(self):
        """Report should show success when tags match."""
        result = BucketVerificationResult(
            bucket_name="test-bucket",
            exists=True,
            accessible=True,
            tags_match=True,
            expected_tags={"ManagedBy": "opentofu", "Owner": "dev@example.com"},
        )
        report = result.format_report()
        assert "PASSED" in report

    def test_format_report_tags_mismatch(self):
        """Report should show failure and mismatched tags."""
        result = BucketVerificationResult(
            bucket_name="test-bucket",
            exists=True,
            accessible=True,
            tags_match=False,
            expected_tags={"Owner": "dev@example.com"},
            mismatched_tags={"Owner": ("dev@example.com", "other@example.com")},
        )
        report = result.format_report()
        assert "FAILED" in report
        assert "dev@example.com" in report
        assert "other@example.com" in report


class TestVerifyBucketOwnership:
    """Tests for verify_bucket_ownership function."""

    def test_returns_not_exists_when_bucket_missing(self):
        """Should return exists=False when bucket doesn't exist."""
        with patch("cybersec.aws.s3_safety.get_bucket_tags") as mock:
            mock.return_value = (False, False, {}, None)
            result = verify_bucket_ownership(
                "missing-bucket",
                {"ManagedBy": "opentofu"},
            )
            assert result.exists is False
            assert result.verified is False

    def test_returns_not_accessible_when_403(self):
        """Should return accessible=False when permission denied."""
        with patch("cybersec.aws.s3_safety.get_bucket_tags") as mock:
            mock.return_value = (True, False, {}, "Access denied")
            result = verify_bucket_ownership(
                "private-bucket",
                {"ManagedBy": "opentofu"},
            )
            assert result.exists is True
            assert result.accessible is False
            assert result.verified is False

    def test_tags_match_when_all_present(self):
        """Should return tags_match=True when all expected tags match."""
        with patch("cybersec.aws.s3_safety.get_bucket_tags") as mock:
            mock.return_value = (
                True,
                True,
                {"ManagedBy": "opentofu", "Owner": "dev@example.com"},
                None,
            )
            result = verify_bucket_ownership(
                "my-bucket",
                {"ManagedBy": "opentofu", "Owner": "dev@example.com"},
            )
            assert result.tags_match is True
            assert result.verified is True

    def test_tags_mismatch_when_owner_different(self):
        """Should detect mismatched Owner tag."""
        with patch("cybersec.aws.s3_safety.get_bucket_tags") as mock:
            mock.return_value = (
                True,
                True,
                {"ManagedBy": "opentofu", "Owner": "other@example.com"},
                None,
            )
            result = verify_bucket_ownership(
                "someone-elses-bucket",
                {"ManagedBy": "opentofu", "Owner": "dev@example.com"},
            )
            assert result.tags_match is False
            assert result.verified is False
            assert "Owner" in result.mismatched_tags
            assert result.mismatched_tags["Owner"] == ("dev@example.com", "other@example.com")

    def test_tags_mismatch_when_tag_missing(self):
        """Should detect when expected tag is missing."""
        with patch("cybersec.aws.s3_safety.get_bucket_tags") as mock:
            mock.return_value = (
                True,
                True,
                {"ManagedBy": "opentofu"},  # Owner tag missing
                None,
            )
            result = verify_bucket_ownership(
                "unowned-bucket",
                {"ManagedBy": "opentofu", "Owner": "dev@example.com"},
            )
            assert result.tags_match is False
            assert "Owner" in result.mismatched_tags
