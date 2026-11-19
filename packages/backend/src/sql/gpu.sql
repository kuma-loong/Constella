INSERT INTO gpus (
  gpu_id, node_id, uuid, gpu_index, pci_bus_id, name,
  device_type, card_id, die_id,
  memory_total_mb, first_seen_at, last_seen_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(gpu_id) DO UPDATE SET
  gpu_index=excluded.gpu_index,
  pci_bus_id=excluded.pci_bus_id,
  name=excluded.name,
  device_type=excluded.device_type,
  card_id=excluded.card_id,
  die_id=excluded.die_id,
  memory_total_mb=excluded.memory_total_mb,
  last_seen_at=excluded.last_seen_at
