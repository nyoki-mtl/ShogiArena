# 開局集の管理

ShogiArena では、トーナメントで使用する初期局面（開局）を柔軟に設定できます。

## 開局の種類

### 1. 平手（startpos）

最も基本的な設定で、常に平手の初期局面から対局を開始します。

```yaml
rules:
  initial_positions:
    type: startpos
```

### 2. ファイル指定（file）

SFEN 形式のファイルから局面を読み込みます。

```yaml
rules:
  initial_positions:
    type: file
    source: "configs/openings/standard.sfen"
    flip_policy: pair_both
```

## SFEN ファイルの作成

### フォーマット

1行に1局面を SFEN 形式で記述します。

```
sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1
sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL w - 1
sfen lnsgkgsnl/9/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1
```

### 例: 標準的な開局集

```sfen
# standard.sfen - 平手と居飛車の基本形
sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1
sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/2P6/PP1PPPPPP/1B5R1/LNSGKGSNL b - 3
sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/7P1/PPPPPPP1P/1B5R1/LNSGKGSNL b - 3
```

### コメント

`#` で始まる行はコメントとして無視されます。

```sfen
# 横歩取りの基本形
sfen lnsgkg1nl/1r5s1/p1ppppbpp/1p7/9/2P6/PP1PPPPPP/1B5R1/LNSGKGSNL b - 5

# 角換わりの基本形
sfen lnsgkgsnl/1r7/ppppppppp/9/9/2P6/PP1PPPPPP/7R1/LNSGKGSNL b Bb 5
```

## 先後入れ替えポリシー

`flip_policy` で、局面の先後をどう扱うかを設定します。

### pair_both（推奨）

各局面について、先手・後手を入れ替えた2局を実行します。

```yaml
initial_positions:
  type: file
  source: "openings.sfen"
  flip_policy: pair_both
```

**メリット**: 開局の有利不利による偏りを打ち消せる。

**例**:
- 局面A（エンジンX先手、エンジンY後手）
- 局面A（エンジンX後手、エンジンY先手）

### random

各対局ごとにランダムに先後を決定します。

```yaml
initial_positions:
  type: file
  source: "openings.sfen"
  flip_policy: random
```

### alternate

対局ごとに先手・後手を交互に割り当てます。

```yaml
initial_positions:
  type: file
  source: "openings.sfen"
  flip_policy: alternate
```

### none

ファイルに記載された通りの手番で固定します。

```yaml
initial_positions:
  type: file
  source: "openings.sfen"
  flip_policy: none
```

## 開局集の作成方法

### 1. 手動作成

エディタで SFEN を直接記述します。

```bash
# vim, VSCode などで編集
vim configs/openings/my_openings.sfen
```

### 2. 棋譜から抽出

既存の棋譜から特定の手数の局面を抽出します。

```python
from rshogi.core import Board
from rshogi.record import GameRecord

# 棋譜ファイルを読み込み
board = Board()
with open("game.kif", encoding="utf-8") as f:
    kif_text = f.read()
record = GameRecord.from_kif_str(kif_text)

with open("openings.sfen", "w") as f:
    for move_rec in record.moves[:10]:  # 最初の10手まで
        board.apply_move(move_rec.move)
        f.write(f"sfen {board.to_sfen()}\n")
```

### 3. ツールを使用

将棋の定跡データベースから局面を抽出するツールを使用します。

例: `python-shogi` や `rshogi` を使ったスクリプト。

## 開局集の配置

### プロジェクト内に配置

```
project/
├── configs/
│   └── openings/
│       ├── standard.sfen
│       ├── yokofudori.sfen
│       └── kakugawari.sfen
└── tournament.yaml
```

### 共有ディレクトリに配置

複数のプロジェクトで共有する場合、`{output_dir}` や絶対パスを使用します。

```yaml
initial_positions:
  type: file
  source: "{output_dir}/shared/openings/standard.sfen"
```

または：

```yaml
initial_positions:
  type: file
  source: "/data/shogi/openings/standard.sfen"
```

## 開局集の例

### 標準的な開局集

