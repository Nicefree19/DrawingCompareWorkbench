# Customer Pilot Batch: Windows Limited Customer Release

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
