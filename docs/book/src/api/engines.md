# Engines API

USI（Universal Shogi Interface）エンジンを操作するための API リファレンスです。

## 概要

Shogi Arena は同期・非同期の両方のエンジンインターフェースを提供します。

- **SyncUsiEngine**: 同期的な API。スクリプトやノートブックで使いやすい
- **AsyncUsiEngine**: 非同期 API。高い並行性が必要な場合に使用
- **EngineFactory**: 設定ファイルからエンジンを構築するファクトリ

---

## SyncUsiEngine クラス

同期的にエンジンを操作するクラスです。コンテキストマネージャとして使用できます。

```python
from shogiarena.arena.engines.sync_usi_engine import SyncUsiEngine
```

```python
with SyncUsiEngine.from_config_path("configs/engine/yaneuraou.yaml") as engine:
    result = engine.think(sfen="startpos", request=UsiThinkRequest(byoyomi=1000))
    print(result.bestmove)
```

### ファクトリメソッド

#### from_config_path()

設定ファイルからエンジンを構築します。

```python
@classmethod
def from_config_path(
    cls,
    path: str | Path,
    *,
    timeout: float = 10.0,
    extra_options: dict[str, Any] | None = None,
    engine_name: str | None = None,
    instance_id: str | None = None,
    instance_pool: InstancePool | None = None,
    collect_info_strings: bool = False,
) -> SyncUsiEngine
```

- **path**: `str | Path` エンジン設定ファイルのパス
- **timeout**: `float` (デフォルト: `10.0`) ハンドシェイクタイムアウト（秒）
- **extra_options**: `dict[str, Any] | None` (デフォルト: `None`) 追加の USI オプション
- **engine_name**: `str | None` (デフォルト: `None`) エンジン名を上書き
- **instance_id**: `str | None` (デフォルト: `None`) インスタンス ID
- **instance_pool**: `InstancePool | None` (デフォルト: `None`) インスタンスプール
- **collect_info_strings**: `bool` (デフォルト: `False`) `info string` メッセージの収集を有効化

---

#### from_async_engine()

既存の `AsyncUsiEngine` をラップします。

```python
@classmethod
def from_async_engine(
    cls,
    engine: AsyncUsiEngine,
    *,
    collect_info_strings: bool = False,
) -> SyncUsiEngine
```

- **engine**: `AsyncUsiEngine` ラップ対象のエンジン
- **collect_info_strings**: `bool` (デフォルト: `False`) `info string` メッセージの収集を有効化

---

### 主要メソッド

#### start()

エンジンを起動します（USI ハンドシェイクを実行）。

```python
def start() -> None
```

---

#### close()

エンジンを終了します。

```python
def close() -> None
```

---

#### trigger_isready()

`isready` コマンドを送信し、`readyok` を待機します。

```python
def trigger_isready(timeout: ReadyTimeout = "default") -> None
```

- **timeout**: `float | None | Literal["default"]` (デフォルト: `"default"`) タイムアウト秒数。`None` で無期限待機

---

#### new_game()

`usinewgame` コマンドを送信します。

```python
def new_game() -> None
```

---

#### submit_position()

局面を設定します。

```python
def submit_position(sfen: str, moves: Iterable[str] | None = None) -> None
```

- **sfen**: `str` 局面文字列（`"startpos"` またはSFEN形式）
- **moves**: `Iterable[str] | None` (デフォルト: `None`) 追加の手順

---

#### think()

思考して最善手を取得します。

```python
def think(
    *,
    sfen: str,
    request: UsiThinkRequest,
    moves: Iterable[str] | None = None,
    info_handler: InfoHandler | None = None,
    timeout: float | None = None,
) -> UsiThinkResult
```

- **sfen**: `str` 局面文字列
- **request**: `UsiThinkRequest` 思考リクエスト
- **moves**: `Iterable[str] | None` (デフォルト: `None`) 追加の手順
- **info_handler**: `InfoHandler | None` (デフォルト: `None`) info 行のコールバック
- **timeout**: `float | None` (デフォルト: `None`) タイムアウト秒数

