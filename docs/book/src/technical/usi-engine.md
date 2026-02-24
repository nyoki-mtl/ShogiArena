# USI Engine の設計

Shogi Arena の USI エンジン実装の設計思想と内部構造を説明します。

**関連ドキュメント**:
- **[エンジンラッパーのレイヤー設計](engine-layers.md)** - 各レイヤーの詳細な説明（必読）
- **[Engine API リファレンス](../api/engines.md)** - API の詳細仕様
- **[Python Library Guide](../user-guide/python-library.md)** - 実際の使用例

## 設計の哲学

### 1. 堅牢性優先

- すべてのエンジン操作にタイムアウトを設定
- 予期しないエンジン応答への対処
- エラーハンドリングを徹底
- プロセスのクラッシュからの回復

### 2. 再利用性

- トーナメントシステムだけでなく、外部ライブラリとしても使用可能
- `SyncUsiEngine` で同期的な API を提供
- 設定ファイルまたはプログラムから直接初期化可能

### 3. 観測可能性

- すべての USI 通信をログに記録
- 思考時間、ノード数などのメトリクスを収集
- デバッグしやすい詳細なログ出力

### 4. テスタビリティ

- モックエンジンで簡単にテスト可能
- 依存関係の注入をサポート
- 各メソッドが独立してテスト可能

### 5. レイヤー化アーキテクチャ

エンジン実装は明確な責務を持つ6つのレイヤーで構成されています：

1. **AsyncUSIProcessBridgeProtocol** - プロセス起動の抽象インターフェース
2. **SpawnerBackedUSIBridge** - ローカル/SSH対応の具体実装
3. **AsyncUsiProcess** - プロセス制御の軽量ラッパー
4. **AsyncUsiEngine** - USIセッション管理
5. **SyncUsiEngine** - 同期ラッパー
6. **EngineFactory** - 設定からの構築

詳細は [エンジンラッパーのレイヤー設計](engine-layers.md) を参照してください。

## クラス構成

### AsyncUsiEngine

非同期 USI エンジンのコア実装です。

**主要メソッド**:

```python
class AsyncUsiEngine:
    async def start() -> None
        """エンジンプロセスを起動し、USI初期化"""

    async def trigger_isready(timeout: float | None | Literal["default"] = "default") -> None
        """isready コマンドを送り、readyok を待機（None で無期限）"""

    async def new_game() -> None
        """usinewgame コマンドを送信"""

    async def submit_position(sfen: str, moves: tuple[str, ...]) -> None
        """position コマンドで局面を設定"""

    async def think(
        sfen: str,
        request: UsiThinkRequest,
        info_handler: InfoHandler | None
    ) -> UsiThinkResult:
        """go コマンドで思考開始、bestmove を待機"""

    async def think_mate(
        sfen: str,
        ply_limit: int | None = None,
        node_limit: int | None = None,
        infinite: bool = False,
        info_handler: InfoHandler | None = None,
        wait_for_bestmove: bool | None = None,
        timeout: float | None = None
    ) -> UsiMateResult:
        """go mate で詰め探索（手数/ノード/infinite 指定対応）"""

    async def analyze(...) -> AnalysisHandle:
        """go infinite で無限解析開始"""

    async def stop(timeout: float | None = None) -> UsiThinkResult | None:
        """stop コマンドで思考を停止"""

    async def close() -> None:
        """quit コマンドでエンジンを終了"""
```

**内部構造**:

- `subprocess.Popen` でエンジンプロセスを管理
- `asyncio.Queue` で USI レスポンスを非同期処理
- `asyncio.create_task` でタイムアウト管理

### SyncUsiEngine

`AsyncUsiEngine` の同期的ラッパーです。

**特徴**:

- 内部で専用の `asyncio` イベントループを持つ
- バックグラウンドスレッドでループを実行
- `async`/`await` を使わずに USI エンジンを操作可能

**使用例**:

```python
with SyncUsiEngine.from_config_path("config.yaml") as engine:
    result = engine.think(sfen=sfen, request=request)
    print(result.bestmove)
```

詳細は [Python Library](../user-guide/python-library.md) を参照。

### EngineFactory

エンジンの生成と初期化を一元管理します。

**役割**:

- 設定ファイルから `AsyncUsiEngine` を生成
- アーティファクトのビルドとキャッシュ
- インスタンスプールとの連携（SSH/ローカル）

**Factory パターンの利点**:

- エンジン生成ロジックを一箇所に集約
- ビルドキャッシュの管理
- テスト時にモックエンジンに差し替え可能

## USI プロトコル実装

### コマンド送信

```python
async def _send_command(self, cmd: str) -> None:
    """USI コマンドを送信"""
    self._log_usi_out(cmd)
    self._proc.stdin.write(f"{cmd}\n".encode("utf-8"))
    await self._proc.stdin.drain()
```

### レスポンス受信

```python
async def _read_responses(self) -> None:
    """エンジンからの出力を非同期で読み取り"""
    while True:
        line = await self._proc.stdout.readline()
        if not line:
            break
        decoded = line.decode("utf-8").strip()
        self._log_usi_in(decoded)
        await self._response_queue.put(decoded)
```

### タイムアウト管理

```python
async def _wait_for_response(self, timeout: float) -> str:
    """タイムアウト付きでレスポンスを待機"""
    try:
        return await asyncio.wait_for(
            self._response_queue.get(),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        raise TimeoutError(f"Engine {self.name} did not respond within {timeout}s")
```

## 思考制御

### UsiThinkRequest

思考リクエストのパラメータを保持します。

