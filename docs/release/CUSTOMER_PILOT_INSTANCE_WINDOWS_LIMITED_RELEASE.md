# Customer Pilot Instance: Windows Limited Customer Release

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
