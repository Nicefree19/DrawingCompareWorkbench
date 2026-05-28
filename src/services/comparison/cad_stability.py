"""Shared stability limits for CAD import and compare pipelines."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


CancelCallback = Callable[[], bool]


class CadLimitCode:
    IMPORT_TIMEOUT = "CAD_IMPORT_TIMEOUT"
    IMPORT_CANCELLED = "CAD_IMPORT_CANCELLED"
    ENTITY_LIMIT_EXCEEDED = "CAD_ENTITY_LIMIT_EXCEEDED"
    TOKEN_LIMIT_EXCEEDED = "CAD_TOKEN_LIMIT_EXCEEDED"
    BLOCK_RECURSION_LIMIT = "CAD_BLOCK_RECURSION_LIMIT"


@dataclass(frozen=True)
class CadStabilityLimits:
    """Runtime guardrails for large or malformed CAD inputs."""

    import_timeout_seconds: Optional[float] = 30.0
    max_entities: int = 100_000
    max_dxf_tokens: int = 2_500_000
    max_block_depth: int = 4
    max_spatial_cells_per_entity: int = 4096

    def to_dict(self) -> Dict[str, Any]:
        return {
            "import_timeout_seconds": self.import_timeout_seconds,
            "max_entities": self.max_entities,
            "max_dxf_tokens": self.max_dxf_tokens,
            "max_block_depth": self.max_block_depth,
            "max_spatial_cells_per_entity": self.max_spatial_cells_per_entity,
        }

    @classmethod
    def disabled(cls) -> "CadStabilityLimits":
        return cls(
            import_timeout_seconds=None,
            max_entities=0,
            max_dxf_tokens=0,
            max_block_depth=64,
            max_spatial_cells_per_entity=0,
        )