---

#### think_mate()

詰み探索を実行します（`go mate`）。

```python
def think_mate(
    *,
    sfen: str,
    ply_limit: int | None = None,
    node_limit: int | None = None,
    infinite: bool = False,
    moves: Iterable[str] | None = None,
    info_handler: InfoHandler | None = None,
    wait_for_bestmove: bool | None = None,
    timeout: float | None = None,
) -> UsiMateResult
```

- **sfen**: `str` 局面文字列
- **ply_limit**: `int | None` (デフォルト: `None`) 詰み手数の上限
- **node_limit**: `int | None` (デフォルト: `None`) ノード数の上限
- **infinite**: `bool` (デフォルト: `False`) 無限探索
- **moves**: `Iterable[str] | None` (デフォルト: `None`) 追加の手順
- **info_handler**: `InfoHandler | None` (デフォルト: `None`) info 行のコールバック
- **wait_for_bestmove**: `bool | None` (デフォルト: `None`) checkmate 後に bestmove を待つか
- **timeout**: `float | None` (デフォルト: `None`) タイムアウト秒数

---

#### analyze()

解析を開始し、ハンドルを返します。

```python
def analyze(
    *,
    sfen: str,
    request: UsiThinkRequest,
    moves: Iterable[str] | None = None,
    info_handler: InfoHandler | None = None,
) -> SyncAnalysisHandle
```

- **sfen**: `str` 局面文字列
- **request**: `UsiThinkRequest` 思考リクエスト（`infinite=True` が必須）
- **moves**: `Iterable[str] | None` (デフォルト: `None`) 追加の手順
- **info_handler**: `InfoHandler | None` (デフォルト: `None`) info 行のコールバック

---

#### stop()

思考を停止します。

```python
def stop(timeout: float | None = None) -> UsiThinkResult | None
```

- **timeout**: `float | None` (デフォルト: `None`) タイムアウト秒数

---

### Ponder 関連

#### start_ponder()

Ponder を開始します。

```python
def start_ponder(
    *,
    sfen: str,
    request: UsiThinkRequest,
    moves: Iterable[str] | None = None,
    predicted_move: str | None = None,
    info_handler: InfoHandler | None = None,
    enable_early_ponder: bool | None = None,
) -> SyncPonderHandle
```

- **sfen**: `str` 局面文字列
- **request**: `UsiThinkRequest` 思考リクエスト
- **moves**: `Iterable[str] | None` (デフォルト: `None`) 追加の手順
- **predicted_move**: `str | None` (デフォルト: `None`) 予測手
- **info_handler**: `InfoHandler | None` (デフォルト: `None`) info 行のコールバック
- **enable_early_ponder**: `bool | None` (デフォルト: `None`) 早期 Ponder の有効化

---

#### ponder_hit()

Ponder ヒットを送信します。

```python
def ponder_hit(
    *,
    timings: PonderHitTimings | None = None,
    timeout: float | None = None,
) -> UsiThinkResult
```

- **timings**: `PonderHitTimings | None` (デフォルト: `None`) タイミング情報
- **timeout**: `float | None` (デフォルト: `None`) タイムアウト秒数

---

#### cancel_ponder()

Ponder をキャンセルします。

```python
def cancel_ponder(*, timeout: float | None = None) -> UsiThinkResult | None
```

- **timeout**: `float | None` (デフォルト: `None`) タイムアウト秒数

---

## AsyncUsiEngine クラス

非同期でエンジンを操作するクラスです。

```python
from shogiarena.arena.engines.usi_engine import AsyncUsiEngine
```

