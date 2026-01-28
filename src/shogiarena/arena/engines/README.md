# Engines Module Overview

This package provides the building blocks for running USI-compatible engines within the arena. Each module keeps a clear separation of concerns so orchestration logic can compose them as needed. The table below summarizes the primary classes and helpers.

## Module index

| Module | Key types | Responsibility |
| --- | --- | --- |
| `engine_factory.py` | `EngineFactory` | Loads `UsiEngineConfig`, resolves binaries (local or remote), provisions artifacts, and returns fully-initialized `AsyncUsiEngine` instances. |
| `time_control.py` | `TimeControlLimits`, `TimeControl`, `TimeControlSpecParseError` | Encapsulates clock settings and runtime state (remaining time, increments, byo-yomi) for each side. Used by runners to track clocks and build thinking requests. |
| `usi_bridge.py` | `AsyncUSIProcessBridgeProtocol` | Structural typing contract for USI bridges. Keeps launch/transport abstractions decoupled from `AsyncUsiEngine` while living alongside engine helpers. |
| `usi_bridge_spawner.py` | `SpawnerBackedUSIBridge` | Unified process launcher that talks to local or SSH instances via the spawner layer and implements `AsyncUSIProcessBridgeProtocol`. |
| `usi_config.py` | `UsiEngineConfig` | Normalizes engine YAML into a strict, typed config with path resolution and overrides. |
| `usi_engine.py` | `AsyncUsiEngine`, `AnalysisHandle`, `UsiMateResult` | High-level session wrapper that owns an `AsyncUsiProcess`, performs protocol handshakes, applies options, streams `info` lines, and manages think/analysis lifecycles. |
| `sync_usi_engine.py` | `SyncUsiEngine`, `SyncAnalysisHandle` | Blocking wrapper over `AsyncUsiEngine` that spins a background event loop for synchronous scripts and notebooks. |
| `usi_process.py` | `AsyncUsiProcess` | Lightweight state machine over an `AsyncUSIProcessBridgeProtocol`, ensuring start/stop semantics and guarding I/O access. |
| `usi_protocol.py` | `UsiProtocolParser`, `UsiOption`, `UsiIdField` | Stateless parsers that convert raw USI output (`id`, `option`, `info`, `bestmove`) into structured types. |
| `usi_think.py` | `UsiThinkRequest`, `request_from_time_controls`, `normalize_searchmoves` | Builders for USI `go` commands and helper utilities to derive them from time-control/position context. |
| `usi_types.py` | `UsiEvalValue`, `UsiBound`, `UsiThinkPV`, `UsiThinkResult` | Value objects representing USI evaluation scores, score bounds, parsed `info` lines, and `bestmove` results. |

## Typical flow

1. A tournament orchestrator loads an engine configuration (`UsiEngineConfig`) and asks `EngineFactory` to materialize an `AsyncUsiEngine`.
2. `EngineFactory` provisions binaries with `SpawnerBackedUSIBridge` and wraps it in `AsyncUsiProcess`, yielding a ready-to-use `AsyncUsiEngine`.
3. During games, the runner tracks per-side `TimeControl` state. When it's an engine's turn, it calls `request_from_time_controls(...)` to obtain a `UsiThinkRequest`, then feeds `UsiThinkRequest.to_command()` to `AsyncUsiEngine`.
4. `AsyncUsiEngine` streams `info` lines through `UsiProtocolParser`, which produces `UsiThinkPV` objects capturing evaluation data (`UsiEvalValue`, `UsiBound`).
5. When the engine replies with `bestmove`, `UsiThinkResult` ties together the move and the final PVs, allowing the runner to update game state and clocks.

## Extending or integrating

- **Adding a new engine backend**: implement `AsyncUSIProcessBridgeProtocol` (see `usi_bridge.py` and `SpawnerBackedUSIBridge` for reference) and plug it into `AsyncUsiProcess`.
- **Custom time policies**: extend `TimeControlLimits` or supply new builders in `usi_think.py` to translate time settings into `UsiThinkRequest` structures.
- **Protocol variants**: add parsing helpers to `usi_protocol.py` if your engine emits non-standard tokens; keep the logic stateless so `AsyncUsiEngine` remains focused on orchestration.

Keep new functionality focused within the appropriate module so that orchestrators, runners, and analytics layers can continue to depend on small, composable pieces.
