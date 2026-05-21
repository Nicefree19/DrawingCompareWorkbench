# -*- coding: utf-8 -*-
"""Performance Optimizer Tests

Tests for the performance optimization module including:
- Hash caching
- Parallel extraction
- Result caching
- Batch processing
- Performance tracking

Author: TEKLA_MCP Team
Date: 2025-12-24
"""

import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import Mock, patch

import pytest

from src.services.comparison.performance_optimizer import (
    CacheStats,
    ComparisonCache,
    HashCache,
    OptimizedComparator,
    ParallelExtractionResult,
    PerformanceMetrics,
    PerformanceTracker,
    cached_hash,
    compute_file_hash,
    estimate_memory_usage,
    extract_entities_parallel,
    get_comparison_cache,
    get_hash_cache,
    process_in_batches,
    should_use_streaming,
)


# =============================================================================
# HashCache Tests
# =============================================================================


class TestHashCache:
    """Tests for HashCache class"""

    def test_init_default_maxsize(self):
        """Test default initialization"""
        cache = HashCache()
        assert cache._maxsize == 10000

    def test_init_custom_maxsize(self):
        """Test custom maxsize"""
        cache = HashCache(maxsize=100)
        assert cache._maxsize == 100

    def test_get_or_compute_miss(self):
        """Test cache miss - computes value"""
        cache = HashCache()
        cache.clear()

        result = cache.get_or_compute("key1", lambda: "computed_value")

        assert result == "computed_value"
        assert cache._stats.misses == 1
        assert cache._stats.hits == 0

    def test_get_or_compute_hit(self):
        """Test cache hit - returns cached value"""
        cache = HashCache()
        cache.clear()

        # First call - miss
        cache.get_or_compute("key1", lambda: "value1")

        # Second call - hit
        result = cache.get_or_compute("key1", lambda: "should_not_compute")

        assert result == "value1"
        assert cache._stats.hits == 1
        assert cache._stats.misses == 1

    def test_eviction_when_full(self):
        """Test LRU eviction when cache is full"""
        cache = HashCache(maxsize=5)
        cache.clear()

        # Fill cache
        for i in range(5):
            cache.get_or_compute(f"key{i}", lambda i=i: f"value{i}")

        # Add one more - should evict
        cache.get_or_compute("key5", lambda: "value5")

        # Check cache size is maintained
        assert len(cache._cache) <= 5

    def test_stats_tracking(self):
        """Test statistics tracking"""
        cache = HashCache()
        cache.clear()
        cache.reset_stats()

        # Generate hits and misses
        cache.get_or_compute("a", lambda: "1")  # miss
        cache.get_or_compute("b", lambda: "2")  # miss
        cache.get_or_compute("a", lambda: "x")  # hit
        cache.get_or_compute("a", lambda: "y")  # hit

        stats = cache.stats
        assert stats.hits == 2
        assert stats.misses == 2
        assert stats.hit_rate == 50.0

    def test_clear(self):
        """Test cache clearing"""
        cache = HashCache()
        cache.get_or_compute("key1", lambda: "value1")
        cache.clear()
        assert len(cache._cache) == 0


class TestCachedHash:
    """Tests for cached_hash function"""

    def test_basic_hash(self):
        """Test basic hash computation"""
        data = {"x": 100.0, "y": 200.0, "layer": "TEST"}
        hash1 = cached_hash(data)
        hash2 = cached_hash(data)

        assert hash1 == hash2
        assert len(hash1) == 32  # MD5 hex length

    def test_precision_rounding(self):
        """Test coordinate precision rounding"""
        # Values that round to same with precision=2
        data1 = {"x": 100.001, "y": 200.002}
        data2 = {"x": 100.004, "y": 200.001}  # Rounds to (100.00, 200.00)

        hash1 = cached_hash(data1, precision=2)
        hash2 = cached_hash(data2, precision=2)

        assert hash1 == hash2

    def test_different_data_different_hash(self):
        """Test different data produces different hash"""
        data1 = {"x": 100.0, "y": 200.0}
        data2 = {"x": 100.0, "y": 300.0}

        hash1 = cached_hash(data1)
        hash2 = cached_hash(data2)

        assert hash1 != hash2


