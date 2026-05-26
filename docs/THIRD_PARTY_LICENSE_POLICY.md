# Third-Party License Policy

Last updated: 2026-05-22 KST

## Purpose

This policy defines which third-party components may be embedded in the Drawing
Compare Workbench after removing ODA File Converter. It applies to runtime
dependencies, packaged binaries, optional plugins, CAD/PDF conversion tools,
test fixtures, fonts, and generated customer installers.

This document is an engineering policy and is not legal advice. Items marked
`법무 검토 필요` require legal approval before release or customer delivery.

## 요약

- 제품 내장 기본 허용 라이선스는 MIT, BSD 계열, ISC, Zlib, PSF, Apache-2.0 등 permissive 라이선스다.
- LGPL/MPL/EPL/CDDL 및 상용 SDK는 조건부 허용이며 법무 검토가 필요하다.
- GPL/AGPL/SSPL, non-commercial-only, no-redistribution, research-only, evaluation-only 라이선스는 고객 빌드 내장 금지다.
- PyMuPDF/MuPDF는 AGPL 또는 상용 라이선스이므로, 독점 고객 배포에는 상용 라이선스 확보 전까지 금지한다.
- PySide6/Qt는 LGPLv3/GPLv3 또는 상용 배포 조건을 확인해야 하므로 법무 검토 필요 항목이다.
- ODA File Converter와 ODA SDK는 고객 빌드 번들링, 자동 호출, 필수 런타임 의존을 금지한다.
- LibreDWG는 GPLv3-or-later이므로 독점 제품 내장 DWG 리더로 사용할 수 없다.

## Product Embedding Rule

A third-party component may be embedded only when all of the following are true:

1. The package has a clear SPDX license identifier or a reviewed custom license.
2. The license permits commercial distribution in the intended product model.
3. The license does not require relicensing the proprietary product as GPL,
   AGPL, or an incompatible copyleft license.
4. Required copyright notices, license texts, and NOTICE files can be shipped.
5. Transitive dependencies have been reviewed with the same criteria.
6. Any binary redistribution rights are confirmed, including wheels, DLLs,
   native libraries, fonts, and model/data files.
7. The component is recorded in the SBOM/release manifest before packaging.

## License Categories

| Category | Licenses/examples | Product policy |
| --- | --- | --- |
| 허용 | MIT, BSD-2-Clause, BSD-3-Clause, ISC, Zlib, PSF, Apache-2.0 | May be embedded when notices and license texts are included. Apache-2.0 patent/NOTICE obligations must be preserved. |
| 조건부 허용 | LGPL-2.1, LGPL-3.0, MPL-2.0, EPL-2.0, CDDL | 법무 검토 필요. Confirm dynamic linking, replacement/relink rights, file-level copyleft duties, and source-offer obligations. |
| 상용 라이선스 필요 | PyMuPDF/MuPDF commercial, Qt commercial, ODA SDK/member license, other proprietary CAD SDKs | 법무 검토 필요. Store signed license evidence and redistribution scope before packaging. |
| 금지 | GPL-2.0, GPL-3.0, AGPL-3.0, SSPL, Commons Clause, non-commercial-only, no-redistribution, research-only, evaluation-only | Do not embed, link, bundle, or require at runtime in customer builds unless legal approves a complete product licensing change or commercial alternative. |

## Current Dependency Policy

| Component | Current use | License posture | Product policy |
| --- | --- | --- | --- |
| `ezdxf` / `ezdxf[draw]` | DXF parsing, entity access, DXF drawing add-on | MIT per upstream docs | 허용. Keep use limited to DXF processing. Do not enable an ODA-dependent DWG add-on in product builds. |
| `numpy`, `pandas`, `scipy` | Data processing and geometry/numeric work | Generally permissive/BSD-style, verify exact versions in SBOM | 허용 after SBOM verification. |
| `matplotlib` | DXF render fallback | Permissive, verify exact wheel license | 허용 after SBOM verification. |
| `OpenCV` | Image/PDF visual workflows | Apache-2.0 for OpenCV 4.5+ | 허용 with Apache-2.0 notice handling. |
| `Pillow` | Image handling | HPND-style permissive | 허용 with license text. |
| `rtree` / `libspatialindex` | Spatial indexing | Verify Python wrapper and native library licenses | 법무 검토 필요 until both Python and native library redistribution are listed in SBOM. |
| `PySide6` / Qt for Python | Desktop GUI | Community edition is LGPLv3/GPLv3; commercial edition available | 법무 검토 필요. Commercial Qt packages are preferred for closed-source customer distribution. Avoid GPL-only Qt modules. |
| `PyMuPDF` / MuPDF | PDF rendering/extraction and optional DXF render backend | AGPL or commercial | 금지 for proprietary embedded distribution unless a commercial license is secured and recorded. 법무 검토 필요. |
| `ODA File Converter` | Legacy DWG to DXF conversion path | ODA free download has non-commercial limitation for non-members; ODA says it does not distribute ODA File Converter as a general redistributable product right | 금지 in customer builds after ODA removal. Remove bundling and automatic invocation. |
| `LibreDWG` | Not currently used | GPLv3-or-later | 금지 for proprietary embedded product use. |
| Internal `DwgNativeAc1015Adapter` | AC1015 read-only MVP for simple 2D DWG import | Internal first-party code, no third-party DWG dependency | 조건부 허용. Keep scope limited to documented AC1015 preview until legal/product approval allows support claims. |
| AI/LLM SDKs (`openai`, `anthropic`, etc.) | Optional AI features | Vendor terms, not just OSS license | 법무 검토 필요 for redistribution, data handling, and customer enablement. |