平手から数手進んだ基本形を含む開局集です。

**standard.sfen**:
```sfen
# 平手
sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1

# 先手▲7六歩
sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/2P6/PP1PPPPPP/1B5R1/LNSGKGSNL w - 2

# 先手▲2六歩
sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/7P1/PPPPPPP1P/1B5R1/LNSGKGSNL w - 2
```

### 戦型別開局集

特定の戦型に絞った開局集です。

**yokofudori.sfen**:
```sfen
# 横歩取り基本形
sfen lnsgkg1nl/1r5s1/p1ppppbpp/1p7/9/2P6/PP1PPPPPP/1B5R1/LNSGKGSNL b - 5

# 横歩取り △8五飛型
sfen lnsgkg1nl/6rs1/p1ppppbpp/1p7/9/2P6/PP1PPPPPP/1B5R1/LNSGKGSNL b - 7
```

**kakugawari.sfen**:
```sfen
# 角換わり基本形
sfen lnsgkgsnl/1r7/ppppppppp/9/9/2P6/PP1PPPPPP/7R1/LNSGKGSNL b Bb 5

# 角換わり棒銀
sfen lnsgkg1nl/1r4s2/pppppp1pp/6p2/9/2P6/PPNPPPPPP/7R1/L1SGKGSNL b Bb 9
```

### テスト用ミニマル開局集

テストやデバッグ用に、少数の局面のみを含む開局集です。

**test.sfen**:
```sfen
# 平手のみ
sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1
```

## 開局のバランス

### 偏りの確認

開局によって先手・後手の有利不利が異なる場合があります。
`flip_policy: pair_both` を使用することで、この偏りを打ち消せます。

### 多様性の確保

開局集に多様な局面を含めることで、エンジンの総合的な強さを測定できます。

- 居飛車・振り飛車の両方
- 序盤・中盤の局面
- バランス型・攻撃型の局面

## 使用例

### 平手のみのトーナメント

```yaml
experiment_name: "standard_tournament"

engines:
  - engine_path: "engine_a.yaml"
  - engine_path: "engine_b.yaml"

tournament:
  scheduler: round_robin
  games_per_pair: 100

rules:
  initial_positions:
    type: startpos
  time_control:
    time_ms: 30000
    increment_ms: 300
```

### 開局集を使用したトーナメント

```yaml
experiment_name: "opening_book_tournament"

engines:
  - engine_path: "engine_a.yaml"
  - engine_path: "engine_b.yaml"

tournament:
  scheduler: round_robin
  games_per_pair: 50

rules:
  initial_positions:
    type: file
    source: "configs/openings/standard.sfen"
    flip_policy: pair_both
  time_control:
    time_ms: 30000
    increment_ms: 300
```

## トラブルシューティング

### 開局ファイルが見つからない

**エラー**: `FileNotFoundError: configs/openings/standard.sfen`

**解決**:
1. ファイルパスが正しいか確認
   ```bash
   ls -l configs/openings/standard.sfen
   ```

2. 相対パスは設定ファイルからの相対パスです

3. 絶対パスを使用
   ```yaml
   source: "/full/path/to/openings.sfen"
   ```

### SFEN のパースエラー

**エラー**: `Invalid SFEN format`

**解決**:
1. SFEN の構文を確認
   - `sfen` で始まっているか
   - 局面表記が正しいか

2. 空行やコメント行を確認

3. ツールで検証
   ```python
   from rshogi.core import Board
   board = Board()
   board.set_sfen("lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1")
   print(board)  # エラーが出なければ OK
   ```

### 局面数が少ない

**問題**: 開局集の局面数が少なく、同じ局面が繰り返される。

**解決**:
1. 開局集に局面を追加
2. `flip_policy: pair_both` で実質的な局面数を2倍にする

## 参考資料

- [トーナメントガイド](tournaments.md): `rules.initial_positions` の詳細設定
- [SFEN 形式](https://web.archive.org/web/20080131070731/http://www.glaurungchess.com/shogi/usi.html): USI プロトコルの SFEN 仕様
