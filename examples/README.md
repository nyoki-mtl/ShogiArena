# Shogi Arena Examples

このディレクトリは Shogi Arena の設定テンプレートと実行例をまとめる場所です。

## ディレクトリ構成

```
examples/configs/
├── run/                              # 実行設定テンプレート
│   ├── tournament/example.yaml       # トーナメント実行（ラウンドロビン、ガントレット）
│   ├── sprt/example.yaml             # SPRT テスト（統計的検定）
│   ├── spsa/example.yaml             # SPSA チューニング（パラメータ最適化）
│   └── generate/example.yaml         # 自己対局による棋譜生成
├── resources/
│   ├── engines/                      # エンジン設定テンプレート
│   ├── instances/                    # インスタンス設定（ローカル、SSH）
│   ├── openings/                     # 小規模な開始局面／指し手列
│   └── spsa/                         # SPSA space 定義
└── example.yaml                      # TournamentRunConfig の包括的リファレンス
```

## 使い方

### YaneuraOuと水匠5ですぐ試す

リポジトリをcloneした環境では、公式YaneuraOu V9.00と公開評価関数の水匠5を取得し、4局の短いトーナメントを実行できます。
エンジンは2 threadsで動作し、同梱の`examples/configs/resources/openings/pair_positions.sfen`（3局面）を先後反転ペアで使います。
Windows x86_64とmacOS（Intel／Apple Silicon）では公式prebuiltを使います。
Linuxでは固定tagからbuildするため、`git`、`make`、`clang++`または`g++`が必要です。

```bash
uv run --with py7zr python examples/bootstrap_yaneuraou.py
uv run shogiarena run tournament examples/.runtime/yaneuraou-suisho5/tournament.yaml --dry-run
uv run shogiarena run tournament examples/.runtime/yaneuraou-suisho5/tournament.yaml
```

実行中は`http://localhost:8080/index.html`を開きます。
完了したrunは次のコマンドで開き直せます。

```bash
uv run shogiarena dashboard serve --config examples/.runtime/yaneuraou-suisho5/tournament.yaml
```