## GPL/AGPL Prohibitions

The following are prohibited in proprietary customer builds unless legal
explicitly approves a different product licensing model:

- Importing, linking, dynamically linking, statically linking, bundling, or
  vendoring GPL/AGPL libraries.
- Adding GPL/AGPL code snippets, generated code, headers, schemas, or examples
  into product source.
- Making GPL/AGPL tools mandatory runtime prerequisites for packaged features.
- Shipping GPL/AGPL binaries next to the application as helper tools when the
  feature is designed to invoke them.
- Using AGPL services or modified AGPL server code without reviewing network
  source-disclosure obligations.
- Adding transitive dependencies that pull GPL/AGPL libraries into the runtime
  environment.

Specific CAD/PDF examples:

- `LibreDWG` is GPLv3-or-later and must not be embedded as the DWG reader.
- `PyMuPDF`/MuPDF under AGPL must not be embedded in a proprietary distribution
  unless a commercial license is obtained.
- GPL CAD renderers/converters must not be used as hidden helper executables.

## ODA Prohibitions

For ODA removal, the product must satisfy all of the following:

- Do not bundle `ODAFileConverter.exe`, ODA DLLs, ODA SDK binaries, ODA headers,
  ODA examples, or ODA-generated redistributables.
- Do not download ODA tools automatically during install or first run.
- Do not prompt users into a workflow that makes ODA a required commercial-use
  dependency unless legal has approved that exact flow.
- Do not claim native DWG support based on ODA conversion.
- Do not keep customer-build code paths that silently invoke ODA when present on
  the machine.
- Do not copy ODA documentation, examples, or SDK-derived code into this
  repository.

Allowed transitional behavior for internal engineering only:

- Existing ODA wrapper code may remain in source temporarily while removal is in
  progress, but customer builds must disable it.
- Internal validation may compare old ODA-generated DXF fixtures against the new
  native DXF pipeline only when the fixture provenance permits internal use.
- Any ODA usage in CI, release scripts, installer scripts, or documentation must
  be removed before customer release.

## Intake Checklist

Before adding or upgrading any third-party dependency:

1. Record package name, version, source URL, checksum, and SPDX identifier.
2. Capture direct and transitive license data in the release manifest/SBOM.
3. Add full license text and NOTICE material to the packaged attribution bundle.
4. Confirm whether the package ships native binaries or links to native system libraries.
5. Confirm whether optional extras add new licenses. Example: drawing backends,
   OCR extras, CAD converters, or AI plugins.
6. Confirm whether the package license changes by distribution channel
   (`pip`, vendor account, source build, commercial wheel).
7. Mark legal review outcome before enabling the dependency in customer builds.

## 법무 검토 필요

The following release blockers must be resolved before shipping:

| Item | Required legal decision |
| --- | --- |
| Direct DWG support claim | Approve whether AC1015 native-reader preview may be advertised, whether broader DWG support stays DXF-only/external-conversion, or whether a commercial DWG SDK is needed. |
| PyMuPDF in customer builds | Approve AGPL compliance strategy or acquire commercial license. |
| PySide6/Qt customer distribution | Approve LGPLv3 compliance package or commercial Qt license. |
| `rtree` native dependency | Confirm native binary redistribution terms. |
| Any ODA-related residual code path | Confirm complete removal or approve licensed ODA use. |
| Any GPL/AGPL transitive dependency | Remove or approve full product relicensing/commercial alternative. |
| Customer sample drawings and fixtures | Confirm redistribution/internal-use rights before including in tests, demos, or releases. |

## Release Gate

A customer build is not releasable unless:

- SBOM exists for runtime dependencies and native binaries.
- License texts and notices are packaged.
- No GPL/AGPL/ODA runtime path is enabled without legal approval.
- Direct DWG support is not advertised unless the approved DWG path exists.
- The installer does not include ODA, GPL, AGPL, no-commercial, or no-redistribution components.
- `CAD_FORMAT_SUPPORT_POLICY.md` and `ENTITY_SUPPORT_MATRIX.md` match the shipped behavior.

## References

- SPDX License List: https://spdx.org/licenses/
- OSI MIT License: https://opensource.org/license/mit
- Apache License 2.0: https://www.apache.org/licenses/LICENSE-2.0.html
- GNU GPL FAQ on static/dynamic linking: https://www.gnu.org/licenses/gpl-faq.en.html
- GNU AGPLv3: https://www.gnu.org/licenses/agpl-3.0.en.html
- PyMuPDF license information: https://pymupdf.io/
- MuPDF license information: https://mupdf.readthedocs.io/en/1.26.4/license.html
- Qt licensing: https://doc.qt.io/qt-6/licensing.html
- Qt for Python commercial distribution: https://doc.qt.io/qtforpython-6.5/commercial/index.html
- ODA File Converter FAQ: https://www.opendesign.com/faq/question/what-are-oda-viewer-and-oda-file-converter
- GNU LibreDWG licensing: https://www.gnu.org/software/libredwg/manual/html_node/Overview.html
