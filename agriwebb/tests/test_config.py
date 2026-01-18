"""Tests for cache configuration."""

from pathlib import Path

from agriwebb.core.config import get_cache_dir


class TestGetCacheDir:
    """Tests for the get_cache_dir function."""

    def test_returns_path_object(self):
        """Verify get_cache_dir returns a Path object."""
        result = get_cache_dir()
        assert isinstance(result, Path)

    def test_returns_cache_dir_in_project_root(self):
        """Verify cache dir is in project root (where .git is)."""
        result = get_cache_dir()
        # Should end with .cache
        assert result.name == ".cache"
        # Parent should contain .git or .claude
        parent = result.parent
        assert (parent / ".git").exists() or (parent / ".claude").exists()

    def test_cache_dir_exists_after_call(self):
        """Verify the cache directory is created if it doesn't exist."""
        result = get_cache_dir()
        assert result.exists()
        assert result.is_dir()

    def test_returns_same_path_on_multiple_calls(self):
        """Verify function returns consistent path (cached)."""
        result1 = get_cache_dir()
        result2 = get_cache_dir()
        assert result1 == result2

    def test_finds_git_repo_root(self):
        """Verify function finds project root by looking for .git."""
        result = get_cache_dir()

        # The .cache directory should be a sibling of .git
        parent = result.parent
        assert (parent / ".git").exists() or (parent / ".claude").exists(), (
            f"Cache dir {result} should be under a directory with .git or .claude"
        )

    def test_fallback_behavior_doesnt_crash(self):
        """Verify the function handles edge cases gracefully."""
        # Just verify the function doesn't crash and returns a valid path
        result = get_cache_dir()
        assert result is not None
        assert isinstance(result, Path)


class TestCacheIntegration:
    """Integration tests for cache usage across modules."""

    def test_modules_use_same_cache_dir(self):
        """Verify all modules get the same cache directory."""
        from agriwebb.core import get_cache_dir as core_get_cache_dir
        from agriwebb.data.grazing import get_cache_dir as grazing_get_cache_dir
        from agriwebb.weather.ncei import get_cache_dir as ncei_get_cache_dir

        # All should return the same path
        core_path = core_get_cache_dir()
        grazing_path = grazing_get_cache_dir()
        ncei_path = ncei_get_cache_dir()

        assert core_path == grazing_path
        assert grazing_path == ncei_path

    def test_cache_files_are_accessible(self):
        """Verify standard cache files can be read."""
        cache_dir = get_cache_dir()

        # Check for common cache files (may not all exist)
        expected_files = [
            "animals.json",
            "weather_historical.json",
            "paddock_soils.json",
        ]

        for filename in expected_files:
            filepath = cache_dir / filename
            if filepath.exists():
                # Verify we can read it
                assert filepath.stat().st_size > 0, f"{filename} should not be empty"

    def test_cache_dir_is_not_in_home_cache(self):
        """Verify cache is NOT in ~/.cache/agriwebb (the old location)."""
        result = get_cache_dir()
        home_cache = Path.home() / ".cache" / "agriwebb"

        assert result != home_cache, "Cache should not be in ~/.cache/agriwebb"

    def test_cache_dir_is_in_project(self):
        """Verify cache is in the project directory."""
        result = get_cache_dir()

        # The cache should be under the farm project
        assert "farm" in str(result) or "agriwebb" in str(result)
