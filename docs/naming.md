# 命名規則（Naming Guide）

ShogiArena プロジェクトの命名規則を定義する。Python・TypeScript・両者の境界をまたぐデータ契約を対象とし、新規コードはすべて本ガイドに従う。

本書は公開向けの統合ガイドであり、内部運用の信頼源は `agent-docs/rules/naming-conventions.md`・`agent-docs/rules/structure-conventions.md`・`agent-docs/rules/type-safety.md` に置く。両者が矛盾する場合は内部ルールを優先し、本書を追従させる。

機械検証は `make check`（warn-only）/ `make convention-lint-strict`（strict）で実施し、Python は `tools/detect_naming_conventions.py`、TypeScript は `npm run frontend:lint`（Biome）/ `npm run frontend:typecheck`（tsc）が担当する。

---

## 1. 基本原則

1. **責務を名前で判別できること**。名前から「何の・どの役割か」が読み取れる状態を維持する。
2. **同一概念に一つの語彙**。`cfg` と `config`、`engine` と `eng` のような表記揺れを禁止する。
3. **境界で変換、内部は統一**。外部契約と内部表現で命名規則が異なる場合は、境界（parser / serializer）で明示的に変換する（→ [6. データ契約](#6-データ契約の命名)）。
4. **ASCII を原則とする**。識別子・ファイル名に非 ASCII を使わない。先頭に数字を使わない。識別子で許可する記号は `_`（Python）/ `-`（TypeScript ファイル名）のみ。`.test.ts` / `.d.ts` のような構造接尾辞は例外として扱う。
5. **時系列・状態を名前に埋め込まない**。`new_` / `old_` / `_v2` / `_final` / `_tmp` / `_copy` / `Legacy*` を新規追加しない。
6. **将棋ドメイン語彙は正本表に従う**。駒名・終局理由・棋譜/局面/手番などの語彙は `docs/shogi-terminology.md` を参照し、`rshogi-py` と一般的な英語訳に揃える。

---

## 2. 大文字小文字の対応表

| 対象 | Python | TypeScript |
|------|--------|------------|
| ディレクトリ | `snake_case` | `kebab-case` |
| ファイル | `snake_case.py` | `kebab-case.ts`（例外は `4.2` 参照） |
| クラス / 型 / Protocol / interface | `PascalCase` | `PascalCase` |
| 関数 / メソッド | `snake_case` | `camelCase` |
| 変数 | `snake_case` | `camelCase` |
| 定数 | `UPPER_SNAKE_CASE` | `UPPER_SNAKE_CASE` |
| enum メンバ | `UPPER_SNAKE_CASE` | `PascalCase`（値は用途に従う） |

正規表現（Python、機械検証で適用）:

| 対象 | パターン |
|------|---------|
| directory | `^[a-z][a-z0-9_]*$` |
| module file | `^[a-z][a-z0-9_]*\.py$` |
| class / protocol | `^[A-Z][A-Za-z0-9]*$` |
| function / method / variable | `^[a-z][a-z0-9_]*$` |
| constant | `^[A-Z][A-Z0-9_]*$` |

可視性プレフィックス（Python）は標準規約に従い、上記パターンの前に付加できる。機械検証は先頭アンダースコアを除去した名前でパターンマッチする。

- `_name` — module-private member（ファイル・クラス・関数いずれも可）
- `__name` / `__dunder__` — name mangling / dunder（`__init__.py`, `__main__.py`, `__init__` 等）

---

## 3. Python の命名

### 3.1 役割接尾辞（必須）

クラス・Protocol は役割を接尾辞で表す。役割が一致する場合は必ず採用する。

| 役割 | 接尾辞 | 実例 |
|------|--------|------|
| Port protocol（依存逆転の契約） | `*Port` | `EngineFactoryPort`, `ShogiRepositoryPort` |
| Factory protocol / 実装 | `*Factory` | `EngineRuntimeFactory`, `SpsaDashboardServicesFactory` |
| Adapter class | `*Adapter` | `EngineRuntimeAdapter` |
| Repository abstraction | `*Repository` | `ShogiRepository` |
| Policy abstraction | `*Policy` | （`slot_policy.py` 等。判断ロジックに付与） |
| Mixin class | `*Mixin` | — |
| Error class | `*Error` | `ProvisionError`, `CliArgumentError`, `ContractParseError` |
| Request DTO | `*Request` | — |
| Response DTO | `*Response` | — |
| Callable protocol | `*Fn` | `PublishFn`, `SnapshotPayloadParserFn`, `DashboardRunStateLoaderFn` |

ルール:

- Port 系契約は `*Port` / `*Factory` / `*Repository` / `*Policy` / `*Fn` を使用する。`*Like` / `*ServiceLike` / `*Protocol` の新規追加は禁止（既存は段階的に移行）。
- 役割が曖昧な `*Manager` / `*Helper` / `*Util` は新規禁止。責務が単一に限定できる場合のみ `*Manager` を許可する。
- Facade は `*Facade`（例: `DashboardScheduleFacade`）。

### 3.2 ファイル名

- 「対象 + 役割」または「動作 + 対象」で命名する（例: `session_run_service.py`, `runtime_factory.py`, `engine_runtime_adapter.py`）。
- ディレクトリ名の接頭辞を重複させない（`engine/config_hashing.py` ◯ / `engine/engine_config_hashing.py` ✕）。検証ルール `N008`。
- `*_and_*` / `*_or_*` のような複数責務名を新規追加しない。
- basename は原則 1〜4 語。5 語以上が必要なら責務分割を検討する。
- 1 語 basename は親ディレクトリ文脈で責務が一意に判別できる場合のみ許可。`service` / `model(s)` / `type(s)` / `common` / `utils` / `helper(s)` / `misc` / `temp` の 1 語ファイルは新規禁止。
- 禁止 basename: `helpers.py`, `utils.py`, `common.py`, `misc.py`, `temp.py`（検証ルール `N005`）。

レイヤ別の語彙優先順位:

| レイヤ | 優先する語彙 | 避ける語彙 |
|--------|-------------|-----------|
| `domain` | 業務語彙 | `http`, `sql`, `cli` |
| `application` | ユースケース語彙 | `thread`, `socket` |
| `adapters` / `platform` | 実体 I/O 語彙（`sqlite`, `ssh`, `http`, `process`） | — |
| `interfaces` | 境界語彙（`parsers`, `serializers`, `commands`, `api`） | framework 固有語の多用 |

### 3.3 関数・変数

- **boolean**: `is_*` / `has_*` / `can_*` / `should_*` のいずれかで開始する。
- **時刻 / ID**:
  - 絶対時刻（datetime / ISO 文字列） → `*_at`（例: `started_at`, `completed_at`, `updated_at`）
  - 絶対時刻（Unix epoch millisecond） → `*_at_ms`（例: `started_at_ms`, `updated_at_ms`）
  - 経過時間・残り時間・レイテンシ・持ち時間 → `*_ms`（例: `wall_time_ms`, `latency_delta_ms`, `remaining_time_ms`）
  - 低レベルイベントの raw timestamp → `ts_ms` / `raw_ts_ms`
  - 識別子 → `*_id`（例: `game_id`, `black_player_id`）
  - token → `*_token`
- **コレクション**:
  - list → 複数形（`workers`, `games`, `engines`）
  - dict / map → `*_by_*` を推奨（`games_by_id`）
- **変換関数**: `to_*` / `from_*` / `parse_*` / `serialize_*` を使う（→ [6.3](#63-境界変換関数)）。

### 3.4 enum / Literal

- 閉じた選択肢は `Literal` または `Enum` で表す。`dict[str, Any]` で表現しない。
- 値は外部契約に現れる文字列をそのまま用い、内部規則（snake_case）に寄せる。
- 例:
  ```python
  AnalysisStatus = Literal["ready", "warming", "error"]
  SnapshotPayloadKind = Literal["summary", "games"]

  class SpsrtConfig(BaseModel):
      flip_policy: Literal["alternate", "random", "none", "pair_both"] = "pair_both"
      model: Literal["gsprt-trinomial-v1", "gsprt-pentanomial-v1"] = "gsprt-trinomial-v1"
  ```

### 3.5 略語

- 一般定着略語（`API`, `CLI`, `URL`, `JSON`, `ID`, `SFEN`, `KIF`, `CSA`, `USI`, `SPRT`, `SPSA`, `Elo`）は許可。
- ドメイン固有略語は初回出現時にコメントで展開する。
- 同一概念に複数略語を混在させない。

---

## 4. TypeScript（ダッシュボード Frontend）の命名

対象: `src/shogiarena/_core/interfaces/dashboard/frontend/src/**`。
フォーマット / Lint は Biome（`biome.json`: indent 4 spaces / single quote / line width 120）、型検査は `tsc` が担当する。

### 4.1 識別子

| 対象 | 規則 | 実例 |
|------|------|------|
| 型 / interface / type alias | `PascalCase` | `EngineViewModel`, `SummaryViewModel`, `DashboardCoreState` |
| union リテラル型 | `PascalCase`（メンバ値は契約に従う） | `type StoreEventType = 'engines' \| 'timeControls' \| 'all'` |
| 関数 | `camelCase`（動詞始まり） | `buildGameRowViewModel`, `resetStore`, `normalizeStringRecord` |
| 変数 | `camelCase` | `storeState`, `indicatorVariant`, `movesLabel` |
| 定数 | `UPPER_SNAKE_CASE` | `MOVES_PLACEHOLDER` |
| ファクトリ / ストア export | `camelCase` | `summaryStore` |

- boolean 変数・関数は Python と同じく `is*` / `has*` / `can*` / `should*` を使う。
- 変換関数は `to*` / `from*` / `parse*` / `normalize*` / `serialize*` を使う。
- 内部ロジックの命名は **camelCase**。一方で wire（サーバとの契約）に現れるキーは **snake_case** のまま保持する（→ [6.1](#61-原則-内部表現と-wire-契約を分離する)）。型のフィールド名がそのまま wire キーになる場合は snake_case を許可する:
  ```ts
  export interface NormalizedGameRow {
      game_id: string;          // wire キー: snake_case のまま
      black_player?: string;
      total_plies?: number | null;
  }
  ```

### 4.2 ファイル名

- source file は **kebab-case**（例: `summary-store.ts`, `game-dialog.ts`, `dom-bindings.ts`, `tournament.ts`）。
- camelCase source file は新規追加しない。単一の主要エクスポートを持つ場合も、ファイル名は export 名ではなく path として読みやすい kebab-case に寄せる。
- ドット修飾 source file は新規追加しない。派生モジュールは `viewmodel-result.ts`, `convergence-delta-chart.ts` のように kebab-case の basename で表す。
- ディレクトリは kebab-case（`modules/`, `services/`, `components/`, `state/`, `contracts/parsers/`）。
- テストは `*.test.ts`、型宣言専用ファイルは `*.d.ts`。
- project-owned generated file も generator 側を更新して kebab-case に統一する。外部生成物で即時変更できない場合のみ、期限付き例外として扱う。

### 4.3 import エイリアス

`tsconfig.json` で定義したパスエイリアスを使う。相対パスの深いさかのぼり（`../../../`）より優先する。

```
@/*        → src/*
@modules/* → modules/*
@styles/*  → styles/*
@types/*   → types/*
```

---

## 5. データベース（SQLite / SQLAlchemy）の命名

`platform/db/` のスキーマはすべて **snake_case**。

- **テーブル名**: 単数形 snake_case（`player`, `game`, `game_record`）。
- **カラム名**: snake_case。役割接尾辞を踏襲する。
  - 主キー: `id`
  - 外部キー: `<entity>_id`（例: `black_player_id`, `white_player_id`）
  - 時刻: 絶対時刻は `*_at` / `*_at_ms`、経過時間・持ち時間・レイテンシは `*_ms`、日付のみは `*_date`
  - SFEN 等の局面: `*_sfen`（`initial_position_sfen`）
- 既存の歴史的カラム（`game_result`, `time_control_black` など）は snake_case で一貫しているが、時刻名の意味が曖昧なものは大規模 rename タスクで再評価する。

---

## 6. データ契約の命名

Python ⇄ TypeScript / WebSocket / JSON ファイル（`meta.json`, `state.json` 等）をまたぐ「wire 契約」の命名規則を定義する。

### 6.1 原則: 内部表現と wire 契約を分離する

```
外部データ (JSON / WebSocket / DB)         wire キー = snake_case
        │
        ▼  parse_wire + Pydantic（境界で検証・変換）
   内部表現
        ├─ Python: snake_case（TypedDict / dataclass / Pydantic model）
        └─ TypeScript: 内部ロジックは camelCase、wire 由来フィールドは snake_case のまま
```

- **wire 契約キーは snake_case に統一する**。Python・TypeScript・JSON ファイルすべてで snake_case を正本とする（例: `game_id`, `total_plies`, `black_player`, `engine_time_controls`, `default_time_control`, `is_summary_ready`）。
- 新規の契約キーで camelCase を導入しない。過去に存在する camelCase キー（例: `enginesMeta`, `summaryReady`, `tournamentType`）は snake_case へ移行対象とし、後方互換 alias は原則追加しない。既存 alias も撤去対象とする。
- 旧 artifact を読む必要がある場合は、通常の parser alias ではなく、期限・対象・撤去条件を持つ artifact migration として別途判断する。
- TypeScript 側は wire キーを変名せず snake_case のまま受ける。camelCase が必要なら ViewModel 変換層で明示的に対応付ける。

### 6.2 DTO / モデルの命名

- Pydantic / dataclass / TypedDict のクラス名は `PascalCase`。役割に応じて `*Request` / `*Response` / `*Snapshot` / `*Entry` / `*ViewModel` / `*Config` を使う。
  - 例: `SpsaMetaData`, `CorrelationAnalysisSnapshot`, `GameBriefEntry`, `EngineViewModel`, `SpsrtConfig`。
- TypedDict で必須 / オプションを分ける場合、必須側を private base（`_RequiredFields`）に切り出し、オプションを `total=False` 側へ置く:
  ```python
  class _GameBriefEntryRequired(TypedDict):
      game_id: str

  class GameBriefEntry(_GameBriefEntryRequired, total=False):
      black_player: str | None
      white_player: str | None
      game_result: str | None
  ```
- 判別共用体は `mode` / `status` 等の `Literal` フィールドで判別する（→ [3.4](#34-enum--literal)）。

### 6.3 境界変換関数

`interfaces/boundaries/` の変換関数は方向を名前で表す。

| 方向 | 接頭辞 | 実例 |
|------|--------|------|
| wire → 内部 | `parse_*` | `parse_wire`, `parse_payload` |
| 内部 → wire | `serialize_*` | `serialize_dashboard_snapshot_payload` |
| 型 A → 型 B（同層内変換） | `to_*` / `from_*` | `to_dict`, `from_mapping` |
| 抽出 | `extract_*` | `extract_engines`, `extract_engine_meta` |
| 正規化 | `normalize_*` / `coerce_*` | `normalize_string_record`, `coerce_int` |

ルール:

- 境界パースは `parse_wire + Pydantic 型制約`（`Literal` / `NonNegativeInt` / `PositiveInt` / `StringConstraints` 等）を第一選択とし、表現できないときのみ `coerce_*` / `field_validator` を追加する。
- 1 行委譲だけの `_parse_payload` ラッパーや、`_coerce_int` / `_coerce_float` / `_coerce_str` の単純複製を新規追加しない。`coerce_*` は `shogiarena.shared.kernel.coerce` を正本とする。

### 6.4 棋譜・記録（SFEN / KIF / CSA）

- 局面は SFEN を正本とし、フィールドは `*_sfen`（`initial_position_sfen`）。
- 1 手単位の記録は `ply`（手数）/ `next_move`（指し手）/ `next_move_time_ms` / `next_move_comment` / `seldepth` のように、対象 + 役割で命名する。
- 形式変換関数は `to_kif` / `to_csa` / `parse_kif` / `parse_csa` のように形式名を含める。ドメインロジックは `rshogi` を参照する。

---

## 7. テストの命名

| 対象 | Python | TypeScript |
|------|--------|------------|
| ファイル | `test_<subject>.py` | `<subject>.test.ts` |
| クラス | `Test<Subject>` | — |
| 関数 / ケース | `test_<behavior>_<condition>` | `describe`/`it` に振る舞いを記述 |

- パラメータ化ケース ID は短い snake_case。

---

## 8. 禁止命名（新規追加禁止）

- ファイル: `helpers.py` / `utils.py` / `common.py` / `misc.py` / `temp.py`
- 接頭辞・語: `Legacy*` / `legacy_*` / `tmp_*` / `new_*` / `old_*` / `*_v2` / `*_final` / `*_copy`
- クラス: `*Helper` / `*Util` / 責務が単一に限定できない `*Manager`
- 契約契約: 新規 camelCase wire キー / `*Like` / `*ServiceLike` / 新規 `*Protocol` 接尾辞

---

## 9. 例外運用

規約に反する命名がやむを得ない場合は `agent-docs/rules/convention-exceptions-policy.md` の手順で申請・記録する。本書では原則 allowlist を持たない。

## 10. 検証コマンド

```bash
# Python: 命名 / 構成 lint（warn-only は make check に統合済み）
make convention-lint
make convention-lint-strict

# Python 個別
uv run python tools/detect_naming_conventions.py --root src --non-blocking-rule-id N008 --fail-on-violations
uv run python tools/detect_structure_conventions.py --root src --fail-on-violations

# TypeScript
npm run frontend:lint        # Biome
npm run frontend:typecheck   # tsc
```

逸脱が検出された既存コードは大規模一括リネームせず、変更対象に含めたときに本書へ段階的に寄せる。import パス変更が広範囲に及ぶ場合はタスクを分離して実施する。
