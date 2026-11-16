# Event Processor

Status: **implemented in MVP 06** as an independent module of the central server:
[`apps/api-server/app/processing/event_processor.py`](../../apps/api-server/app/processing/event_processor.py).

It validates event batches from edge agents, checks the device's rights, de-duplicates by event UUID
(`ON CONFLICT DO NOTHING`), stores events in PostgreSQL (`camera_events`) and updates hourly
aggregates (`event_statistics`) — see [docs/CENTRAL_SERVER.md](../../docs/CENTRAL_SERVER.md).

It has no dependency on the HTTP layer (it takes a database session, the authenticated device and
raw events), so it can be moved into its own worker process behind a queue when the server is
scaled horizontally (planned with Redis, MVP 09). Until then it runs inside the API process — this
directory is kept as the place for that worker.
