# Drawing Compare 고객급 증거 요청서

작성일: 2026-05-13 KST
Work item: WI-20260510-001
목적: Drawing Compare Workbench 고객 배포 MVP 최종 10/10 감사 통과

## 현재 상태

코드, Windows 패키지, 대형 DWG 성능 개선, PDF/DWG/DXF 회귀 테스트,
선택구역 뷰어, confirmed-only export, path leakage 감사는 통과했습니다.

현재 감사 점수는 `25/26 = 9.6/10`입니다.

남은 1개 실패 항목은 기능 버그가 아니라 고객급 증거 부재입니다.

실패 항목:

`customer_grade_evidence_declared: --customer-evidence-manifest is required for customer_grade evidence`

최신 고객 공유 패키지:

`tmp\drawing_compare_release_mvp_packaged_fix_large_dwg_request_ko_ascii_json_probe_filter_precise\DrawingCompareWorkbench_customer_shareable.zip`

최신 synthetic 감사:

`tmp\drawing_compare_mvp_exit_audit_large_dwg_request_ko_ascii_json_probe_filter_precise.json`

최신 customer-grade 게이트 probe:

`tmp\drawing_compare_mvp_exit_audit_customer_grade_gate_request_ko_ascii_json_probe_filter_precise.json`

Windows JSON check:

`customer_shareable_package\cli\*.py` outputs are ASCII-safe UTF-8 JSON and
were verified with PowerShell `Get-Content -Raw | ConvertFrom-Json`.

## 필요한 산출물 5개

### 1. 승인된 `review_ground_truth.csv`

구조검토 책임자 또는 지정 검토자가 실제 20~50장 고객급 검증 세트에서
중요 구조 변경이 review queue에 잡혔는지 확인한 CSV입니다.

필수 컬럼:

```csv
drawing_label,category,summary_contains,source_format,detection_source,bbox_status,notes
```

필수 조건:

- 템플릿/example/sample 행이 남아 있으면 안 됩니다.
- `ground_truth.status=approved`로 manifest를 만들 수 있어야 합니다.
- 구조 핵심 변경을 포함해야 합니다:
  - 부재 추가/삭제/이동
  - 단면/치수 변경
  - 배근/철근 간격 변경
  - 그리드 변경
  - `D13@100 -> D13@200`
  - `SHD13@100 -> SHD13@200`
  - 기타 구조적으로 의미 있는 텍스트 변경

### 2. 승인된 `review_decision_truth.csv`

review queue 항목에 대해 실제 검토자가 `true_positive`, `false_positive`, `hold`를 라벨링한 CSV입니다.

필수 컬럼:

```csv
pair_uuid,zone_id,drawing_label,structural_bucket,human_label,source_format,detection_source,bbox_status,notes
```

필수 조건:

- 라벨 행 20개 이상
- overall precision 0.85 이상
- 구조 bucket별 precision 0.75 이상
- false-positive rate 0.15 이하
- 필수 구조 bucket별 라벨 행 2개 이상

### 3. 승인된 `dataset_strata.csv`

20~50장 고객급 세트가 쉬운 도면만 고른 것이 아니라는 분포 증거입니다.

필수 컬럼:

```csv
pair_uuid,drawing_label,format_pair,sheet_type,risk_class,large_dwg,block_text_case,negative_control,notes
```

필수 조건:

- 행 수가 manifest `sheet_count`와 동일
- CAD rows 8 이상, PDF-PDF rows 8 이상
- raster/low-quality risk rows 2 이상
- large-DWG rows 2 이상
- block-text rows 2 이상
- `plan`, `section`, `detail`, `schedule_like` 각각 2 이상
- negative/control rows 2 이상

### 4. 대형 DWG resource/cancel probe

기존 elapsed/stream proof에 더해 다음 필드가 필요합니다.

- `peak_rss_mb <= 4096`
- `progress_max_gap_s <= 10`
- `cancel_probe.status == passed`
- `cancel_probe.cancel_to_idle_s <= 10`
- `cancel_probe.partial_outputs_cleaned == true`
- `cancel_probe.worker_processes_left == 0`

### 5. 구조검토 책임자 dry-run notes

파일명 예:

`operator_dry_run_notes.md`

필수 조건:

- `reviewer_role: structural_review_lead` 또는 승인된 구조검토 팀장/책임자 역할을 명시해야 합니다.
- 아래 workflow ID가 모두 실제로 확인되어야 합니다.
- 체크리스트만 있으면 부족하며, 실제 도면/zone 관찰 내용이 포함되어야 합니다.

필수 workflow ID:

```text
input_selection
automatic_compare_completed
top_structural_review_queue_seen
selected_zone_before_after_sync_zoom
korean_reason_summary_reviewed
confirmed_false_positive_hold_used
confirmed_only_export_checked
sharable_path_leakage_checked
```

권장 notes 내용:

- 어떤 도면 세트를 사용했는지
- Top 3~5 구조 핵심 변경이 첫 화면에서 확인됐는지
- 선택한 변경구역이 Before/After에서 같은 기준으로 확대됐는지
- 한글 요약과 검토 이유가 실제 판단에 충분했는지
- `confirmed`, `false_positive`, `hold` 판정을 사용했는지
- confirmed 항목만 구름마크/리포트로 export됐는지
- 공유 산출물에서 절대경로/cache/state/temp 경로 누출이 없었는지

## 증거 배치 후 실행 순서

1. 증거 파일을 customer evidence 폴더에 둡니다.
2. inventory를 실행해 `status=ready_for_manifest`인지 확인합니다.
   이때 현재 대형 DWG probe
   `tmp\dwg_s20_dwg_differ_after_fix.json`를 `--large-dwg-probe`로
   포함합니다.
3. `prepare_drawing_compare_customer_evidence.py`로
   `customer_evidence_manifest.json`을 생성합니다.
4. `audit_drawing_compare_mvp_exit.py --evidence-level customer_grade`를 실행합니다.
   최종 감사에도 같은 probe와 `--require-large-dwg-probe`를 포함합니다.
5. 최종 audit JSON이 `status=passed`, failed check 0건이면 10/10 완료입니다.

정확한 명령은 다음 문서에 고정되어 있습니다:

`docs/collab/DRAWING_COMPARE_CUSTOMER_GRADE_CLOSEOUT_RUNBOOK.md`

## 완료 판정

다음 7개가 모두 통과하기 전에는 목표를 완료 처리하지 않습니다.

1. 승인된 비템플릿 `review_ground_truth.csv`.
2. precision/false-positive 기준을 통과한 `review_decision_truth.csv`.
3. stratified evidence 기준을 통과한 `dataset_strata.csv`.
4. resource/cancel 기준을 통과한 `large_dwg_probe.json`.
5. 구조검토 책임자/팀장 `operator_dry_run_notes.md`.
6. `readiness.status=ready`인 `customer_evidence_manifest.json`.
7. `--evidence-level customer_grade` 최종 감사 `status=passed`.
