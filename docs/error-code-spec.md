# CAD Pipeline Error Code Spec

## Scope

This spec covers the ODA-free CAD compare path:

`ImportPipeline -> CanonicalDrawing -> DrawingNormalizer -> DrawingCompareEngine`

The API and UI must read error and partial status from `ImportPipelineResult` and `ComparePipelineResult`, not from legacy ODA conversion exceptions.

## Status Values

| Status | Meaning | UI behavior |
| --- | --- | --- |
| `ok` | Import or compare completed without warnings that affect coverage. | Show normal success state. |
| `partial` | Import or compare completed, but one or more entities were skipped, approximated, or otherwise warning-producing. | Show result with an incomplete-coverage badge and warning details. |
| `failed` | Import or compare cannot produce a usable result. | Block diff rendering and show the error code/message. |

## Import Error Codes

| Code | Owner | Meaning | UI message policy |
| --- | --- | --- | --- |
| `CAD_UNSUPPORTED_FORMAT` | ImportPipeline | File extension is not `.dxf` or `.dwg`. | Tell the user to choose DXF/DWG. |
| `CAD_READ_FAILED` | ImportPipeline | File cannot be read from disk. | Show file path and OS error. |
| `DXF_PARSE_ERROR` | DxfTokenizer/DxfImporter | ASCII DXF group code/value stream is malformed. | Show parse failure and ask for a valid ASCII DXF export. |
| `CAD_IMPORT_TIMEOUT` | ImportPipeline/importer | Import exceeded configured wall-clock limit. | Show timeout and suggest lowering scope or increasing limit. |
| `CAD_IMPORT_CANCELLED` | ImportPipeline/importer | User or caller cancelled import. | Return to idle state without treating it as a data error. |
| `CAD_ENTITY_LIMIT_EXCEEDED` | ImportPipeline/importer | Canonical entity count exceeded configured stability limit. | Show limit and suggest using a narrower drawing/sheet or approved higher limit. |
| `CAD_TOKEN_LIMIT_EXCEEDED` | DxfTokenizer | DXF group-code token count exceeded configured stability limit. | Show file-too-large/malformed warning. |
| `DWG_IMPORT_FAILED` | ImportPipeline | DWG failed without a more specific adapter code. | Show generic DWG import failure. |
| `DWG_CORRUPTED` | DwgVersionDetector/DwgImporter | DWG header or adapter payload is invalid. | Show "corrupted or invalid DWG". |
| `DWG_ENCRYPTED` | DwgImporterAdapter | DWG is encrypted and cannot be read. | Show "encrypted DWG is unsupported". |
| `DWG_UNSUPPORTED_VERSION` | DwgVersionDetector | DWG version is outside first-phase support. | Show detected version and supported version list. |
| `DWG_ADAPTER_UNAVAILABLE` | DwgImporterAdapter | Approved DWG adapter is missing or cannot read this file. | Show adapter unavailable; do not mention ODA as required. |
| `DWG_ADAPTER_FAILED` | DwgImporterAdapter | Approved DWG adapter failed while reading. | Show adapter failure and diagnostics. |
| `DWG_FORBIDDEN_LICENSE` | DwgImporter | Adapter license is not allowed for product embedding. | Show internal configuration error; legal review required. |
| `DWG_NO_READABLE_ENTITIES` | DwgImporter | Adapter succeeded but returned no readable drawing entities. | Show empty/unsupported DWG result. |
| `DWG_IMPORT_TIMEOUT` | DwgImporter | DWG adapter/import mapping exceeded configured wall-clock limit. | Show timeout and preserve side-specific diagnostics. |
| `DWG_IMPORT_CANCELLED` | DwgImporter | Caller cancelled DWG import. | Return to idle state without crash. |
| `DWG_ENTITY_LIMIT_EXCEEDED` | DwgImporter | DWG adapter returned more entities than configured limit. | Show limit and classify as failed import. |
| `ODA_FALLBACK_DISABLED` | ImportPipeline | Legacy ODA fallback was not enabled. | Internal state only; default product builds must keep this disabled. |
| `ODA_FALLBACK_FAILED` | ImportPipeline | Optional legacy fallback was explicitly enabled but failed. | Show fallback failure only in approved internal builds. |

## Compare Error Codes

| Code | Meaning | UI behavior |
| --- | --- | --- |
| `COMPARE_IMPORT_FAILED` | At least one side failed import. | Show side-specific import status and do not render diff. |
| `COMPARE_FAILED` | Both imports succeeded but comparison engine failed. | Show compare failure and keep import diagnostics visible. |
| `COMPARE_UNSUPPORTED_FORMAT_PAIR` | Caller required same format family and inputs differ. | Ask user to compare same family or disable strict format matching. |

## Partial Import Contract

Partial import is not a fatal error. The UI must still display the diff if `ComparePipelineResult.status == "partial"` and `diff` is present.

Required UI fields:

- `status`
- `partial_imports`
- `imports.a.status`, `imports.b.status`
- `imports.*.warnings`
- `imports.*.import_report.unsupported_entities`
- `imports.*.entity_count`, `imports.*.layer_count`, `imports.*.bbox`

Recommended badge text:

- Korean: `부분 가져오기 - 일부 객체가 비교에서 제외되었습니다.`
- English: `Partial import - some entities were excluded from comparison.`

## ODA Policy

Product builds must not automatically invoke ODA. `ImportPipelineOptions.allow_oda_fallback` defaults to `False`; the fallback path is isolated behind that option and imports `DwgConverter` only inside the fallback method.

`DwgDiffer.compare()` uses the canonical pipeline by default. The legacy ezdxf/ODA path is retained only for compatibility tests or approved internal workflows through `config={"use_canonical_pipeline": False}` plus an explicitly configured converter.

Legal review required:

- Enabling ODA fallback in any customer build
- Adding any proprietary DWG SDK adapter
- Adding GPL/AGPL/no-commercial CAD reader/converter dependencies
- Shipping or downloading converter binaries during install or first run