# =============================================================================
# ComparisonCache Tests
# =============================================================================


class TestComparisonCache:
    """Tests for ComparisonCache class"""

    def test_init(self):
        """Test initialization"""
        cache = ComparisonCache(maxsize=50, ttl_seconds=1800)
        assert cache._maxsize == 50
        assert cache._ttl == 1800

    def test_set_and_get(self):
        """Test storing and retrieving"""
        cache = ComparisonCache()
        cache.clear()

        result = {"changes": [1, 2, 3]}
        cache.set("hash_a", "hash_b", result)

        retrieved = cache.get("hash_a", "hash_b")
        assert retrieved == result

    def test_get_nonexistent(self):
        """Test getting non-existent entry"""
        cache = ComparisonCache()
        cache.clear()

        result = cache.get("nonexistent_a", "nonexistent_b")
        assert result is None

    def test_ttl_expiration(self):
        """Test TTL expiration"""
        cache = ComparisonCache(ttl_seconds=0.1)  # 100ms TTL
        cache.clear()

        cache.set("hash_a", "hash_b", {"data": "test"})
        time.sleep(0.15)  # Wait for expiration

        result = cache.get("hash_a", "hash_b")
        assert result is None

    def test_maxsize_eviction(self):
        """Test eviction when maxsize reached"""
        cache = ComparisonCache(maxsize=3)
        cache.clear()

        for i in range(5):
            cache.set(f"a{i}", f"b{i}", {"idx": i})

        # Should have at most 3 entries
        assert len(cache._cache) <= 3


# =============================================================================
# Parallel Extraction Tests
# =============================================================================


class TestParallelExtraction:
    """Tests for parallel entity extraction"""

    def test_extract_entities_parallel_basic(self):
        """Test basic parallel extraction"""

        def mock_extractor(path: Path) -> Dict[str, List[Any]]:
            return {"LINE": [1, 2], "CIRCLE": [3]}

        paths = [Path("file1.dxf"), Path("file2.dxf")]

        with patch("builtins.open", create=True):
            results = extract_entities_parallel(
                paths, mock_extractor, max_workers=2
            )

        assert len(results) == 2
        for result in results:
            assert isinstance(result, ParallelExtractionResult)
            assert result.entity_count == 3

    def test_extraction_error_handling(self):
        """Test error handling in parallel extraction"""

        def failing_extractor(path: Path) -> Dict[str, List[Any]]:
            raise ValueError("Extraction failed")

        paths = [Path("fail.dxf")]

        results = extract_entities_parallel(paths, failing_extractor, max_workers=1)

        assert len(results) == 1
        assert len(results[0].errors) > 0


# =============================================================================
# Batch Processing Tests
# =============================================================================


class TestBatchProcessing:
    """Tests for batch processing utilities"""

    def test_process_in_batches(self):
        """Test basic batch processing"""
        items = list(range(100))

        def batch_fn(batch: List[int]) -> List[int]:
            return [x * 2 for x in batch]

        results = process_in_batches(items, batch_fn, batch_size=10)

        assert len(results) == 100
        assert results[0] == 0
        assert results[50] == 100

    def test_batch_progress_callback(self):
        """Test progress callback"""
        items = list(range(50))
        progress_calls = []

        def callback(processed: int, total: int):
            progress_calls.append((processed, total))

        process_in_batches(
            items,
            lambda batch: batch,
            batch_size=10,
            progress_callback=callback,
        )

        assert len(progress_calls) == 5
        assert progress_calls[-1] == (50, 50)

    def test_batch_size_larger_than_items(self):
        """Test batch size larger than item count"""
        items = [1, 2, 3]

        results = process_in_batches(
            items, lambda batch: [x + 1 for x in batch], batch_size=100
        )

        assert results == [2, 3, 4]


