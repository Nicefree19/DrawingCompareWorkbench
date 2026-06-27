# Customer Pilot Batch: Windows Limited Customer Release

> ⚠️ **미구현 / 참조용** — 이 런북이 호출하는 `scripts/build_customer_pilot_batch.py`(및 `build_customer_pilot_instance.py`, handoff packet producer)는 리포지토리에 **존재하지 않습니다**(git 이력상 생성된 적 없음). 이 문서는 *계획*이며 실행 절차가 아닙니다.
>
> **실제 패킷 제작(2단계)**: ① exe는 빌드머신에서 `scripts/release_drawing_compare_workbench.py`(PyInstaller)로 빌드 → ② 빌드된 app 디렉터리를 **`scripts/build_pilot_packet.py --app-dir <빌드된 onedir> -o <out> --version v0.9.3 --zip`** 로 패킷 조립(런처·가이드·샘플도면·매니페스트, 한 명령·재현 가능). 가이드 소스는 `docs/pilot_packet/`.

## Purpose
Build multiple customer-specific pilot packets from one shared handoff packet and one CSV plan.

## CSV columns
- `customer_name` (required)
- `pilot_id` (required)
- `project_name` (required)
- `sku_scope` (optional, default `core`)
- `operator_count` (optional, default `1`)
- `workspace_hint` (optional)
- `notes` (optional)

## Build command
```powershell
python scripts\build_customer_pilot_batch.py `
  --csv docs\templates\pilot_customer_batch_template.csv `
  --handoff-root tmp\pilot_handoff_packet `
  --output-root tmp\customer_pilot_batch
```

## Output
- one subdirectory per customer
- one `customer-pilot-batch-manifest.json`
- per-customer installer/docs/sample/pilot assets
