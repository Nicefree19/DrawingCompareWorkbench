# -*- coding: utf-8 -*-
"""
Comparison Module
=================

엑셀 및 도면 비교 기능을 제공하는 모듈입니다.

Author: TEKLA_MCP Team
Date: 2025-12-14
"""

from .base import BaseComparator, ComparisonResult, ChangeRecord, ChangeType
from .excel_differ import ExcelDiffer
from .drawing_differ import DrawingDiffer

# DXF Comparison Engine
from .dxf_comparator import (
    DxfChangeType,
    DxfChange,
    LayerStatistics,
    DxfComparisonResult,
    DxfComparator,
)

# Phase 3+ Priority Scoring System
from .priority_score import (
    PriorityLevel,
    ReviewReason,
    ConfidenceFactors,
    PriorityScore,
    create_critical_score,
    create_high_score,
    create_medium_score,
)
from .priority_calculator import (
    LayerProfile,
    DEFAULT_LAYER_PROFILES,
    PriorityCalculator,
    get_default_calculator,
    calculate_priority,
)

# QW-2: Color Toggle System
from .visualization_service import ColorConfig

# QW-1: Sensitivity Preset System
from .comparison_config import (
    SensitivityPreset,
    SensitivityConfig,
    ComparisonConfig,
)

# QW-4: Project Config Save/Load
from .project_config import (
    ProjectMetadata,
    ProjectConfig,
    RecentProject,
    RecentProjectsManager,
    get_recent_projects_manager,
    save_project_config,
    load_project_config,
)

# QW-NEW: Top N Filter System
from .top_n_filter import (
    FilterMode,
    TopNFilterConfig,
    FilterStatistics,
    FilterResult,
    TopNFilter,
    filter_top_n,
    filter_critical_changes,
    filter_review_needed,
    filter_structural_changes,
    apply_project_filter,
)

# Performance Optimization System
from .performance_optimizer import (
    CacheStats,
    HashCache,
    ComparisonCache,
    ParallelExtractionResult,
    PerformanceMetrics,
    PerformanceTracker,
    OptimizedComparator,
    cached_hash,
    compute_file_hash,
    extract_entities_parallel,
    process_in_batches,
    estimate_memory_usage,
    should_use_streaming,
    get_hash_cache,
    get_comparison_cache,
)

# Drawing batch orchestration
from .drawing_batch import (
    BatchCompareJob,
    BatchCompareOptions,
    BatchCompareSummary,
    DescriptorBuildOptions,
    DrawingFileDescriptor,
    DrawingKind,
    FilenameIdentity,
    MatchAlternative,
    MatchCandidate,
    MatchingOptions,
    MatchStatus,
    apply_manual_matches,
    are_compatible,
    build_drawing_descriptor,
    compare_candidate,
    compare_pdf_documents,
    load_manual_match_csv,
    load_compare_state,
    match_drawing_sets,
    parse_filename_identity,
    quality_gate_visible_statuses,
    scan_drawing_inputs,
    score_match,
    write_compare_state,
    write_manual_match_csv,
)

from .change_zones import (
    ChangeArtifactPackage,
    CloudMarkOptions,
    CloudMarkRegion,
    ChangeZoneOptions,
    DrawingChangeZone,
    ExecutiveReviewOptions,
    ExecutiveReviewPackage,
    MarkedArtifact,
    build_change_zones,
    change_record_bbox,
    export_change_artifacts,
    export_executive_review_from_artifacts,
)

from .review_project import (
    PreviewArtifact,
    PreviewPackage,
    ReviewStateRecord,
    ZoneOverlay,
    apply_review_state,
    collect_review_zones,
    export_preview_artifacts,
    load_review_state,
    save_review_state,
    write_review_project,
)
from .review_dashboard import (
    ReviewDashboardOptions,
    ReviewDashboardPackage,
    export_review_dashboard,
)
from .viewer_package import (
    ViewerPackage,
    ViewerPackageOptions,
    export_viewer_package,
)
from .folder_compare_pipeline import (
    FolderComparePipeline,
    FolderCompareRunRequest,
    FolderCompareRunResult,
)

