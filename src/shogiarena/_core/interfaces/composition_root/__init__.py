"""Composition root for ShogiArena runtime wiring.

このパッケージは全ての runtime dependency graph を明示的に構築する。
CLI / dashboard / boundary parser は composition root から deps を取得し、
application entrypoint に渡す。
"""