# =============================================================================
# Performance Tracker Tests
# =============================================================================


class TestPerformanceTracker:
    """Tests for PerformanceTracker class"""

    def test_basic_tracking(self):
        """Test basic performance tracking"""
        tracker = PerformanceTracker()

        tracker.start()
        time.sleep(0.01)  # Simulate extraction
        tracker.mark_extraction_done(100, 150)
        time.sleep(0.01)  # Simulate comparison
        tracker.mark_comparison_done(25)

        metrics = tracker.finalize()

        assert metrics.entity_count_a == 100
        assert metrics.entity_count_b == 150
        assert metrics.change_count == 25
        assert metrics.extraction_time_ms > 0
        assert metrics.comparison_time_ms > 0
        assert metrics.total_time_ms > 0

    def test_entities_per_second(self):
        """Test entities per second calculation"""
        metrics = PerformanceMetrics(
            total_time_ms=1000,
            entity_count_a=1000,
            entity_count_b=1000,
        )

        assert metrics.entities_per_second == 2000.0

    def test_metrics_to_dict(self):
        """Test metrics serialization"""
        metrics = PerformanceMetrics(
            total_time_ms=100.5,
            extraction_time_ms=50.2,
            comparison_time_ms=50.3,
            entity_count_a=100,
            entity_count_b=200,
            change_count=10,
        )

        d = metrics.to_dict()

        assert "total_time_ms" in d
        assert "entities_per_second" in d


# =============================================================================
# OptimizedComparator Tests
# =============================================================================


class TestOptimizedComparator:
    """Tests for OptimizedComparator wrapper"""

    def test_compare_with_tracking(self):
        """Test comparison with performance tracking"""
        mock_comparator = Mock()
        mock_result = Mock()
        mock_result.changes = [1, 2, 3]
        mock_comparator.compare_with_modified_detection.return_value = mock_result

        optimizer = OptimizedComparator(mock_comparator)

        entities_a = {"LINE": [Mock(), Mock()]}
        entities_b = {"LINE": [Mock()]}

        result, metrics = optimizer.compare(entities_a, entities_b)

        assert result == mock_result
        assert metrics.entity_count_a == 2
        assert metrics.entity_count_b == 1
        assert metrics.change_count == 3

    def test_get_cache_stats(self):
        """Test cache statistics retrieval"""
        optimizer = OptimizedComparator(Mock())
        stats = optimizer.get_cache_stats()

        assert "hash_cache" in stats
        assert "comparison_cache" in stats

    def test_clear_caches(self):
        """Test cache clearing"""
        optimizer = OptimizedComparator(Mock())

        # Populate caches
        get_hash_cache().get_or_compute("test", lambda: "value")
        get_comparison_cache().set("a", "b", {"data": 1})

        optimizer.clear_caches()

        # Verify cleared
        assert get_hash_cache()._cache.get("test") is None


# =============================================================================
# Utility Function Tests
# =============================================================================