```python
import asyncio
from shogiarena.arena.engines.engine_factory import EngineFactory
from shogiarena.arena.engines.usi_think import UsiThinkRequest

async def main():
    engine = await EngineFactory.create_engine("configs/engine/yaneuraou.yaml")
    await engine.start()
    result = await engine.think(sfen="startpos", request=UsiThinkRequest(byoyomi=1000))
    print(result.bestmove)
    await engine.close()

asyncio.run(main())
```

### プロパティ

- **state**: `UsiEngineState` 現在の USI 状態
- **name**: `str` エンジン名
- **engine_info**: `dict[str, str]` USI info（`id name` / `id author` など）
- **active_ponder**: `PonderHandle | None` アクティブな Ponder ハンドル
- **is_running**: `bool` エンジンプロセスが稼働中か
- **is_ready**: `bool` `ready` 状態か
- **is_thinking**: `bool` 思考中か

### 主要メソッド

`SyncUsiEngine` と同様のメソッドを非同期で提供します。すべて `await` が必要です。

---

## EngineFactory クラス

設定ファイルからエンジンを構築するファクトリです。

```python
from shogiarena.arena.engines.engine_factory import EngineFactory
```

```python
engine = await EngineFactory.create_engine(
    "configs/engine/yaneuraou.yaml",
    timeout=12.0,
    extra_options={"Threads": 4},
)
```

#### create_engine()

```python
@staticmethod
async def create_engine(
    config_path: Path,
    timeout: float = 10.0,
    extra_options: dict[str, Any] | None = None,
    engine_name: str | None = None,
    instance_id: str | None = None,
    instance_pool: InstancePool | None = None,
    cpu_affinity: Sequence[int] | None = None,
) -> AsyncUsiEngine
```

- **config_path**: `Path` エンジン設定ファイルのパス
- **timeout**: `float` (デフォルト: `10.0`) ハンドシェイクタイムアウト（秒）
- **extra_options**: `dict[str, Any] | None` (デフォルト: `None`) 追加の USI オプション
- **engine_name**: `str | None` (デフォルト: `None`) エンジン名を上書き
- **instance_id**: `str | None` (デフォルト: `None`) インスタンス ID
- **instance_pool**: `InstancePool | None` (デフォルト: `None`) インスタンスプール
- **cpu_affinity**: `Sequence[int] | None` (デフォルト: `None`) CPU アフィニティ

---

## UsiThinkRequest クラス

思考リクエストを定義するデータクラスです。

```python
from shogiarena.arena.engines.usi_think import UsiThinkRequest
```

```python
# 秒読み 1 秒
request = UsiThinkRequest(byoyomi=1000)

# 持ち時間制御
request = UsiThinkRequest(btime=60000, wtime=60000, binc=1000, winc=1000)

# 深さ制限
request = UsiThinkRequest(depth=10)

# ノード制限
request = UsiThinkRequest(nodes=100000)

# 固定時間
request = UsiThinkRequest(movetime=5000)
```

### フィールド

- **movetime**: `int | None` (デフォルト: `None`) 1手あたりの固定時間（ミリ秒）
- **btime**: `int | None` (デフォルト: `None`) 先手の残り時間（ミリ秒）
- **wtime**: `int | None` (デフォルト: `None`) 後手の残り時間（ミリ秒）
- **binc**: `int | None` (デフォルト: `None`) 先手のインクリメント（ミリ秒）
- **winc**: `int | None` (デフォルト: `None`) 後手のインクリメント（ミリ秒）
- **byoyomi**: `int | None` (デフォルト: `None`) 秒読み（ミリ秒）
- **depth**: `int | None` (デフォルト: `None`) 探索深さ制限
- **nodes**: `int | None` (デフォルト: `None`) ノード数制限
- **infinite**: `bool` (デフォルト: `False`) 無限探索
- **ponder**: `bool` (デフォルト: `False`) Ponder モード
- **searchmoves**: `tuple[str, ...]` (デフォルト: `()`) 探索する手を制限

### メソッド

#### to_command()

