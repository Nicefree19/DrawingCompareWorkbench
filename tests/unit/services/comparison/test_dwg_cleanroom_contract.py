from __future__ import annotations

from src.services.comparison.dwg_cleanroom_contract import (
    CLEANROOM_CONTRACT_ID,
    BLOCKING_STAGE_DETAIL,
    contract_for_version,
    versioned_section_map_contracts,
)


def test_versioned_section_map_contracts_block_unapproved_versions() -> None:
    contracts = {contract.version_code: contract for contract in versioned_section_map_contracts()}

    assert set(contracts) == {"AC1024", "AC1032"}
    for contract in contracts.values():
        assert contract.contract_id == CLEANROOM_CONTRACT_ID
        assert contract.approval_status == "blocked"
        assert contract.blocking_stage_detail == BLOCKING_STAGE_DETAIL
        assert contract.decoder_provenance == "internal/public-approved-only"
        assert contract.approved_reference_available is False
        assert "approved public-reference citation with license/provenance notes" in (
            contract.required_approval_evidence
        )


def test_unknown_version_has_no_cleanroom_section_map_contract() -> None:
    assert contract_for_version("AC1027") is None