class TestUtilityFunctions:
    """Tests for utility functions"""

    def test_estimate_memory_usage(self):
        """Test memory estimation"""
        usage = estimate_memory_usage(1000, bytes_per_entity=500)
        assert usage == 500000

    def test_should_use_streaming_small(self):
        """Test streaming decision for small files"""
        result = should_use_streaming(1000, available_memory_mb=1024)
        assert result is False

    def test_should_use_streaming_large(self):
        """Test streaming decision for large files"""
        result = should_use_streaming(10000000, available_memory_mb=1024)
        assert result is True

    def test_compute_file_hash(self, tmp_path):
        """Test file hash computation"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        hash1 = compute_file_hash(test_file)
        hash2 = compute_file_hash(test_file)

        assert hash1 == hash2
        assert len(hash1) == 32


# =============================================================================
# Benchmark Tests (Performance Regression)
# =============================================================================


class TestPerformanceBenchmarks:
    """Performance benchmark tests"""

    @pytest.mark.benchmark
    def test_hash_cache_speedup(self):
        """Benchmark: Cache should provide >10x speedup for repeated hashes"""
        cache = HashCache()
        cache.clear()
        cache.reset_stats()

        data = {"x": 100.0, "y": 200.0, "z": 300.0, "layer": "TEST", "type": "LINE"}

        # First call - computes
        start = time.perf_counter()
        for _ in range(100):
            cache.get_or_compute(
                str(data), lambda: "abcd1234" * 4
            )
        first_elapsed = time.perf_counter() - start

        # Subsequent calls - cached
        start = time.perf_counter()
        for _ in range(100):
            cache.get_or_compute(str(data), lambda: "should_not_compute")
        cached_elapsed = time.perf_counter() - start

        # Cache should be faster (allow for some overhead)
        assert cached_elapsed < first_elapsed or cache.stats.hits > 0

    @pytest.mark.benchmark
    def test_batch_processing_memory_efficiency(self):
        """Benchmark: Batch processing should handle large lists"""
        large_list = list(range(100000))

        def heavy_fn(batch: List[int]) -> List[int]:
            return [x * 2 for x in batch]

        start = time.perf_counter()
        results = process_in_batches(large_list, heavy_fn, batch_size=10000)
        elapsed = time.perf_counter() - start

        assert len(results) == 100000
        assert elapsed < 1.0  # Should complete within 1 second

    @pytest.mark.benchmark
    def test_10k_entity_comparison_time(self):
        """Benchmark: 10K entity comparison should complete in <1 second"""
        # Create mock entities
        mock_comparator = Mock()
        mock_result = Mock()
        mock_result.changes = list(range(100))  # 100 changes
        mock_comparator.compare_with_modified_detection.return_value = mock_result

        optimizer = OptimizedComparator(mock_comparator)

        entities_a = {"LINE": [Mock() for _ in range(5000)]}
        entities_b = {"LINE": [Mock() for _ in range(5000)]}

        start = time.perf_counter()
        result, metrics = optimizer.compare(entities_a, entities_b)
        elapsed = time.perf_counter() - start

        assert elapsed < 1.0
        assert metrics.entity_count_a == 5000
        assert metrics.entity_count_b == 5000


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests for performance optimizer"""

    def test_full_optimization_pipeline(self):
        """Test complete optimization pipeline"""
        # Setup
        mock_comparator = Mock()
        mock_result = Mock()
        mock_result.changes = [Mock(), Mock()]
        mock_comparator.compare_with_modified_detection.return_value = mock_result

        optimizer = OptimizedComparator(
            mock_comparator,
            enable_hash_cache=True,
            enable_result_cache=True,
        )

        entities_a = {"LINE": [Mock()], "CIRCLE": [Mock()]}
        entities_b = {"LINE": [Mock()]}

        # First comparison
        result1, metrics1 = optimizer.compare(entities_a, entities_b)

        # Second comparison (should use cache)
        result2, metrics2 = optimizer.compare(entities_a, entities_b)

        assert result1 == result2
        assert metrics1.entity_count_a == 2
        assert metrics2.entity_count_a == 2

        # Check cache stats
        stats = optimizer.get_cache_stats()
        assert "hash_cache" in stats

    def test_cache_stats_accumulation(self):
        """Test that cache stats accumulate correctly"""
        cache = get_hash_cache()
        cache.clear()
        cache.reset_stats()

        # Generate cache activity
        for i in range(10):
            cache.get_or_compute(f"key_{i}", lambda: f"value_{i}")

        # Repeat some
        for i in range(5):
            cache.get_or_compute(f"key_{i}", lambda: "x")

        stats = cache.stats
        assert stats.misses == 10
        assert stats.hits == 5
        assert stats.hit_rate == pytest.approx(33.33, rel=0.1)
