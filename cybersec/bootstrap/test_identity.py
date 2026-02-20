"""Tests for developer identity module."""

import hashlib
import os
from unittest.mock import patch, MagicMock
import pytest

from .identity import (
    get_git_email,
    get_git_username,
    get_developer_identity,
    generate_developer_prefix,
    get_developer_prefix,
    get_aws_bucket_name,
    get_developer_email,
)


class TestGenerateDeveloperPrefix:
    """Tests for generate_developer_prefix function."""

    def test_prefix_is_8_chars(self):
        """Prefix should always be exactly 8 characters."""
        prefix = generate_developer_prefix("test@example.com")
        assert len(prefix) == 8

    def test_prefix_is_hex(self):
        """Prefix should only contain lowercase hex characters."""
        prefix = generate_developer_prefix("test@example.com")
        assert all(c in "0123456789abcdef" for c in prefix)

    def test_prefix_is_stable(self):
        """Same identity should always produce same prefix."""
        prefix1 = generate_developer_prefix("dev@company.com")
        prefix2 = generate_developer_prefix("dev@company.com")
        assert prefix1 == prefix2

    def test_different_identities_produce_different_prefixes(self):
        """Different identities should produce different prefixes."""
        prefix1 = generate_developer_prefix("alice@example.com")
        prefix2 = generate_developer_prefix("bob@example.com")
        assert prefix1 != prefix2

    def test_prefix_matches_sha256_first_8(self):
        """Prefix should match first 8 chars of SHA-256 hash."""
        identity = "test@example.com"
        expected = hashlib.sha256(identity.encode()).hexdigest()[:8]
        prefix = generate_developer_prefix(identity)
        assert prefix == expected


class TestGetDeveloperPrefix:
    """Tests for get_developer_prefix function."""

    def test_uses_config_prefix_if_set(self):
        """Should use configured prefix if available."""
        config = MagicMock()
        config.developer_prefix = "deadbeef"
        prefix = get_developer_prefix(config)
        assert prefix == "deadbeef"

    def test_autogenerates_if_config_prefix_empty(self):
        """Should auto-generate if config prefix is empty string."""
        config = MagicMock()
        config.developer_prefix = ""
        with patch("cybersec.bootstrap.identity.get_developer_identity") as mock:
            mock.return_value = "test@example.com"
            prefix = get_developer_prefix(config)
            assert len(prefix) == 8

    def test_autogenerates_if_no_config(self):
        """Should auto-generate if no config provided."""
        with patch("cybersec.bootstrap.identity.get_developer_identity") as mock:
            mock.return_value = "test@example.com"
            prefix = get_developer_prefix(None)
            assert len(prefix) == 8


class TestGetAwsBucketName:
    """Tests for get_aws_bucket_name function."""

    def test_bucket_name_format(self):
        """Bucket name should follow {project}-{prefix}-data format."""
        with patch("cybersec.bootstrap.identity.get_developer_prefix") as mock:
            mock.return_value = "abc12345"
            bucket = get_aws_bucket_name("cybersec", None)
            assert bucket == "cybersec-abc12345-data"

    def test_bucket_name_uses_project(self):
        """Bucket name should incorporate project name."""
        with patch("cybersec.bootstrap.identity.get_developer_prefix") as mock:
            mock.return_value = "12345678"
            bucket1 = get_aws_bucket_name("project-a", None)
            bucket2 = get_aws_bucket_name("project-b", None)
            assert "project-a" in bucket1
            assert "project-b" in bucket2


class TestGetDeveloperEmail:
    """Tests for get_developer_email function."""

    def test_uses_config_email_if_set(self):
        """Should use configured email if available."""
        config = MagicMock()
        config.developer_email = "configured@example.com"
        email = get_developer_email(config)
        assert email == "configured@example.com"

    def test_uses_git_email_if_config_empty(self):
        """Should use git email if config email is empty."""
        config = MagicMock()
        config.developer_email = ""
        with patch("cybersec.bootstrap.identity.get_git_email") as mock:
            mock.return_value = "git@example.com"
            email = get_developer_email(config)
            assert email == "git@example.com"

    def test_fallback_to_user_at_local(self):
        """Should fallback to USER@local if no other option."""
        config = MagicMock()
        config.developer_email = ""
        with patch("cybersec.bootstrap.identity.get_git_email") as mock_git:
            mock_git.return_value = None
            with patch.dict(os.environ, {"USER": "testuser"}, clear=False):
                email = get_developer_email(config)
                assert email == "testuser@local"


class TestGetDeveloperIdentity:
    """Tests for get_developer_identity function."""

    def test_prefers_git_email(self):
        """Should prefer git email over $USER."""
        with patch("cybersec.bootstrap.identity.get_git_email") as mock:
            mock.return_value = "git@example.com"
            identity = get_developer_identity()
            assert identity == "git@example.com"

    def test_falls_back_to_user(self):
        """Should fall back to $USER if git email not available."""
        with patch("cybersec.bootstrap.identity.get_git_email") as mock:
            mock.return_value = None
            with patch.dict(os.environ, {"USER": "testuser"}, clear=False):
                identity = get_developer_identity()
                assert identity == "testuser"

    def test_ultimate_fallback(self):
        """Should return 'unknown' if nothing else available."""
        with patch("cybersec.bootstrap.identity.get_git_email") as mock:
            mock.return_value = None
            with patch.dict(os.environ, {}, clear=True):
                # Clear USER env var
                os.environ.pop("USER", None)
                identity = get_developer_identity()
                assert identity == "unknown"
