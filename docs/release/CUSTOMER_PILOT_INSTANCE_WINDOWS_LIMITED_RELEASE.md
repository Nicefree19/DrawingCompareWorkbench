# Customer Pilot Instance: Windows Limited Customer Release

> ⚠️ **미구현 / 참조용** — 이 런북이 호출하는 `scripts/build_customer_pilot_instance.py`는 리포지토리에 **존재하지 않습니다**(git 이력상 생성된 적 없음). 이 문서는 *계획*이며 실행 절차가 아닙니다.
>
> **실제 패킷 제작**: exe는 `scripts/release_drawing_compare_workbench.py`(PyInstaller, 빌드머신)로 빌드 → 빌드된 app 디렉터리를 **`scripts/build_pilot_packet.py --app-dir <빌드된 onedir> -o <out> --version v0.9.3 --zip`** 로 조립(한 명령·재현 가능, 가이드 소스 `docs/pilot_packet/`).

## Purpose
This step creates a customer-specific packet from the shared pilot handoff packet.

## Build command
```powershell
python scripts\build_customer_pilot_instance.py `
  --handoff-root tmp\pilot_handoff_packet `
  --output-root tmp\customer_pilot_instance `
  --customer-name "Customer A" `
  --pilot-id "PILOT-001" `
  --project-name "Project Alpha" `
  --sku-scope core
```

## Output
- installer
- docs
- samples
- pilot assets
- customer-specific runbook
- customer-specific manifest
- prefilled KPI CSV
