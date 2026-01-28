# USI Engine の設計

Shogi Arena の USI エンジン実装の設計思想と内部構造を説明します。

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

## クラス構成

### AsyncUsiEngine

非同期 USI エンジンのコア実装です。

**主要メソッド**:

```python
class AsyncUsiEngine:
    async def start() -> None
        """エンジンプロセスを起動し、USI初期化"""

    async def trigger_isready(timeout: float) -> None
        """isready コマンドを送り、readyok を待機"""

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

    async def think_mate(sfen: str, ply_limit: int) -> UsiMateResult:
        """go mate で詰め探索"""

    async def analyze(...) -> AnalysisHandle:
        """go infinite で無限解析開始"""

    async def stop(timeout: float) -> UsiThinkResult | None:
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
    time_ms: int | None = None          # 持ち時間（ミリ秒）
    increment_ms: int | None = None     # フィッシャーインクリメント
    byoyomi_ms: int | None = None       # 秒読み
    depth_limit: int | None = None      # 深さ制限
    node_limit: int | None = None       # ノード数制限
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
def info_handler(info: dict[str, Any]) -> None:
    """info 行のパース結果を受け取る"""
    if "score_cp" in info:
        print(f"評価値: {info['score_cp']} cp")
    if "pv" in info:
        print(f"読み筋: {' '.join(info['pv'])}")

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
