# Event Processor — not implemented yet

Status: **placeholder (MVP 01)**. Implementation: MVP 06.

Will receive event batches from edge agents, validate them against `shared/schemas/camera-event.schema.json`,
de-duplicate by event UUID, store them in PostgreSQL (`camera_events`) and update aggregates.