BootstrapはYaneuraOuと水匠5の公式GitHub Releaseから取得し、SHA-256を検証してから`examples/.runtime/`へ配置します。
エンジンと評価関数はリポジトリへ取り込みません。
取得元は[YaneuraOu V9.00](https://github.com/yaneurao/YaneuraOu/releases/tag/V9.00)と[水匠5評価関数](https://github.com/yaneurao/YaneuraOu/releases/tag/suisho5)です。

同じbootstrapは、YaneuraOuの`FV_SCALE`を2 updateだけ調整するSPSAデモも生成します。
YaneuraOu V9.00はShogiArena固有のtunable manifestを実装していませんが、`FV_SCALE`は通常のUSI `spin` optionなので調整できます。
一方で`Clear Hash` optionは公開されないため、この例では`clear_hash: false`を明示しています。
これは実行経路を確認するための小規模デモであり、得られた値を実戦向けの推奨値とはみなしません。

```bash
uv run shogiarena run spsa examples/.runtime/yaneuraou-suisho5/spsa.yaml --dry-run
uv run shogiarena run spsa examples/.runtime/yaneuraou-suisho5/spsa.yaml
```

### YaneuraOuの探索parameterをSPSAする

`FV_SCALE`ではなく探索parameterを調整する上級例には、V9.40から分岐した
[ShogiArena SPSA fork](https://github.com/nyoki-mtl/YaneuraOu/tree/6b014a16ea0648ab04c62dd245a40a46306a0835)
を使います。このforkはV9.40の公式SPSA定義に含まれる149個のparameter
（探索・move ordering 148個と`FV_SCALE`）、`usi_tunables` manifest、`Clear Hash`を公開します。
生成される`search-spsa-space.yaml`では、探索の主要領域をまたぐ32個を`select`します。
対象を変える場合は、engineを再buildせずにこの`select`を編集します。
通常buildには影響せず、`SHOGIARENA_SPSA`を定義したbuildでだけ有効になります。
この上級例は2 threads／4並列、2 pairs per update、最大320手です。

次はWindowsのMSYS2／MinGW clangで確認済みのbuild例です。

```bash
git clone https://github.com/nyoki-mtl/YaneuraOu.git ../YaneuraOu-shogiarena-spsa
git -C ../YaneuraOu-shogiarena-spsa checkout 6b014a16ea0648ab04c62dd245a40a46306a0835
cd ../YaneuraOu-shogiarena-spsa/source
make clean
make -j8 tournament COMPILER=clang++ TARGET_CPU=AVX2 \
  YANEURAOU_EDITION=YANEURAOU_ENGINE_NNUE \
  EXTRA_CPPFLAGS="-DSHOGIARENA_SPSA"
cd ../../ShogiArena
```

通常のbootstrapで水匠5を用意し、buildしたbinaryを追加指定すると、検索parameter用の設定も生成されます。

```bash
uv run --with py7zr python examples/bootstrap_yaneuraou.py \
  --spsa-search-engine ../YaneuraOu-shogiarena-spsa/source/YaneuraOu-by-gcc.exe
uv run shogiarena run spsa examples/.runtime/yaneuraou-suisho5/search-spsa.yaml --dry-run
uv run shogiarena run spsa examples/.runtime/yaneuraou-suisho5/search-spsa.yaml
```

この例は統合経路の確認用です。2 updateの結果を推奨値として採用せず、実際の調整では十分な対局数と独立した棋力検証を行ってください。

### 1. テンプレートをコピーして調整

```bash
# トーナメント実行
cp examples/configs/run/tournament/example.yaml my_tournament.yaml

# SPRT テスト
cp examples/configs/run/sprt/example.yaml my_sprt.yaml

# SPSA チューニング
cp examples/configs/run/spsa/example.yaml my_spsa.yaml
```

**注意**：サンプル設定は artifact 参照やプレースホルダー（`{output_dir}`、`{engine_dir}`）を使う場合があります。
次のどちらかで実環境のパスに解決してください。

- `shogiarena config init` を実行してパスを設定する
- エンジン、インスタンス、評価関数、定跡などのパスを自分の環境に合わせて書き換える

### 2. 検証して実行

```bash
shogiarena run tournament my_tournament.yaml --dry-run
shogiarena run tournament my_tournament.yaml
```

## 設定ファイルの説明

### Tournament (トーナメント)

`examples/configs/run/tournament/example.yaml` は、現行仕様に合わせたトーナメント実行テンプレートです。

`examples/configs/example.yaml` は、TournamentRunConfigの代表的な項目をまとめた包括例です。
主要オプションに詳細なコメントを付けています。

主な設定項目：

- `engines`：対局させるエンジン（artifact 参照または engine_path）
- `tournament.scheduler`：`round_robin` または `gauntlet`
- `rules.time_control`：持ち時間、秒読み、ノード制限など
- `rules.adjudication`：投了判定、引き分け判定
- `instances`：ローカル実行またはリモート SSH 実行

### SPRT (統計的検定)

`examples/configs/run/sprt/example.yaml` は、2 つのエンジンバージョンの差を早期停止付きで検定する例です。

主な設定項目：

- `engines`：2 つのエンジン（baseline と modified）
- `sprt.elo0`：H0 仮説の Elo 差（通常 0.0）
- `sprt.elo1`：H1 仮説の Elo 差（例: 5.0）
- `sprt.alpha`：第一種過誤率（既定: 0.05）
- `sprt.beta`：第二種過誤率（既定: 0.05）
- `sprt.max_games`：最大対局数

結果の解釈：

- H0 棄却 → modified 側が統計的に有意に強い
- H0 受容 → 設定した閾値を超える差は認められない
- max_games 到達 → 結論は出ない（対局数を増やす必要がある）

### SPSA (パラメータチューニング)

`examples/configs/run/spsa/example.yaml` は、エンジンパラメータを自動調整する例です。
勾配を近似する確率的探索によって、良い値を探索します。

主な設定項目：

- `engines`：1 つのエンジン（baseline と tuned で共用）
- `spsa.space`：チューニング対象パラメータの SPSA space 定義ファイル
- `spsa.num_updates`：更新回数
- `spsa.num_parallel`：並列対局数
- `rules.initial_positions`：開始局面ファイル（必須）
- `rules.time_control.node_limit`：ノード数制限（推奨）

YaneuraOuと水匠5ですぐ試す場合は、上記bootstrapが生成する`examples/.runtime/yaneuraou-suisho5/spsa.yaml`を使えます。

### Generate (棋譜生成)

`examples/configs/run/generate/example.yaml` は、自己対局で棋譜と局面データを生成する例です。

主な設定項目：

- `engines`：自己対局に使うエンジン定義
- `tournament.games_per_pair`：生成する対局数
- `rules`：持ち時間、初期局面、adjudication
- `records_output`：sbinpack や psv 形式の出力設定

## リファレンス設定

### example.yaml

`TournamentRunConfig` の主要項目をまとめたリファレンスです。
自分の環境に合わせて必要な項目だけを書き換えて使います。

## 追加リソース

- **詳細ドキュメント**：[https://nyoki-mtl.github.io/ShogiArena/](https://nyoki-mtl.github.io/ShogiArena/)
- **トーナメントガイド**：[User Guide - Tournaments](https://nyoki-mtl.github.io/ShogiArena/user-guide/tournaments.html)
- **SPSA ガイド**：[User Guide - SPSA](https://nyoki-mtl.github.io/ShogiArena/user-guide/spsa.html)
