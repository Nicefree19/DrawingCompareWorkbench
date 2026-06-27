# Customer Pilot Instance: Windows Limited Customer Release

> ⚠️ **미구현 / 참조용** — 이 런북이 호출하는 `scripts/build_customer_pilot_instance.py`는 리포지토리에 **존재하지 않습니다**(git 이력상 생성된 적 없음). 이 문서는 *계획*이며 실행 절차가 아닙니다. 실제로 배포 가능한 패킷은 현존 producer `scripts/release_drawing_compare_workbench.py`(+ 운영자 안내 `docs/INTERNAL_PILOT_GUIDE.md`)로 만드세요.

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
