# Arena Dashboard Tournament SSE Contract

The tournament dashboard uses a single Summary SSE stream.

- API: `src/shogiarena/web/dashboard/backend/tournament/api.py`
- Frontend subscriber: `src/shogiarena/web/dashboard/frontend/src/modules/tournament/services/data.ts`

## Design Goals

- Reduce concurrent EventSource connections by opening Summary SSE only on tabs that need it.
- Live View uses a separate WebSocket pipeline (`summary:update`) to keep the summary fresh.
- Closing Summary SSE does not discard the last known Summary snapshot.

## Summary SSE Activation

Summary SSE is active only on these tabs:

- `tournament`
- `openings`
- `engines`
- `rules`
- `games`

Summary SSE is stopped on other tabs (`live`, `instances`, `spsa`).