`go` コマンド文字列を生成します。

```python
def to_command() -> str
```

---

#### with_limits()

制限を変更したコピーを返します。

```python
def with_limits(*, depth: int | None = None, nodes: int | None = None) -> UsiThinkRequest
```

- **depth**: `int | None` (デフォルト: `None`) 新しい探索深さ制限
- **nodes**: `int | None` (デフォルト: `None`) 新しいノード数制限

---

## UsiThinkResult クラス

`go` コマンドの思考結果を保持するクラスです。最善手（bestmove）と、思考中に収集された PV 情報を格納します。

```python
from shogiarena.arena.engines.usi_types import UsiThinkResult
```

### フィールド

- **bestmove**: `str | None` 最善手（USI 形式）
- **ponder**: `str | None` Ponder 手
- **pvs**: `list[UsiThinkPV]` 思考中に収集された PV のリスト
- **info_strings**: `tuple[str, ...]` (デフォルト: `()`) 探索中に受信した `info string` メッセージ。`collect_info_strings=True` でエンジンを構築した場合のみ収集される

評価値や探索統計（`eval`, `depth`, `nodes` など）は `pvs` 内の `UsiThinkPV` オブジェクトに格納されています。ヘルパーメソッド `get_last_pv()` を使用してアクセスできます。

### メソッド

#### get_last_pv()

指定した MultiPV インデックスの最新 PV を取得します。

```python
def get_last_pv(multipv_index: int = 1) -> UsiThinkPV | None
```

- **multipv_index**: `int` (デフォルト: `1`) 取得する MultiPV のインデックス（1始まり）

---

#### to_debug_string()

デバッグ用のコンパクトな文字列を生成します。

```python
def to_debug_string() -> str
```

---

#### to_usi_string()

USI 形式の `bestmove ... [ponder ...]` 文字列を生成します。`bestmove` が未設定の場合は `ValueError` を送出します。

```python
def to_usi_string() -> str
```

---

#### from_string()

USI の `bestmove ...` 行をパースして `UsiThinkResult` を生成します。

```python
@classmethod
def from_string(bestmove_line: str) -> UsiThinkResult
```

- **bestmove_line**: `str` パース対象の bestmove 行

---

## UsiThinkPV クラス

USI の `info ...` 行をパースした結果を保持するクラスです。評価値、探索統計、読み筋（PV）などの情報を格納します。`UsiThinkResult.pvs` の要素として使用されます。

```python
from shogiarena.arena.engines.usi_types import UsiThinkPV
```

### フィールド

- **eval**: `UsiEvalValue | None` 評価値
- **bound**: `UsiBound` (デフォルト: `UsiBound.NONE`) 評価値のバウンド（上限/下限）
- **depth**: `int | None` 探索深さ
- **seldepth**: `int | None` 選択的深さ
- **nodes**: `int | None` 探索ノード数
- **time**: `int | None` 思考時間（ミリ秒）
- **nps**: `int | None` NPS（Nodes Per Second）
- **hashfull**: `int | None` ハッシュ使用率（パーミル、0-1000）
- **multipv**: `int | None` MultiPV 行番号（1始まり）
- **pv**: `list[str] | None` 読み筋（USI 形式の手のリスト）
- **string**: `str | None` カスタム info 文字列

---

## UsiMateResult クラス

詰み探索（`go mate`）の結果を保持するデータクラスです。

```python
from shogiarena.arena.engines.usi_engine import UsiMateResult
```

### フィールド

- **is_mate**: `bool` 詰みが見つかったか
- **moves**: `tuple[str, ...]` (デフォルト: `()`) 詰み手順
- **mate_in_ply**: `int | None` (デフォルト: `None`) 詰み手数
- **pvs**: `tuple[UsiThinkPV, ...]` (デフォルト: `()`) `info` から収集した PV 群
- **info_strings**: `tuple[str, ...]` (デフォルト: `()`) 探索中に受信した `info string` メッセージ。`collect_info_strings=True` でエンジンを構築した場合のみ収集される

