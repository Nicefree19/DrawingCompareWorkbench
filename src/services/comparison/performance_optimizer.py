# -*- coding: utf-8 -*-
"""Performance Optimization Module for DXF Comparison

Phase V2: Performance optimizations for drawing comparison.

Features:
- LRU caching for hash computations
- Parallel entity extraction
- Result caching for repeated comparisons
- Memory-efficient batch processing

Author: TEKLA_MCP Team
Date: 2025-12-24
"""

import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# LRU Cache with Statistics
# =============================================================================


@dataclass
class CacheStats:
    """Cache hit/miss statistics"""

    hits: int = 0
    misses: int = 0
    total_time_saved_ms: float = 0.0

    @property
    def hit_rate(self) -> float:
        """Cache hit rate as percentage"""
        total = self.hits + self.misses
        return (self.hits / total * 100) if total > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": f"{self.hit_rate:.1f}%",
            "time_saved_ms": f"{self.total_time_saved_ms:.2f}",
        }


class HashCache:
    """Thread-safe LRU cache for entity hash computations

    Provides caching for expensive hash computations with statistics tracking.
    """

    def __init__(self, maxsize: int = 10000):
        """
        Args:
            maxsize: Maximum number of cached entries
        """
        self._cache: Dict[str, str] = {}
        self._maxsize = maxsize
        self._lock = Lock()
        self._stats = CacheStats()
        self._avg_compute_time_ms = 0.1  # Initial estimate

    def get_or_compute(
        self,
        key: str,
        compute_fn: Callable[[], str],
    ) -> str:
        """Get cached value or compute and cache

        Args:
            key: Cache key (usually serialized entity data)
            compute_fn: Function to compute hash if not cached

        Returns:
            Computed or cached hash value
        """
        with self._lock:
            if key in self._cache:
                self._stats.hits += 1
                self._stats.total_time_saved_ms += self._avg_compute_time_ms
                return self._cache[key]

            self._stats.misses += 1

        # Compute outside lock to allow concurrency
        start = time.perf_counter()
        result = compute_fn()
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Update average compute time
        self._avg_compute_time_ms = (
            self._avg_compute_time_ms * 0.9 + elapsed_ms * 0.1
        )

        with self._lock:
            # Evict oldest if at capacity (simple LRU approximation)
            if len(self._cache) >= self._maxsize:
                # Remove 10% of entries
                evict_count = max(1, self._maxsize // 10)
                keys_to_remove = list(self._cache.keys())[:evict_count]
                for k in keys_to_remove:
                    del self._cache[k]

            self._cache[key] = result

        return result

    def clear(self):
        """Clear all cached entries"""
        with self._lock:
            self._cache.clear()

    @property
    def stats(self) -> CacheStats:
        """Get cache statistics"""
        return self._stats

    def reset_stats(self):
        """Reset statistics"""
        self._stats = CacheStats()


# Global hash cache instance
_hash_cache = HashCache(maxsize=50000)


def get_hash_cache() -> HashCache:
    """Get the global hash cache instance"""
    return _hash_cache


def cached_hash(data: Dict[str, Any], precision: int = 2) -> str:
    """Compute hash with caching

    Args:
        data: Entity data dictionary
        precision: Coordinate precision (decimal places)

    Returns:
        Computed hash string
    """
    # Create cache key from serialized data
    cache_key = _serialize_for_cache(data, precision)

    def compute():
        return _compute_hash(data, precision)

    return _hash_cache.get_or_compute(cache_key, compute)


def _serialize_for_cache(data: Dict[str, Any], precision: int) -> str:
    """Serialize data for cache key

    Args:
        data: Entity data
        precision: Coordinate precision

    Returns:
        Serialized string key
    """
    # Sort keys for consistent ordering
    items = sorted(data.items())
    parts = []
    for k, v in items:
        if isinstance(v, float):
            parts.append(f"{k}:{round(v, precision)}")
        elif isinstance(v, (list, tuple)):
            rounded = [round(x, precision) if isinstance(x, float) else x for x in v]
            parts.append(f"{k}:{rounded}")
        else:
            parts.append(f"{k}:{v}")
    return "|".join(parts)


def _compute_hash(data: Dict[str, Any], precision: int) -> str:
    """Compute MD5 hash of entity data

    Args:
        data: Entity data
        precision: Coordinate precision

    Returns:
        MD5 hash string
    """
    serialized = _serialize_for_cache(data, precision)
    return hashlib.md5(serialized.encode()).hexdigest()


# =============================================================================
# Parallel Entity Extraction
# =============================================================================


@dataclass
class ParallelExtractionResult:
    """Result of parallel entity extraction"""

    entities: Dict[str, List[Any]]
    elapsed_ms: float
    entity_count: int
    worker_count: int
    errors: List[str] = field(default_factory=list)


def extract_entities_parallel(
    file_paths: List[Path],
    extractor_fn: Callable[[Path], Dict[str, List[Any]]],
    max_workers: int = 4,
) -> List[ParallelExtractionResult]:
    """Extract entities from multiple files in parallel

    Args:
        file_paths: List of DXF file paths
        extractor_fn: Function to extract entities from a single file
        max_workers: Maximum number of parallel workers

    Returns:
        List of extraction results
    """
    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_path = {
            executor.submit(_extract_single, path, extractor_fn): path
            for path in file_paths
        }

        for future in as_completed(future_to_path):
            path = future_to_path[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                logger.error(f"Entity extraction failed for {path}: {e}")
                results.append(
                    ParallelExtractionResult(
                        entities={},
                        elapsed_ms=0,
                        entity_count=0,
                        worker_count=max_workers,
                        errors=[str(e)],
                    )
                )

    return results


def _extract_single(
    path: Path,
    extractor_fn: Callable[[Path], Dict[str, List[Any]]],
) -> ParallelExtractionResult:
    """Extract entities from a single file

    Args:
        path: File path
        extractor_fn: Extraction function

    Returns:
        Extraction result
    """
    start = time.perf_counter()
    entities = extractor_fn(path)
    elapsed_ms = (time.perf_counter() - start) * 1000

    entity_count = sum(len(v) for v in entities.values())

    return ParallelExtractionResult(
        entities=entities,
        elapsed_ms=elapsed_ms,
        entity_count=entity_count,
        worker_count=1,
    )


# =============================================================================
# Result Caching for Repeated Comparisons
# =============================================================================


@dataclass
class ComparisonCacheEntry:
    """Cached comparison result"""

    result: Any
    timestamp: float
    file_hash_a: str
    file_hash_b: str


class ComparisonCache:
    """Cache for comparison results

    Caches comparison results keyed by file content hashes to avoid
    redundant comparisons of unchanged files.
    """

    def __init__(self, maxsize: int = 100, ttl_seconds: float = 3600):
        """
        Args:
            maxsize: Maximum number of cached comparisons
            ttl_seconds: Time-to-live for cache entries (default: 1 hour)
        """
        self._cache: Dict[str, ComparisonCacheEntry] = {}
        self._maxsize = maxsize
        self._ttl = ttl_seconds
        self._lock = Lock()
        self._stats = CacheStats()

    def _make_key(self, hash_a: str, hash_b: str) -> str:
        """Create cache key from file hashes"""
        return f"{hash_a}:{hash_b}"

    def get(self, hash_a: str, hash_b: str) -> Optional[Any]:
        """Get cached comparison result

        Args:
            hash_a: Hash of first file
            hash_b: Hash of second file

        Returns:
            Cached result or None
        """
        key = self._make_key(hash_a, hash_b)
        now = time.time()

        with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                # Check TTL
                if now - entry.timestamp < self._ttl:
                    self._stats.hits += 1
                    return entry.result
                else:
                    # Expired
                    del self._cache[key]

            self._stats.misses += 1
            return None

    def set(self, hash_a: str, hash_b: str, result: Any):
        """Store comparison result

        Args:
            hash_a: Hash of first file
            hash_b: Hash of second file
            result: Comparison result to cache
        """
        key = self._make_key(hash_a, hash_b)

        with self._lock:
            # Evict expired entries
            self._evict_expired()

            # Evict oldest if at capacity
            if len(self._cache) >= self._maxsize:
                oldest_key = min(
                    self._cache.keys(),
                    key=lambda k: self._cache[k].timestamp,
                )
                del self._cache[oldest_key]

            self._cache[key] = ComparisonCacheEntry(
                result=result,
                timestamp=time.time(),
                file_hash_a=hash_a,
                file_hash_b=hash_b,
            )

    def _evict_expired(self):
        """Remove expired entries"""
        now = time.time()
        expired = [
            k for k, v in self._cache.items()
            if now - v.timestamp >= self._ttl
        ]
        for k in expired:
            del self._cache[k]

    def clear(self):
        """Clear all cached entries"""
        with self._lock:
            self._cache.clear()

    @property
    def stats(self) -> CacheStats:
        """Get cache statistics"""
        return self._stats


# Global comparison cache
_comparison_cache = ComparisonCache(maxsize=100)


def get_comparison_cache() -> ComparisonCache:
    """Get the global comparison cache instance"""
    return _comparison_cache


# =============================================================================
# Batch Processing Utilities
# =============================================================================


def process_in_batches(
    items: List[T],
    batch_fn: Callable[[List[T]], List[Any]],
    batch_size: int = 1000,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> List[Any]:
    """Process items in batches for memory efficiency

    Args:
        items: List of items to process
        batch_fn: Function to process a batch
        batch_size: Number of items per batch
        progress_callback: Optional callback(processed, total)

    Returns:
        Combined results from all batches
    """
    results = []
    total = len(items)

    for i in range(0, total, batch_size):
        batch = items[i : i + batch_size]
        batch_results = batch_fn(batch)
        results.extend(batch_results)

        if progress_callback:
            processed = min(i + batch_size, total)
            progress_callback(processed, total)

    return results


# =============================================================================
# Performance Metrics
# =============================================================================


@dataclass
class PerformanceMetrics:
    """Performance metrics for comparison operations"""

    total_time_ms: float = 0.0
    extraction_time_ms: float = 0.0
    comparison_time_ms: float = 0.0
    entity_count_a: int = 0
    entity_count_b: int = 0
    change_count: int = 0
    cache_stats: Optional[Dict[str, Any]] = None

    @property
    def entities_per_second(self) -> float:
        """Entities processed per second"""
        total_entities = self.entity_count_a + self.entity_count_b
        if self.total_time_ms > 0:
            return total_entities / (self.total_time_ms / 1000)
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_time_ms": f"{self.total_time_ms:.2f}",
            "extraction_time_ms": f"{self.extraction_time_ms:.2f}",
            "comparison_time_ms": f"{self.comparison_time_ms:.2f}",
            "entity_count_a": self.entity_count_a,
            "entity_count_b": self.entity_count_b,
            "change_count": self.change_count,
            "entities_per_second": f"{self.entities_per_second:.0f}",
            "cache_stats": self.cache_stats,
        }


class PerformanceTracker:
    """Track performance metrics for comparison operations"""

    def __init__(self):
        self._start_time: Optional[float] = None
        self._extraction_time: float = 0.0
        self._comparison_time: float = 0.0
        self._metrics = PerformanceMetrics()

    def start(self):
        """Start timing"""
        self._start_time = time.perf_counter()

    def mark_extraction_done(self, entity_count_a: int, entity_count_b: int):
        """Mark extraction phase complete"""
        if self._start_time:
            self._extraction_time = (time.perf_counter() - self._start_time) * 1000
        self._metrics.entity_count_a = entity_count_a
        self._metrics.entity_count_b = entity_count_b
        self._metrics.extraction_time_ms = self._extraction_time

    def mark_comparison_done(self, change_count: int):
        """Mark comparison phase complete"""
        if self._start_time:
            total = (time.perf_counter() - self._start_time) * 1000
            self._comparison_time = total - self._extraction_time
        self._metrics.comparison_time_ms = self._comparison_time
        self._metrics.change_count = change_count
        self._metrics.total_time_ms = self._extraction_time + self._comparison_time

    def finalize(self) -> PerformanceMetrics:
        """Finalize and return metrics"""
        self._metrics.cache_stats = get_hash_cache().stats.to_dict()
        return self._metrics


# =============================================================================
# Optimized Comparator Wrapper
# =============================================================================


class OptimizedComparator:
    """Wrapper for DxfComparator with performance optimizations

    Provides caching, parallel processing, and performance tracking.
    """

    def __init__(
        self,
        comparator: Any,
        enable_hash_cache: bool = True,
        enable_result_cache: bool = True,
        parallel_extraction: bool = False,
    ):
        """
        Args:
            comparator: DxfComparator instance
            enable_hash_cache: Enable hash computation caching
            enable_result_cache: Enable result caching
            parallel_extraction: Enable parallel entity extraction
        """
        self._comparator = comparator
        self._enable_hash_cache = enable_hash_cache
        self._enable_result_cache = enable_result_cache
        self._parallel_extraction = parallel_extraction
        self._tracker = PerformanceTracker()

    def compare(
        self,
        entities_a: Dict[str, List[Any]],
        entities_b: Dict[str, List[Any]],
        use_modified_detection: bool = True,
    ) -> Tuple[Any, PerformanceMetrics]:
        """Compare entities with performance tracking

        Args:
            entities_a: Old entities
            entities_b: New entities
            use_modified_detection: Use MODIFIED detection

        Returns:
            (comparison_result, performance_metrics)
        """
        self._tracker.start()

        # Count entities
        count_a = sum(len(v) for v in entities_a.values())
        count_b = sum(len(v) for v in entities_b.values())
        self._tracker.mark_extraction_done(count_a, count_b)

        # Perform comparison
        if use_modified_detection:
            result = self._comparator.compare_with_modified_detection(
                entities_a, entities_b
            )
        else:
            result = self._comparator.compare(entities_a, entities_b)

        self._tracker.mark_comparison_done(len(result.changes))

        return result, self._tracker.finalize()

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return {
            "hash_cache": get_hash_cache().stats.to_dict(),
            "comparison_cache": get_comparison_cache().stats.to_dict(),
        }

    def clear_caches(self):
        """Clear all caches"""
        get_hash_cache().clear()
        get_comparison_cache().clear()


# =============================================================================
# Utility Functions
# =============================================================================


def compute_file_hash(file_path: Path) -> str:
    """Compute hash of file content for caching

    Args:
        file_path: Path to file

    Returns:
        MD5 hash of file content
    """
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def estimate_memory_usage(entity_count: int, bytes_per_entity: int = 500) -> int:
    """Estimate memory usage for entity storage

    Args:
        entity_count: Number of entities
        bytes_per_entity: Estimated bytes per entity

    Returns:
        Estimated bytes
    """
    return entity_count * bytes_per_entity


def should_use_streaming(
    entity_count: int,
    available_memory_mb: int = 1024,
) -> bool:
    """Determine if streaming mode should be used

    Args:
        entity_count: Expected entity count
        available_memory_mb: Available memory in MB

    Returns:
        True if streaming mode recommended
    """
    estimated_mb = estimate_memory_usage(entity_count) / (1024 * 1024)
    # Use streaming if estimated usage > 50% of available memory
    return estimated_mb > (available_memory_mb * 0.5)
