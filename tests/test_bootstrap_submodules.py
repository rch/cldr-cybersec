"""Tests for submodules management."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from cybersec.bootstrap.submodules import (
    SubmoduleSpec,
    SUBMODULE_SPECS,
    is_submodule_initialized,
    get_submodule_branch,
    prepare_submodule,
    get_submodule_status,
)


class TestSubmoduleSpecs:
    """Test submodule specifications."""

    def test_known_submodules(self):
        """All expected submodules are defined."""
        assert "flink" in SUBMODULE_SPECS
        assert "polaris" in SUBMODULE_SPECS
        assert "iceberg" in SUBMODULE_SPECS

    def test_flink_spec(self):
        """Flink submodule spec is correct."""
        spec = SUBMODULE_SPECS["flink"]
        assert spec.path == "thirdparty/flink"
        assert spec.expected_branch == "rch/devenv-cybersec"
        assert "flink" in spec.required_for

    def test_polaris_spec(self):
        """Polaris submodule spec is correct."""
        spec = SUBMODULE_SPECS["polaris"]
        assert spec.path == "thirdparty/polaris"
        assert spec.expected_branch == "main"
        assert "polaris" in spec.required_for


class TestIsSubmoduleInitialized:
    """Test submodule initialization detection."""

    def test_unknown_submodule(self):
        """Unknown submodule returns True (considered OK)."""
        assert is_submodule_initialized("nonexistent") is True

    def test_flink_initialized(self):
        """Flink submodule is initialized if pom.xml exists."""
        # This test assumes we're running from project root
        path = Path("thirdparty/flink")
        if path.exists() and (path / "pom.xml").exists():
            assert is_submodule_initialized("flink") is True

    def test_polaris_initialized(self):
        """Polaris submodule is initialized if build.gradle.kts exists."""
        path = Path("thirdparty/polaris")
        if path.exists() and (path / "build.gradle.kts").exists():
            assert is_submodule_initialized("polaris") is True


class TestGetSubmoduleBranch:
    """Test branch detection."""

    def test_unknown_submodule(self):
        """Unknown submodule returns None."""
        assert get_submodule_branch("nonexistent") is None

    def test_initialized_submodule_has_branch(self):
        """Initialized submodule returns branch or HEAD."""
        if is_submodule_initialized("flink"):
            branch = get_submodule_branch("flink")
            assert branch is not None
            # Branch should be a string
            assert isinstance(branch, str)


class TestPrepareSubmodule:
    """Test submodule preparation."""

    def test_unknown_submodule(self):
        """Unknown submodule returns success with skip message."""
        ok, msg = prepare_submodule("nonexistent")
        assert ok is True
        assert "Unknown" in msg or "skipped" in msg

    def test_initialized_submodule(self):
        """Already initialized submodule returns success."""
        if is_submodule_initialized("flink"):
            ok, msg = prepare_submodule("flink")
            assert ok is True
            assert "ready" in msg.lower()


class TestGetSubmoduleStatus:
    """Test status reporting."""

    def test_returns_all_submodules(self):
        """Status includes all known submodules."""
        status = get_submodule_status()
        assert "flink" in status
        assert "polaris" in status
        assert "iceberg" in status

    def test_status_fields(self):
        """Status entries have expected fields."""
        status = get_submodule_status()
        for name, info in status.items():
            assert "path" in info
            assert "initialized" in info
            assert "branch" in info
            assert "expected_branch" in info
            assert "on_expected_branch" in info
            assert "required_for" in info
