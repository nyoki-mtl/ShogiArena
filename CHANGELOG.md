# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-01-28

### Changed
- **CLI**: `--version` now reports the package version (no hardcoded string)

## [0.1.0] - 2026-01-27

### Added
- **Specialized runners**: Introduced `SprtRunner` and `SpsaRunner` for dedicated tournament execution workflows
- **Dashboard enhancements**: Live view management, SPSA algorithm configuration display, and improved KPI visualization
- **CLI expansion**: Enhanced override syntax and command structure reorganization
- **Windows support**: PowerShell CIM fallback for health monitoring when WMIC is unavailable

### Changed
- **Package rename**: `shogi_arena` → `shogiarena` for consistency
- **CLI unification**: Merged `init` and `config` commands with improved default settings
- **Game identifiers**: Start at `g0001` instead of `g0000` (existing run states must be recreated)
- **Dashboard refactor**: Unified game progress structure and simplified tab management
- **Engine helpers**: Consolidated CLI engine helper functions

### Fixed
- **Dashboard actions**: Restored actions menu for pending games with timestamp tooltips
- **Type safety**: Added precise types to `api_server.py` and SPSA endpoints
- **CLI warnings**: Suppressed unnecessary warnings for init/config commands
- **Performance**: Optimized init/config command startup time
- **GitHub token**: Improved clarity in token prompt messages

### Removed
- **Deprecated commands**: Removed `run match` command
- **Simplified CLI**: Streamlined dashboard CLI surface
- **Legacy placeholders**: Dropped `{nnue_model_dir}` from documentation and provisioning

### Documentation
- **SSH requirements**: Documented `asyncssh` as mandatory dependency
- **Model paths**: Recommend `{engine_dir}/...` or absolute paths for models/evals
- **License**: Added MIT License with GPL-3.0 dependency notice for `cshogi`

[Unreleased]: https://github.com/nyoki-mtl/ShogiArena/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/nyoki-mtl/ShogiArena/releases/tag/v0.1.1
[0.1.0]: https://github.com/nyoki-mtl/ShogiArena/releases/tag/v0.1.0
