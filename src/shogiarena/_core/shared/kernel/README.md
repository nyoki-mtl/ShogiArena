## shared/kernel

Minimal cross-layer primitives that are safe for any architecture layer.

### Naming Rules

- Use `*_types.py` for `TypeAlias` / `TypedDict`-centric modules.
- Use `*_models.py` for `dataclass` / model-object-centric modules.
- Keep conversion logic in `*_coercion.py` or `<category>_coercion/` modules.
