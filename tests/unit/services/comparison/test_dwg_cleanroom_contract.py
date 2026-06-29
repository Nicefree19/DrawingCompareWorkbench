from __future__ import annotations

from src.services.comparison.dwg_cleanroom_contract import (
    CLEANROOM_CONTRACT_ID,
    BLOCKING_STAGE_DETAIL,
    contract_for_version,
    versioned_section_map_contracts,
)


def test_versioned_section_map_contracts_block_unapproved_versions() -> None:
    contracts = {contract.version_code: contract for contract in versioned_section_map_contracts()}

    # AC1027 (R2013) joins the gated set when the native reader back-expands to it
    # — but it must stay BLOCKED, exactly like AC1024/AC1032 (adding a blocked
    # entry is the contract REQUIRING approval, not granting it).
    assert set(contracts) == {"AC1024", "AC1027", "AC1032"}
    for contract in contracts.values():
        assert contract.contract_id == CLEANROOM_CONTRACT_ID
        assert contract.approval_status == "blocked"
        assert contract.blocking_stage_detail == BLOCKING_STAGE_DETAIL
        assert contract.decoder_provenance == "internal/public-approved-only"
        assert contract.approved_reference_available is False
        assert "approved public-reference citation with license/provenance notes" in (
            contract.required_approval_evidence
        )


def test_ac1027_cleanroom_contract_is_present_and_blocked() -> None:
    # The native reader now decodes AC1027 (R2013), so it has a clean-room contract
    # gate — which is BLOCKED and not approved (no support claim). The family label
    # spans the AutoCAD 2013-2017 releases that all use the AC1027 code.
    contract = contract_for_version("AC1027")
    assert contract is not None
    assert contract.version_code == "AC1027"
    assert contract.family == "AutoCAD 2013/2014/2015/2016/2017"
    assert contract.approval_status == "blocked"
    assert contract.approved_reference_available is False


def test_unknown_version_has_no_cleanroom_section_map_contract() -> None:
    assert contract_for_version("AC1099") is None
