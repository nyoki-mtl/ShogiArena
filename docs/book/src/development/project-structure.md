# Project Structure

このドキュメントでは、ShogiArena の現在のディレクトリ構成と責務境界を説明します。

## 現在の基本方針

ShogiArena は次の 2 層で整理しています。

- `src/shogiarena/_core/**`: 実装正本
- `src/shogiarena/*.py`: 利用者向け facade

つまり、アーキテクチャ上の `contexts / platform / interfaces / shared` は残しつつ、それらを `_core` の下に置いています。

## Directory Layout

```text
src/shogiarena/
├── __init__.py
├── cli.py
├── composition.py
├── engine.py
├── tournament.py
└── _core/
    ├── contexts/
    ├── platform/
    ├── interfaces/
    └── shared/kernel/
```

## 公開面

利用者向けとして説明・保証する import は次です。

- `shogiarena.engine`
- `shogiarena.tournament`
- `shogiarena.cli`
- `shogiarena.composition`

`shogiarena._core.*` は内部 import です。テストや実装では使ってもよいですが、外部利用者には勧めません。

## _core の構造

各 context は下記の責務分離を採用します。

```text
_core/contexts/<name>/
├── domain/                  # 純粋ドメインロジック
├── application/             # use-case / orchestration
├── ports/                   # 外部依存の抽象契約
└── adapters/                # ports の実装
```

## レイヤ責務

- `_core.contexts`: 業務ルールとユースケース
- `_core.platform`: DB / FS / process / network などの共通 I/O
- `_core.interfaces`: CLI / dashboard / 境界パーサ
- `_core.shared.kernel`: 最小共通核

## facade の役割

公開モジュールは薄い再 export と helper に限定します。

- 実装の置き場所を隠す
- 利用者に推奨 import パスを与える
- `_core` を public API に見せない

facade で重い wiring を持ち込むことは避け、依存グラフの正本は composition root に置きます。

## composition root

標準配線の正本は次です。

- `shogiarena.composition.build_default_root`
- `shogiarena._core.interfaces.composition_root.default_root`

CLI や facade helper はここから runtime を取得します。

## 依存方向

- `_core.interfaces -> _core.contexts.application / ports`
- `_core.contexts.application -> _core.contexts.domain / ports`
- `_core.contexts.adapters -> _core.contexts.ports | _core.platform | _core.shared.kernel`
- `_core.platform -> _core.contexts.ports`
- `shogiarena/*.py -> _core only`

## Verification Commands

```bash
uv run python tools/detect_architecture_imports.py --root src/shogiarena --legacy-profile final --fail-on-violations
uv run python tools/detect_dynamic_import_cheats.py --root src --fail-on-violations
make check
```
