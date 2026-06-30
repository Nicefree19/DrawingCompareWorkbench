"""Clean-room approval contract for native DWG reader expansion."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

CLEANROOM_CONTRACT_ID = "DWG-CLEANROOM-SECTION-MAP-CONTRACT-v1"
BLOCKING_STAGE_DETAIL = "approved_format_contract_required"


@dataclass(frozen=True)
class DwgCleanRoomFormatContract:
    """Approval gate for a versioned DWG binary decoder.

    The contract is intentionally metadata-only.  It does not describe offsets,
    byte layouts, or algorithms.  Those details must only be added after the
    evidence requirements are satisfied and reviewed.
    """

    version_code: str
    family: str
    contract_id: str
    approval_status: str
    blocking_stage_detail: str
    decoder_provenance: str
    approved_reference_available: bool
    required_approval_evidence: Tuple[str, ...]
    next_safe_step: str


_REQUIRED_APPROVAL_EVIDENCE = (
    "approved public-reference citation with license/provenance notes",
    "legal or product-owner approval recorded in docs",
    "clean-room implementation notes written without incompatible source code",
    "diagnostic-only tests before any entity decoding support claim",
)

_VERSIONED_SECTION_MAP_CONTRACTS = {
    "AC1024": DwgCleanRoomFormatContract(
        version_code="AC1024",
        family="AutoCAD 2010/2011/2012",
        contract_id=CLEANROOM_CONTRACT_ID,
        approval_status="blocked",
        blocking_stage_detail=BLOCKING_STAGE_DETAIL,
        decoder_provenance="internal/public-approved-only",
        approved_reference_available=False,
        required_approval_evidence=_REQUIRED_APPROVAL_EVIDENCE,
        next_safe_step=("record an approved AC1024 section-map format contract before decoding"),
    ),
    "AC1027": DwgCleanRoomFormatContract(
        version_code="AC1027",
        family="AutoCAD 2013/2014/2015/2016/2017",
        contract_id=CLEANROOM_CONTRACT_ID,
        approval_status="blocked",
        blocking_stage_detail=BLOCKING_STAGE_DETAIL,
        decoder_provenance="internal/public-approved-only",
        approved_reference_available=False,
        required_approval_evidence=_REQUIRED_APPROVAL_EVIDENCE,
        next_safe_step=("record an approved AC1027 section-map format contract before decoding"),
    ),
    "AC1032": DwgCleanRoomFormatContract(
        version_code="AC1032",
        family="AutoCAD 2018+",
        contract_id=CLEANROOM_CONTRACT_ID,
        approval_status="blocked",
        blocking_stage_detail=BLOCKING_STAGE_DETAIL,
        decoder_provenance="internal/public-approved-only",
        approved_reference_available=False,
        required_approval_evidence=_REQUIRED_APPROVAL_EVIDENCE,
        next_safe_step=("record an approved AC1032 section-map format contract before decoding"),
    ),
}


def contract_for_version(version_code: str) -> Optional[DwgCleanRoomFormatContract]:
    """Return the clean-room contract gate for a planned DWG version."""

    return _VERSIONED_SECTION_MAP_CONTRACTS.get(version_code)


def versioned_section_map_contracts() -> Tuple[DwgCleanRoomFormatContract, ...]:
    """Return all current versioned section-map contract gates."""

    return tuple(_VERSIONED_SECTION_MAP_CONTRACTS.values())