### メソッド

#### get_last_pv()

指定した MultiPV インデックスの最新 PV を取得します。

```python
def get_last_pv(multipv_index: int = 1) -> UsiThinkPV | None
```

- **multipv_index**: `int` (デフォルト: `1`) 取得する MultiPV のインデックス（1始まり）

---

## 使用例

### 基本的な思考

```python
from shogiarena.arena.engines.sync_usi_engine import SyncUsiEngine
from shogiarena.arena.engines.usi_think import UsiThinkRequest

with SyncUsiEngine.from_config_path("engine.yaml") as engine:
    # 初期局面から思考
    result = engine.think(
        sfen="startpos",
        request=UsiThinkRequest(byoyomi=1000)
    )
    print(f"最善手: {result.bestmove}")

    # 評価値や読み筋は pvs 経由でアクセス
    pv = result.get_last_pv()
    if pv is not None:
        print(f"評価値: {pv.eval}")
        print(f"読み筋: {' '.join(pv.pv or [])}")
```

### 詰み探索

```python
with SyncUsiEngine.from_config_path("engine.yaml") as engine:
    # 手数制限での詰み探索
    result = engine.think_mate(
        sfen="ln1gkg1nl/6+P2/2sppps1p/2p3p2/p8/P1P1P3P/2NP1PP2/3s1KSR1/L1+b2G1NL w R2Pbgp 1",
        ply_limit=15
    )
    if result.is_mate:
        print(f"{result.mate_in_ply}手詰み: {' '.join(result.moves)}")

    # ノード制限
    node_limited = engine.think_mate(sfen="startpos", node_limit=200000)
    print(node_limited.is_mate)

    # go mate infinite + checkmate後bestmove待ち
    stable = engine.think_mate(
        sfen="startpos",
        infinite=True,
        wait_for_bestmove=True,
        timeout=10.0,
    )
    print(stable.is_mate)
```

### 解析モード

```python
with SyncUsiEngine.from_config_path("engine.yaml") as engine:
    # 無限解析を開始
    handle = engine.analyze(
        sfen="startpos moves 7g7f",
        request=UsiThinkRequest(infinite=True)
    )

    # 5秒後に停止
    import time
    time.sleep(5)
    result = engine.stop()
    if result is not None:
        pv = result.get_last_pv()
        if pv is not None:
            print(f"評価値: {pv.eval}")
```

### info string の収集（デバッグ用）

エンジンが送信する `info string` メッセージを収集し、あとからフィルタリングできます。
`collect_info_strings=True` を指定すると、各探索（go → bestmove）の間に受信した全ての `info string` が
結果オブジェクトの `info_strings` フィールドに格納されます。

```python
with SyncUsiEngine.from_config_path(
    "engine.yaml",
    collect_info_strings=True,  # info string 収集を有効化
) as engine:
    result = engine.think(
        sfen="startpos",
        request=UsiThinkRequest(byoyomi=1000),
    )

    # 探索中のデバッグメッセージをフィルタリング
    for msg in result.info_strings:
        if "error" in msg.lower():
            print(f"[ENGINE DEBUG] {msg}")

    # 詰み探索でも同様に利用可能
    mate_result = engine.think_mate(sfen=sfen, ply_limit=15)
    print(f"info string count: {len(mate_result.info_strings)}")
```

**注意事項**:

- デフォルトでは無効（`collect_info_strings=False`）
- 有効時は探索中の全 `info string` をメモリに保持するため、デバッグ目的でのみ使用推奨
- 各探索の開始時に自動でクリアされるため、手動クリアは不要

---

## 関連ドキュメント

- [エンジン設定](../user-guide/engine-configuration.md) - 設定ファイルの書き方
- [USI プロトコル](../technical/usi-engine.md) - USI 実装の詳細
