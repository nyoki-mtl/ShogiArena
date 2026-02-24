# Development Guide

This document provides guidelines for contributors and developers working on ShogiArena.

## Development Environment Setup

### Prerequisites

- Python 3.10 or higher
- [uv](https://github.com/astral-sh/uv) package manager
- Node.js 18+ (for dashboard frontend)
- Git

### Installation from Source

```bash
git clone https://github.com/nyoki-mtl/ShogiArena.git
cd ShogiArena
make sync  # Install Python dependencies via uv
```

### Dev Container (Recommended)

For a consistent development environment, use the provided Dev Container:

```bash
# Open in VS Code with Dev Containers extension
code .
# Select "Reopen in Container" when prompted
```

The Dev Container includes all necessary tools and dependencies pre-configured.

## Project Structure

```
ShogiArena/
├── src/shogiarena/          # Main source code
│   ├── arena/                # Core tournament logic
│   │   ├── engines/          # USI engine interface (AsyncUsiEngine, SyncUsiEngine)
│   │   ├── orchestrators/    # Match orchestration and scheduling
│   │   ├── runners/          # Tournament/SPSA/SPRT runners
│   │   ├── services/         # Rating, SPRT, statistics services
│   │   └── configs/          # Configuration models
│   ├── cli/                  # CLI command implementations
│   ├── db/                   # Database layer (SQLite/SQLAlchemy)
│   ├── records/              # Game record data models
│   ├── utils/                # Shared utilities
│   └── web/                  # Dashboard backend and frontend
│       ├── api_server.py     # FastAPI backend
│       └── dashboard/
│           ├── backend/      # Python API modules
│           └── frontend/     # TypeScript frontend (Vite)
├── tests/
│   ├── unit/                 # Unit tests
│   ├── integration/          # Integration tests
│   └── property/             # Property-based tests
├── docs/                     # MkDocs documentation
├── configs/                  # Configuration templates
├── examples/                 # Example configurations
├── stubs/                    # Type stubs for external libraries
└── .sandbox/                 # Development sandbox (not committed)
```

## Development Workflow

### Code Quality Checks

Use the Makefile for common development tasks:

```bash
make format      # Format code with ruff
make lint        # Lint code with ruff
make typecheck   # Type check with mypy (strict mode)
make test        # Run all tests
make test-cov    # Run tests with coverage
make check       # Run format, lint, typecheck, and test
make check-all   # Run check + pre-commit on all files
```

### Running Tests

```bash
# Run all tests
make test

# Run specific test categories
make test-unit        # Unit tests only
make test-integration # Integration tests only
make test-property    # Property-based tests only

# Run with coverage
make test-cov
```

### Frontend Development

```bash
# Install frontend dependencies
npm install

# Run frontend type checking
npm run typecheck

# Run frontend tests
npm run test

# Check for dead code
npm run frontend:knip

# Build frontend
npm run build
```

## Coding Standards

### Python

- **Type Annotations**: Required for all functions and methods
- **Docstrings**: Required for public functions/classes (Japanese)
- **Formatting**: Use `ruff format`
- **Linting**: Pass `ruff check --fix`
- **Type Checking**: Pass `mypy --strict`
- **Naming**:
  - Variables/functions: snake_case (English words)
  - Constants: UPPER_SNAKE_CASE
  - Private members: prefix with underscore
- **Logger Messages**: English only, concise and clear

### Error Handling

- **NO silent exceptions**: Avoid bare `except Exception: pass`
- **Specific exception handling**: Catch specific exception types when possible
- **Proper logging**: Log errors before re-raising or returning failure
- **Fail fast**: Don't use fallbacks to hide bugs; fix the root cause
- **Breaking changes OK**: This is a development project; prioritize clean code over backward compatibility

### Configuration Files

- Use lowercase snake_case (`tournament_config.yaml`)
- Store templates in `configs/`
- Development configs go in `.sandbox/`

## Contributing & Release Process

This file focuses on the development environment, code structure, and coding standards.
For contribution workflow and release procedures, see:

- `docs/development/contributing.md`

## Testing Guidelines

### Unit Tests

- Test individual functions/classes in isolation
- Use mocks for external dependencies
- Keep tests fast and deterministic
- Place in `tests/unit/`

### Integration Tests

- Test component interactions
- Use real engines when possible (or test doubles)
- May involve I/O or subprocess execution
- Place in `tests/integration/`

### Property-Based Tests

- Use Hypothesis for property-based testing
- Test invariants and edge cases
- Place in `tests/property/`

## Documentation

Documentation is built with MkDocs and deployed to GitHub Pages.

### Building Locally

```bash
make docs-serve  # Serve at http://localhost:8000
make docs-build  # Build static site
```

### Documentation Structure

- `docs/getting-started/`: Installation and first steps
- `docs/user-guide/`: Feature guides (tournaments, SPSA, Python API)
- `docs/technical/`: Architecture and implementation details
- `docs/api/`: API reference documentation
- `docs/development/`: Development guidelines and contributing

### Writing Documentation

- Use Markdown format
- Write in Japanese for user-facing docs, English for technical docs
- Include code examples with syntax highlighting
- Use admonitions for important notes (tip, warning, note)

## CI/CD

### GitHub Actions

- **CI workflow** (`ci.yml`): Runs on every push and PR
  - Linting (ruff)
  - Type checking (mypy)
  - Tests (pytest)
  - Frontend checks
  
- **Docs workflow** (`docs.yml`): Builds and deploys documentation
  - Runs on push to main
  - Deploys to GitHub Pages

- **Release workflow** (`release.yml`): Publishes to PyPI
  - Triggered by version tags (`vX.Y.Z`)

## Common Tasks

### Adding a New Dependency

```bash
# Add runtime dependency
uv add package-name

# Add development dependency
uv add --dev package-name

# Sync dependencies
make sync
```

### Debugging

```bash
# Run with debug logging
shogiarena run tournament config.yaml --log-level DEBUG

# Use Python debugger
python -m pdb -m shogiarena.cli.main run tournament config.yaml
```

### Profiling

```bash
# Profile tournament execution
python -m cProfile -o profile.stats -m shogiarena.cli.main run tournament config.yaml

# Analyze profile
python -m pstats profile.stats
```

## Architecture Overview

### Core Components

1. **USI Engine Interface** (`arena/engines/`)
   - `AsyncUsiEngine`: Asynchronous engine communication
   - `SyncUsiEngine`: Synchronous wrapper for simple use cases
   - `UsiProcess`: Low-level USI protocol implementation

2. **Orchestrators** (`arena/orchestrators/`)
   - Schedule and execute matches in parallel
   - Manage engine instances and time controls
   - Handle adjudication (draw, win by evaluation)

3. **Runners** (`arena/runners/`)
   - `TournamentRunner`: Round-robin tournaments
   - `SprtRunner`: SPRT-based engine testing
   - `SpsaRunner`: Parameter tuning with SPSA

4. **Services** (`arena/services/`)
   - Rating calculation (Elo, Glicko)
   - SPRT statistical testing
   - Game statistics and analysis

5. **Dashboard** (`web/dashboard/`)
   - FastAPI backend for REST API
   - TypeScript frontend with Vite
   - Real-time updates via Server-Sent Events

### Key Design Principles

- **Async-first**: Use asyncio for concurrent execution
- **Type-safe**: Full type annotations with strict mypy checking
- **Modular**: Clear separation between engines, orchestration, and runners
- **Testable**: Dependency injection and mockable interfaces
- **Observable**: Comprehensive logging and live dashboard

## Getting Help

- **Issues**: Report bugs or request features on [GitHub Issues](https://github.com/nyoki-mtl/ShogiArena/issues)
- **Discussions**: Ask questions on [GitHub Discussions](https://github.com/nyoki-mtl/ShogiArena/discussions)
- **Documentation**: Check the [full documentation](https://nyoki-mtl.github.io/ShogiArena/)

## Reference Materials

The `_refs/` directory contains vendored reference implementations for learning:

- **OpenBench**: SPRT implementation and distributed testing
- **YaneuraOu**: USI protocol reference and engine integration
- **cutechess**: Tournament scheduling and time control management
- **fastshogi**: High-performance match execution patterns
- **rshogi**: Python shogi library (`_refs/rshogi/` contains reference implementation)

Consult these when implementing related features or understanding protocols.