__all__ = [
    # Base comparison
    "BaseComparator",
    "ComparisonResult",
    "ChangeRecord",
    "ChangeType",
    "ExcelDiffer",
    "DrawingDiffer",
    # DXF Comparison Engine
    "DxfChangeType",
    "DxfChange",
    "LayerStatistics",
    "DxfComparisonResult",
    "DxfComparator",
    # Priority Scoring System
    "PriorityLevel",
    "ReviewReason",
    "ConfidenceFactors",
    "PriorityScore",
    "create_critical_score",
    "create_high_score",
    "create_medium_score",
    "LayerProfile",
    "DEFAULT_LAYER_PROFILES",
    "PriorityCalculator",
    "get_default_calculator",
    "calculate_priority",
    # QW-2: Color Toggle
    "ColorConfig",
    # QW-1: Sensitivity Preset
    "SensitivityPreset",
    "SensitivityConfig",
    "ComparisonConfig",
    # QW-4: Project Config Save/Load
    "ProjectMetadata",
    "ProjectConfig",
    "RecentProject",
    "RecentProjectsManager",
    "get_recent_projects_manager",
    "save_project_config",
    "load_project_config",
    # QW-NEW: Top N Filter
    "FilterMode",
    "TopNFilterConfig",
    "FilterStatistics",
    "FilterResult",
    "TopNFilter",
    "filter_top_n",
    "filter_critical_changes",
    "filter_review_needed",
    "filter_structural_changes",
    "apply_project_filter",
    # Performance Optimization System
    "CacheStats",
    "HashCache",
    "ComparisonCache",
    "ParallelExtractionResult",
    "PerformanceMetrics",
    "PerformanceTracker",
    "OptimizedComparator",
    "cached_hash",
    "compute_file_hash",
    "extract_entities_parallel",
    "process_in_batches",
    "estimate_memory_usage",
    "should_use_streaming",
    "get_hash_cache",
    "get_comparison_cache",
    # Drawing batch orchestration
    "BatchCompareJob",
    "BatchCompareOptions",
    "BatchCompareSummary",
    "DescriptorBuildOptions",
    "DrawingFileDescriptor",
    "DrawingKind",
    "FilenameIdentity",
    "MatchAlternative",
    "MatchCandidate",
    "MatchingOptions",
    "MatchStatus",
    "apply_manual_matches",
    "are_compatible",
    "build_drawing_descriptor",
    "compare_candidate",
    "compare_pdf_documents",
    "load_compare_state",
    "load_manual_match_csv",
    "match_drawing_sets",
    "parse_filename_identity",
    "quality_gate_visible_statuses",
    "scan_drawing_inputs",
    "score_match",
    "write_compare_state",
    "write_manual_match_csv",
    "ChangeArtifactPackage",
    "CloudMarkOptions",
    "CloudMarkRegion",
    "ChangeZoneOptions",
    "DrawingChangeZone",
    "ExecutiveReviewOptions",
    "ExecutiveReviewPackage",
    "MarkedArtifact",
    "build_change_zones",
    "change_record_bbox",
    "export_change_artifacts",
    "export_executive_review_from_artifacts",
    "PreviewArtifact",
    "PreviewPackage",
    "ReviewStateRecord",
    "ZoneOverlay",
    "apply_review_state",
    "collect_review_zones",
    "export_preview_artifacts",
    "load_review_state",
    "save_review_state",
    "write_review_project",
    "ReviewDashboardOptions",
    "ReviewDashboardPackage",
    "export_review_dashboard",
    "ViewerPackage",
    "ViewerPackageOptions",
    "export_viewer_package",
    "FolderComparePipeline",
    "FolderCompareRunRequest",
    "FolderCompareRunResult",
]
