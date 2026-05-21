# -*- coding: utf-8 -*-
"""Manifest SHA-256 provenance chain (Plan §17 Phase F6 / GPT Pro F6).

Adds content-addressed integrity for customer-grade evidence manifests so
that intentional or accidental post-generation manual edits are detectable
by ``audit_drawing_compare_mvp_exit.py``.

The provenance block carries:

* ``manifest_sha256`` — deterministic SHA-256 over the manifest JSON
  with the ``provenance`` field excluded (avoids the chicken-and-egg
  problem of hashing a field that contains its own hash).
* ``generated_at_utc`` — ISO 8601 UTC timestamp.
* ``generated_by`` — generator script identifier.
* ``tool_version`` — short git SHA or fallback.
* ``input_file_hashes`` — ``{role_id: sha256}`` of evidence inputs.
  Keys MUST be role identifiers (e.g. ``review_ground_truth_csv``,
  ``operator_notes_file``), NEVER real filenames — Plan §19 A-1
  (Agent T finding T1) — to prevent customer project metadata
  (e.g. ``2026_SECRET_ACQUISITION.dwg``) leaking into the provenance
  block consumed by external auditors. ``verify_manifest_integrity``
  flags filename-shaped keys at audit time.
* ``template_detection`` — result of the existing template/handoff
  anti-bypass marker scan (string label).

The verifier recomputes ``manifest_sha256`` from the current manifest
content and rejects any mutation that changes a non-``provenance`` key.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

__all__ = [
    "DEFAULT_EXCLUDED_KEYS",
    "FILENAME_LIKE_EXTENSIONS",
    "TEMPLATE_DETECTION_CLEAN",
    "TEMPLATE_DETECTION_FOUND",
    "build_provenance",
    "compute_file_sha256",
    "compute_manifest_hash",
    "verify_manifest_integrity",
]


# Plan §19 A-1 (Agent T finding T1) — keys in ``input_file_hashes`` MUST
# be role identifiers (e.g. ``review_ground_truth_csv``,
# ``operator_notes_file``, ``confirmed_export_artifact``), NOT real
# filenames. The original docstring mistakenly said ``{filename: sha256}``
# which would leak customer project names (e.g.
# ``2026_SECRET_ACQUISITION.dwg``) into the provenance JSON consumed by
# external auditors. Production prepare script already uses role names
# (prepare_drawing_compare_customer_evidence.py:442/449/456), but a
# future maintainer extending the schema could accidentally pass
# real filenames. The heuristic below rejects keys that look like
# filenames at verify time so the gap can't widen silently.
FILENAME_LIKE_EXTENSIONS: tuple[str, ...] = (
    ".dwg", ".dxf", ".pdf", ".csv", ".md", ".png", ".jpg", ".jpeg",
    ".tiff", ".tif", ".xlsx", ".xls", ".docx", ".doc", ".json",
    ".jsonl", ".zip", ".7z", ".tar", ".gz",
)


def _looks_like_filename(key: str) -> bool:
    """Heuristic: True if ``key`` looks like a real filename rather than
    a role identifier. Used by ``verify_manifest_integrity`` to flag
    accidental project-name leakage in ``input_file_hashes``.
    """
    lowered = key.lower()
    return any(lowered.endswith(ext) for ext in FILENAME_LIKE_EXTENSIONS)


DEFAULT_EXCLUDED_KEYS: tuple[str, ...] = ("provenance",)

TEMPLATE_DETECTION_CLEAN = "no_template_markers_found"
TEMPLATE_DETECTION_FOUND = "template_markers_found"

_GENERATED_BY_DEFAULT = "prepare_drawing_compare_customer_evidence.py"
_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _reject_non_finite_floats(payload: Any, path: str = "$") -> None:
    """Plan §19 A-5 (Agent A finding A2) — manifest must not contain
    NaN/Inf floats. ``json.dumps(default=str)`` would silently coerce
    them to invalid JSON strings (``"NaN"``, ``"Infinity"``) that
    cannot round-trip through standard parsers, breaking
    ``verify_manifest_integrity`` downstream. This walker raises
    ValueError before serialisation if any non-finite value is found.

    The walker recurses dicts and lists; for any other shape, it
    only checks float instances (bool is a subclass of int, not
    float, so it's safe).
    """
    import math

    if isinstance(payload, float):
        if not math.isfinite(payload):
            raise ValueError(
                f"non-finite float at {path}: {payload!r} — manifest may not "
                "contain NaN/Inf; clip or normalise upstream before hashing"
            )
        return
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            _reject_non_finite_floats(value, f"{path}.{key}")
        return
    if isinstance(payload, (list, tuple)):
        for index, item in enumerate(payload):
            _reject_non_finite_floats(item, f"{path}[{index}]")


def _canonical_dumps(payload: Any) -> str:
    """Deterministic JSON serialisation used by the hash.

    ``sort_keys=True`` removes key-order variability, ``ensure_ascii=True``
    keeps the byte stream stable across platforms with different default
    encodings, and the compact ``separators`` argument avoids whitespace
    drift.

    Plan §19 A-5 (Agent A A2) — explicit rejection of NaN/Inf floats
    BEFORE serialisation. The previous ``default=str`` silently converted
    them to invalid JSON strings; the walker above raises ValueError
    instead so the caller can clamp or normalise upstream.
    """

    _reject_non_finite_floats(payload)
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    )


def compute_manifest_hash(
    manifest: Mapping[str, Any],
    *,
    exclude_keys: Iterable[str] = DEFAULT_EXCLUDED_KEYS,
) -> str:
    """SHA-256 over the manifest JSON, EXCLUDING the provenance field.

    The exclusion list defaults to ``("provenance",)`` so that the hash
    can be embedded back inside the very block it describes without
    introducing recursion.
    """

    excluded = set(exclude_keys)
    filtered = {key: value for key, value in manifest.items() if key not in excluded}
    payload = _canonical_dumps(filtered).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compute_file_sha256(path: Path) -> str:
    """SHA-256 of a file on disk (streaming, 1 MiB chunks)."""

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_provenance(
    manifest: Mapping[str, Any],
    *,
    input_file_hashes: Mapping[str, str],
    tool_version: str,
    generator_script: str = _GENERATED_BY_DEFAULT,
    template_detection: str = TEMPLATE_DETECTION_CLEAN,
    exclude_keys: Iterable[str] = DEFAULT_EXCLUDED_KEYS,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the provenance block to attach as ``manifest["provenance"]``.

    The caller is responsible for assigning the returned dict back onto
    the manifest. This function does NOT mutate ``manifest``.
    """

    if generated_at_utc is None:
        # Plan §18 B-2 (Agent F production-scale follow-up) — second-
        # precision timestamps collided when two operators happened to
        # generate manifests in the same wall second. Switch to
        # microsecond precision (the highest the stdlib gives portably)
        # so the ``generated_at_utc`` field on its own remains a useful
        # tie-breaker for audit-trail ordering. The hash already covers
        # tampering; this just removes the false-equivalence window.
        generated_at_utc = (
            datetime.now(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
    return {
        "schema_version": 1,
        "manifest_sha256": compute_manifest_hash(manifest, exclude_keys=exclude_keys),
        "generated_at_utc": generated_at_utc,
        "generated_by": generator_script,
        "tool_version": str(tool_version or "unknown"),
        "input_file_hashes": dict(input_file_hashes),
        "template_detection": template_detection,
    }


def verify_manifest_integrity(
    manifest: Mapping[str, Any],
    *,
    exclude_keys: Iterable[str] = DEFAULT_EXCLUDED_KEYS,
) -> list[str]:
    """Return a list of integrity violations (empty list = intact).

    The audit script converts each violation into a failure reason for
    ``customer_grade_evidence_declared``. Manifests created before this
    code shipped will simply lack a ``provenance`` block and trip the
    first check, which is the desired backward-incompatibility behaviour
    for ``--evidence-level customer_grade`` exit audits.
    """

    violations: list[str] = []
    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict):
        violations.append(
            "provenance block is missing — manifest was either generated by an "
            "older script version or had its provenance manually stripped"
        )
        return violations

    declared_hash = str(provenance.get("manifest_sha256") or "").strip().lower()
    if not declared_hash:
        violations.append("provenance.manifest_sha256 is missing or empty")
    elif not _SHA256_HEX_PATTERN.match(declared_hash):
        violations.append("provenance.manifest_sha256 is not a 64-char hex digest")
    else:
        recomputed = compute_manifest_hash(manifest, exclude_keys=exclude_keys)
        if recomputed != declared_hash:
            violations.append(
                "provenance.manifest_sha256 mismatch — manifest was modified after "
                f"generation (expected={declared_hash}, recomputed={recomputed})"
            )

    generated_at = str(provenance.get("generated_at_utc") or "").strip()
    if not generated_at:
        violations.append("provenance.generated_at_utc is missing")
    else:
        try:
            # ISO 8601 with trailing Z or +00:00.
            datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            violations.append(
                f"provenance.generated_at_utc is not a parseable ISO 8601 timestamp: {generated_at!r}"
            )

    tool_version = str(provenance.get("tool_version") or "").strip()
    if not tool_version:
        violations.append("provenance.tool_version is missing or empty")

    input_file_hashes = provenance.get("input_file_hashes")
    if not isinstance(input_file_hashes, dict) or not input_file_hashes:
        violations.append("provenance.input_file_hashes must be a non-empty dict")
    else:
        for name, value in input_file_hashes.items():
            hexdigest = str(value or "").strip().lower()
            if not _SHA256_HEX_PATTERN.match(hexdigest):
                violations.append(
                    f"provenance.input_file_hashes[{name!r}] is not a 64-char hex digest"
                )
            # Plan §19 A-1 (Agent T T1) — reject filename-shaped keys.
            # The schema requires role identifiers, not real filenames,
            # so customer project names cannot leak through provenance.
            if _looks_like_filename(str(name)):
                violations.append(
                    "provenance.input_file_hashes contains a filename-shaped key "
                    f"({name!r}); keys must be role identifiers (e.g. "
                    "'review_ground_truth_csv') to prevent customer project "
                    "metadata leakage"
                )

    return violations
