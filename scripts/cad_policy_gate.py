"""Static policy gate for the standalone drawing compare project.

This check is intentionally narrower than a full legal audit.  It catches the
regressions that would invalidate the ODA-free/customer-build direction:

* ODA, LibreDWG, GPL, AGPL, PyMuPDF/MuPDF, or fitz in runtime requirements.
* ODA executable or LibreDWG/GPL CAD reader references outside quarantined files.
* Missing clean-room approval contract for planned AC1024/AC1032 DWG decoding.
* Customer-facing text that implies full DWG or AC1024/AC1032 native support.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]

RUNTIME_REQUIREMENT_FILES = (
    "requirements.txt",
    "requirements-unified.txt",
    "requirements-core.txt",
)
FORBIDDEN_REQUIREMENT_TOKENS = (
    "ODAFileConverter",
    "ODA_CONVERTER",
    "ODA_FILE_CONVERTER",
    "opendesign.com",
    "PyMuPDF",
    "pymupdf",
    "fitz",
    "MuPDF",
    "mupdf",
    "LibreDWG",
    "libredwg",
    "dwgread",
    "dwg_api",
    "GPL",
    "AGPL",
)
PRODUCT_SCAN_ROOTS = ("src",)
POLICY_WORDING_SCAN_FILES = (
    "src/services/comparison/dwg_differ.py",
    "src/services/comparison/dxf_renderer.py",
    "scripts/release_environment_check.py",
    "scripts/release_drawing_compare_workbench.py",
    ".github/workflows/cad-format-regression.yml",
)
SUPPORT_CLAIM_SCAN_FILES = (
    "README.md",
    "docs/CAD_FORMAT_SUPPORT_POLICY.md",
    "docs/CAD_MILESTONE_VERIFICATION_REPORT.md",
    "docs/DWG_CLEANROOM_FORMAT_CONTRACT.md",
    "docs/DWG_NATIVE_READER_EXTENSION_SPEC.md",
    "docs/ENTITY_SUPPORT_MATRIX.md",
    "docs/error-code-spec.md",
    "scripts/release_drawing_compare_workbench.py",
    "scripts/release_environment_check.py",
)
DWG_CLEANROOM_CONTRACT_DOC = "docs/DWG_CLEANROOM_FORMAT_CONTRACT.md"
DWG_CLEANROOM_CONTRACT_SOURCE = "src/services/comparison/dwg_cleanroom_contract.py"
DWG_CLEANROOM_CONTRACT_ID = "DWG-CLEANROOM-SECTION-MAP-CONTRACT-v1"
QUARANTINED_CODE_FILES = {
    "src/services/comparison/dwg_converter.py",
    "src/services/comparison/dwg_differ.py",
    "src/services/comparison/import_pipeline.py",
}
FORBIDDEN_CODE_PATTERNS = (
    (
        "CAD_POLICY_ODA_REFERENCE",
        re.compile(r"\b(?:ODAFileConverter|DwgConverter|ODAConverterNotFoundError)\b"),
        "ODA fallback references must stay quarantined.",
    ),
    (
        "CAD_POLICY_LIBREDWG_REFERENCE",
        re.compile(r"\b(?:LibreDWG|libredwg|dwgread|dwg_api)\b", re.IGNORECASE),
        "GPL/LibreDWG CAD reader references are not allowed in product code.",
    ),
)
FORBIDDEN_WORDING_PATTERNS = (
    (
        "CAD_POLICY_ODA_REQUIRED_WORDING",
        re.compile(r"\brequires\s+ODA\b|\brequires\s+ODA\s+File\s+Converter\b", re.IGNORECASE),
        "Customer-facing code must not say ODA is required.",
    ),
    (
        "CAD_POLICY_ODA_MISSING_WORDING",
        re.compile(r"\bODA\s+Converter\s*:\s*MISSING\b", re.IGNORECASE),
        "Release output must not report ODA Converter as a required missing dependency.",
    ),
    (
        "CAD_POLICY_DEFAULT_PYMUPDF_AUTO",
        re.compile(r"fallback_chain\s*=\s*\[[^\]]*['\"]pymupdf['\"]", re.IGNORECASE),
        "Default DXF auto rendering must not include PyMuPDF/MuPDF.",
    ),
)
FORBIDDEN_SUPPORT_CLAIM_PATTERNS = (
    (
        "CAD_POLICY_DWG_FULLY_SUPPORTED_WORDING",
        re.compile(r"\bDWG\s+fully\s+supported\b", re.IGNORECASE),
        "Customer-facing text must not claim DWG is fully supported.",
    ),
    (
        "CAD_POLICY_AC1024_SUPPORTED_WORDING",
        re.compile(
            r"\bAC1024\b[^\n]{0,120}\b(?:native\s+)?(?:DWG\s+)?"
            r"(?:supported|support\s+available)\b",
            re.IGNORECASE,
        ),
        "Customer-facing text must not claim AC1024 native DWG support.",
    ),
    (
        "CAD_POLICY_AC1032_SUPPORTED_WORDING",
        re.compile(
            r"\bAC1032\b[^\n]{0,120}\b(?:native\s+)?(?:DWG\s+)?"
            r"(?:supported|support\s+available)\b",
            re.IGNORECASE,
        ),
        "Customer-facing text must not claim AC1032 native DWG support.",
    ),
    (
        "CAD_POLICY_DWG_ODA_REQUIRED_WORDING",
        re.compile(r"\bODA\s+required\b", re.IGNORECASE),
        "Customer-facing text must not say ODA is required.",
    ),
)


@dataclass(frozen=True)
class PolicyViolation:
    path: str
    line: int
    code: str
    message: str
    snippet: str = ""

    def format(self) -> str:
        location = f"{self.path}:{self.line}" if self.line else self.path
        suffix = f" :: {self.snippet.strip()}" if self.snippet.strip() else ""
        return f"{location}: {self.code}: {self.message}{suffix}"


def scan_repo(root: Path = ROOT) -> list[PolicyViolation]:
    root = root.resolve()
    violations: list[PolicyViolation] = []
    violations.extend(check_runtime_requirements(root))
    violations.extend(check_product_code(root))
    violations.extend(check_policy_wording(root))
    violations.extend(check_support_claim_wording(root))
    violations.extend(check_dwg_cleanroom_contract(root))
    violations.extend(check_ci_gate(root))
    return sorted(violations, key=lambda item: (item.path, item.line, item.code))


def check_runtime_requirements(root: Path) -> list[PolicyViolation]:
    violations: list[PolicyViolation] = []
    lowered_tokens = tuple(token.lower() for token in FORBIDDEN_REQUIREMENT_TOKENS)
    for rel in RUNTIME_REQUIREMENT_FILES:
        path = root / rel
        if not path.exists():
            continue
        for line_number, line in enumerate(_read_lines(path), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            lowered = stripped.lower()
            for token, lowered_token in zip(FORBIDDEN_REQUIREMENT_TOKENS, lowered_tokens):
                if lowered_token in lowered:
                    violations.append(
                        PolicyViolation(
                            rel,
                            line_number,
                            "CAD_POLICY_FORBIDDEN_RUNTIME_REQUIREMENT",
                            f"{token} must not be a default/runtime requirement.",
                            stripped,
                        )
                    )
    return violations


def check_product_code(root: Path) -> list[PolicyViolation]:
    violations: list[PolicyViolation] = []
    for scan_root in PRODUCT_SCAN_ROOTS:
        base = root / scan_root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            rel = _rel(path, root)
            if rel in QUARANTINED_CODE_FILES:
                continue
            for line_number, line in enumerate(_read_lines(path), start=1):
                for code, pattern, message in FORBIDDEN_CODE_PATTERNS:
                    if pattern.search(line):
                        violations.append(PolicyViolation(rel, line_number, code, message, line))
    return violations


def check_policy_wording(root: Path) -> list[PolicyViolation]:
    violations: list[PolicyViolation] = []
    for rel in POLICY_WORDING_SCAN_FILES:
        path = root / rel
        if not path.exists():
            continue
        for line_number, line in enumerate(_read_lines(path), start=1):
            for code, pattern, message in FORBIDDEN_WORDING_PATTERNS:
                if pattern.search(line):
                    violations.append(PolicyViolation(rel, line_number, code, message, line))

    release_check = root / "scripts/release_environment_check.py"
    if release_check.exists():
        text = release_check.read_text(encoding="utf-8", errors="ignore")
        runtime_block = text.split("runtime_modules = {", 1)
        if len(runtime_block) > 1:
            body = runtime_block[1].split("}", 1)[0]
            if '"fitz"' in body or "'fitz'" in body:
                violations.append(
                    PolicyViolation(
                        "scripts/release_environment_check.py",
                        0,
                        "CAD_POLICY_PYMUPDF_REQUIRED_RUNTIME",
                        "PyMuPDF/fitz must be reported as optional/licensed, not required runtime.",
                    )
                )
    return violations


def check_support_claim_wording(root: Path) -> list[PolicyViolation]:
    violations: list[PolicyViolation] = []
    for rel in SUPPORT_CLAIM_SCAN_FILES:
        path = root / rel
        if not path.exists():
            continue
        for line_number, line in enumerate(_read_lines(path), start=1):
            for code, pattern, message in FORBIDDEN_SUPPORT_CLAIM_PATTERNS:
                if pattern.search(line):
                    violations.append(PolicyViolation(rel, line_number, code, message, line))
    return violations


def check_dwg_cleanroom_contract(root: Path) -> list[PolicyViolation]:
    violations: list[PolicyViolation] = []
    doc = root / DWG_CLEANROOM_CONTRACT_DOC
    source = root / DWG_CLEANROOM_CONTRACT_SOURCE
    if not doc.exists():
        return [
            PolicyViolation(
                DWG_CLEANROOM_CONTRACT_DOC,
                0,
                "CAD_POLICY_DWG_CLEANROOM_CONTRACT_MISSING",
                "Planned AC1024/AC1032 decoding must have a clean-room approval contract.",
            )
        ]
    if not source.exists():
        violations.append(
            PolicyViolation(
                DWG_CLEANROOM_CONTRACT_SOURCE,
                0,
                "CAD_POLICY_DWG_CLEANROOM_SOURCE_MISSING",
                "Runtime DWG diagnostics must expose the clean-room contract gate.",
            )
        )
        source_text = ""
    else:
        source_text = source.read_text(encoding="utf-8", errors="ignore")

    doc_text = doc.read_text(encoding="utf-8", errors="ignore")
    required_doc_markers = {
        "CAD_POLICY_DWG_CONTRACT_ID": DWG_CLEANROOM_CONTRACT_ID,
        "CAD_POLICY_DWG_CONTRACT_ODA_BAN": "ODA File Converter",
        "CAD_POLICY_DWG_CONTRACT_GPL_BAN": "GPL or AGPL",
        "CAD_POLICY_DWG_CONTRACT_BLOCKER": "approved_format_contract_required",
        "CAD_POLICY_DWG_CONTRACT_AC1024": "AC1024",
        "CAD_POLICY_DWG_CONTRACT_AC1032": "AC1032",
        "CAD_POLICY_DWG_CONTRACT_EVIDENCE_TABLE": "Evidence Packet",
        "CAD_POLICY_DWG_CONTRACT_REFERENCE_TITLE": "Reference title",
        "CAD_POLICY_DWG_CONTRACT_SOURCE_PATH": "Source URL/path",
        "CAD_POLICY_DWG_CONTRACT_ALLOWED_USE": "Allowed use",
        "CAD_POLICY_DWG_CONTRACT_APPROVAL_STATUS": "Approval status",
        "CAD_POLICY_DWG_CONTRACT_PENDING_ROWS": "pending",
        "CAD_POLICY_DWG_CONTRACT_BLOCKED_ROWS": "blocked",
    }
    for code, marker in required_doc_markers.items():
        if marker not in doc_text:
            violations.append(
                PolicyViolation(
                    DWG_CLEANROOM_CONTRACT_DOC,
                    0,
                    code,
                    f"Clean-room contract must contain `{marker}`.",
                )
            )

    required_source_markers = {
        "CAD_POLICY_DWG_SOURCE_CONTRACT_ID": DWG_CLEANROOM_CONTRACT_ID,
        "CAD_POLICY_DWG_SOURCE_BLOCKED": 'approval_status="blocked"',
        "CAD_POLICY_DWG_SOURCE_NOT_APPROVED": "approved_reference_available=False",
        "CAD_POLICY_DWG_SOURCE_AC1024": '"AC1024"',
        "CAD_POLICY_DWG_SOURCE_AC1032": '"AC1032"',
    }
    compact_source = re.sub(r"\s+", "", source_text)
    for code, marker in required_source_markers.items():
        haystack = compact_source if " " not in marker else source_text
        needle = marker.replace(" ", "") if " " not in marker else marker
        if needle not in haystack:
            violations.append(
                PolicyViolation(
                    DWG_CLEANROOM_CONTRACT_SOURCE,
                    0,
                    code,
                    f"Clean-room source contract must contain `{marker}`.",
                )
            )
    return violations


def check_ci_gate(root: Path) -> list[PolicyViolation]:
    workflow = root / ".github/workflows/cad-format-regression.yml"
    if not workflow.exists():
        return []
    text = workflow.read_text(encoding="utf-8", errors="ignore")
    required_snippets = {
        "CAD_POLICY_CI_DIFF_CHECK": "git diff --check",
        "CAD_POLICY_CI_POLICY_GATE": "python scripts\\cad_policy_gate.py",
    }
    violations = []
    for code, snippet in required_snippets.items():
        if snippet not in text:
            violations.append(
                PolicyViolation(
                    ".github/workflows/cad-format-regression.yml",
                    0,
                    code,
                    f"CI workflow must run `{snippet}`.",
                )
            )
    return violations


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()


def _rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    violations = scan_repo(args.root)
    if violations:
        print("CAD policy gate failed:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation.format()}", file=sys.stderr)
        return 1
    print("CAD policy gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