```python
@dataclass
class UsiThinkRequest:
    movetime: int | None = None         # 固定時間（ミリ秒）
    btime: int | None = None            # 先手の残り時間（ミリ秒）
    wtime: int | None = None            # 後手の残り時間（ミリ秒）
    binc: int | None = None             # 先手のインクリメント（ミリ秒）
    winc: int | None = None             # 後手のインクリメント（ミリ秒）
    byoyomi: int | None = None          # 秒読み（ミリ秒）
    depth: int | None = None            # 探索深さ制限
    nodes: int | None = None            # ノード数制限
    infinite: bool = False              # 無限解析
```

### UsiThinkResult

思考結果を保持します。

```python
@dataclass
class UsiThinkResult:
    bestmove: str               # 最善手（USI形式）
    ponder: str | None          # 先読み手
    score_cp: int | None        # 評価値（cp）
    score_mate: int | None      # 詰み手数
    depth: int | None           # 探索深さ
    nodes: int | None           # 探索ノード数
    time_ms: int | None         # 思考時間
    pv: list[str]               # 読み筋（Principal Variation）
```

## 情報ハンドラ

思考中の情報（`info` 行）をリアルタイムで受け取るためのコールバックです。

```python
def info_handler(pv: UsiThinkPV) -> None:
    """info 行のパース結果を受け取る"""
    if pv.eval is not None and pv.eval.cp is not None:
        print(f"評価値: {pv.eval.cp} cp")
    if pv.pv:
        print(f"読み筋: {' '.join(pv.pv)}")

result = await engine.think(
    sfen=sfen,
    request=request,
    info_handler=info_handler  # コールバックを設定
)
```

**パース対象**:

- `score cp X`: 評価値
- `score mate X`: 詰み手数
- `depth X`: 探索深さ
- `nodes X`: ノード数
- `pv ...`: 読み筋
- `time X`: 経過時間
- `multipv X`: MultiPV
- `string ...`: デバッグ文字列（`collect_info_strings` で収集可能）

## info string の収集

エンジンが送信する `info string ...` メッセージをデバッグ目的で収集する機能です。

### 仕組み

`collect_info_strings=True` でエンジンを構築すると、各探索（go → bestmove/checkmate/nomate/timeout）の間に受信した全ての `info string` が内部バッファに蓄積されます。探索終了時にスナップショットが結果オブジェクト（`UsiThinkResult.info_strings` / `UsiMateResult.info_strings`）に付与され、バッファは次の探索開始時に自動クリアされます。

```
go byoyomi 1000
  ├─ info string debug: hash collision detected    ← 収集
  ├─ info depth 10 score cp 100 pv 7g7f 3c3d      ← 通常の info（収集対象外）
  ├─ info string debug: eval cache hit rate 95%    ← 収集
  └─ bestmove 7g7f ponder 3c3d

結果: info_strings = ("debug: hash collision detected", "debug: eval cache hit rate 95%")
```

### 有効化

```python
# AsyncUsiEngine
engine = AsyncUsiEngine(config=config, bridge=bridge, collect_info_strings=True)

# SyncUsiEngine（コンストラクタ / ファクトリメソッド）
engine = SyncUsiEngine(async_engine, collect_info_strings=True)
engine = SyncUsiEngine.from_config_path("engine.yaml", collect_info_strings=True)
```

### 設計の特徴

- **探索スコープ**: info_strings は go → bestmove の単位で区切られる
- **結果オブジェクトに付与**: `think()` / `think_mate()` の返り値から直接参照
- **自動クリア**: 次の探索開始時に自動リセット
- **オプトイン**: デフォルト無効。有効時のみメモリを消費

## エラーハンドリング

### タイムアウト

```python
try:
    result = await engine.think(sfen=sfen, request=request, timeout=10.0)
except asyncio.TimeoutError:
    logger.error("Engine did not respond in time")
    # エンジンを強制停止
    await engine.stop()
```

### プロセスクラッシュ

```python
if self._proc.returncode is not None:
    raise RuntimeError(f"Engine process {self.name} has terminated unexpectedly")
```

### 不正なレスポンス

```python
if not response.startswith("bestmove"):
    logger.warning(f"Unexpected response: {response}")
    # 再試行またはエラーとして扱う
```

## パフォーマンス最適化

### プロセスの再利用

対局ごとにエンジンを再起動せず、`usinewgame` で初期化：

```python
await engine.new_game()  # プロセスは維持したまま初期化
```

### 非同期 I/O

すべての I/O 操作を非同期で実行：

```python
# ブロッキングしない
await proc.stdin.drain()
line = await proc.stdout.readline()
```

### バッファリング

USI 通信をバッファリングして効率化：

```python
self._response_queue = asyncio.Queue(maxsize=1000)
```

## テスト戦略

### モックエンジン

```python
class MockEngine(AsyncUsiEngine):
    async def think(self, **kwargs) -> UsiThinkResult:
        return UsiThinkResult(
            bestmove="7g7f",
            score_cp=100,
            pv=["7g7f", "3c3d"]
        )
```

### 統合テスト

```python
@pytest.mark.asyncio
async def test_engine_lifecycle():
    engine = await EngineFactory.create_engine("config.yaml")
    await engine.start()
    result = await engine.think(sfen=STARTPOS, request=request)
    assert result.bestmove
    await engine.close()
```

## 次のステップ

- **[Runner と Orchestrator](runners-orchestrators.md)** - エンジンを使った対局管理
- **[Python Library](../user-guide/python-library.md)** - 実際の使用例
- **[API Reference - Engine API](../api/engines.md)** - 詳細な API ドキュメント
