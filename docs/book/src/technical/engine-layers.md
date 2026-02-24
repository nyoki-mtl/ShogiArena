# エンジンラッパーのレイヤー設計

このドキュメントでは、ShogiArenaのUSIエンジン実装における各レイヤーの役割と責務を詳しく説明します。

## 設計思想

エンジンラッパーは、**責務の明確な分離**と**拡張性**を重視した階層設計になっています。各レイヤーは特定の責務のみを持ち、上位レイヤーは下位レイヤーの実装詳細を知る必要がありません。

### 設計原則

1. **単一責任の原則**: 各レイヤーは一つの関心事のみを扱う
2. **依存性の逆転**: インターフェースに依存し、実装に依存しない
3. **拡張性**: 新しい起動方式やプロトコルを容易に追加できる
4. **テスタビリティ**: 各レイヤーを独立してテスト可能

## レイヤー構成

```
┌─────────────────────────────────────────────────┐
│           6. EngineFactory                      │  設定から全体を構築
│           (engine_factory.py)                   │  アーティファクト解決
└─────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│           5. SyncUsiEngine                      │  同期API提供
│           (sync_usi_engine.py)                  │  バックグラウンドループ
└─────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│           4. AsyncUsiEngine                     │  USIセッション管理
│           (usi_engine.py)                       │  ハンドシェイク、思考制御
└─────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│           3. AsyncUsiProcess                    │  プロセス制御
│           (usi_process.py)                      │  ライフサイクル管理
└─────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│  2. SpawnerBackedUSIBridge (実装)              │  ローカル/SSH起動
│     (usi_bridge_spawner.py)                     │  EngineProcessSpawner使用
└─────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│  1. AsyncUSIProcessBridgeProtocol (IF)         │  プロセス起動の抽象化
│     (usi_bridge.py)                             │  I/O操作の定義
└─────────────────────────────────────────────────┘
```

## 各レイヤーの詳細

### Layer 1: AsyncUSIProcessBridgeProtocol

**ファイル**: `usi_bridge.py`

**役割**: プロセス起動とI/O操作の抽象インターフェース

**責務**:
- プロセス起動・停止の抽象メソッド定義
- コマンド送信とレスポンス受信のインターフェース提供
- プロセス状態確認のメソッド定義

**実装が必要なメソッド**:

```python
@runtime_checkable
class AsyncUSIProcessBridgeProtocol(Protocol):
    @property
    def name(self) -> str:
        """エンジン名を返す（ログと診断用）"""

    async def start_process(self) -> None:
        """エンジンプロセスを起動してI/Oを準備"""

    async def stop_process(self) -> None:
        """エンジンプロセスを終了してリソースを解放"""

    async def send_line(self, command: str) -> None:
        """USIコマンドを1行送信（末尾改行なし）"""

    def receive_lines(self) -> AsyncIterator[str]:
        """プロセスのstdoutから行を非同期イテレート"""

    def is_running(self) -> bool:
        """プロセスが生きていてI/O可能ならTrue"""
```

**特徴**:
- Protocolを使った構造的サブタイピング（Structural Subtyping）
- 実装クラスは明示的な継承不要（Duck Typing）
- 起動手段の詳細（ローカル/SSH/Docker等）を完全に隠蔽

### Layer 2: SpawnerBackedUSIBridge

**ファイル**: `usi_bridge_spawner.py`

**役割**: `AsyncUSIProcessBridgeProtocol`の具体的な実装

**責務**:
- `EngineProcessSpawner`を使ってプロセスを起動
- ローカル/SSHの両方に対応
- stderr出力の監視とログ記録
- プロセスの優雅な終了（quit送信→タイムアウト→kill）

**初期化パラメータ**:

```python
class SpawnerBackedUSIBridge:
    def __init__(
        self,
        *,
        instance: Instance,           # 実行インスタンス（ローカルorSSH）
        engine_path: str,             # エンジンバイナリのパス
        working_dir: str | None,      # 作業ディレクトリ
        name: str | None,             # エンジン名（自動生成可能）
        engine_args: list[str] | None,  # コマンドライン引数
        env: dict[str, str] | None,   # 環境変数
        cpu_affinity: tuple[int, ...] | None,  # CPUアフィニティ
    )
```

**動作の流れ**:

1. **起動** (`start_process`):
   - `EngineProcessSpawner.spawn()`でプロセス作成
   - stderr監視タスクを起動
   - プロセスIDをログ出力

2. **停止** (`stop_process`):
   - stderr監視タスクをキャンセル
   - `quit`コマンドを送信
   - 5秒待機、タイムアウトならkill
   - リソースをクリーンアップ

3. **送信** (`send_line`):
   - stdinにUTF-8エンコードして書き込み
   - drain()で送信完了を待機
   - BrokenPipeErrorならstop_processを呼ぶ

4. **受信** (`receive_lines`):
   - stdoutから非同期に行を読み取り
   - UTF-8デコードして返す

**エラーハンドリング**:
- 二重起動のチェック（`_stopping`フラグ）
- パイプ破損時の適切なエラー伝播
- プロセス終了時の確実なクリーンアップ

### Layer 3: AsyncUsiProcess

**ファイル**: `usi_process.py`

**役割**: プロセス制御とI/Oの軽量ラッパー

**責務**:
- ブリッジのライフサイクル管理
- 起動状態の追跡とバリデーション
- I/O操作の前提条件チェック
- **プロトコル知識は持たない**（上位レイヤーの責務）

**主要メソッド**:

```python
class AsyncUsiProcess:
    def __init__(self, bridge: AsyncUSIProcessBridgeProtocol)

    async def start(self) -> None:
        """プロセスを1度だけ起動（二重起動を防ぐ）"""

    async def stop(self) -> None:
        """プロセスを停止（未起動ならエラー）"""

    def is_running(self) -> bool:
        """ブリッジが生きているか確認"""

    async def send_line(self, command: str) -> None:
        """コマンド送信（プロセス停止中ならエラー）"""

    def receive_lines(self) -> AsyncIterator[str]:
        """レスポンス受信（プロセス停止中ならエラー）"""
```

**設計の特徴**:
- **状態機械**: `_state_lock`で起動/停止の排他制御
- **前提条件の強制**: I/O操作前に`is_running()`をチェック
- **シンプルなAPI**: ブリッジの複雑さを隠蔽し、上位レイヤーに簡潔なAPIを提供

**Layer 2との違い**:
- Layer 2 (Bridge): プロセス起動の**実装**
- Layer 3 (Process): プロセス制御の**状態管理**

### Layer 4: AsyncUsiEngine

**ファイル**: `usi_engine.py`

**役割**: 高レベルのUSIセッション管理

**責務**:
- USIプロトコルのハンドシェイク（usi → usiok）
- エンジンオプションの設定（setoption）
- 思考セッションの制御（go → bestmove）
- 解析セッションの制御（go infinite → stop）
- info行のパースとコールバック呼び出し

**主要メソッド**:

```python
class AsyncUsiEngine:
    def __init__(self, process: AsyncUsiProcess, config: UsiEngineConfig)

    async def start(self) -> None:
        """
        エンジンを起動してUSI初期化を実行
        - プロセス起動
        - 'usi'コマンド送信
        - 'id name/author', 'option'の受信
        - 'usiok'待機
        - オプション設定（setoption）
        - 'isready' → 'readyok'確認
        """

    async def new_game(self) -> None:
        """usinewgameコマンドを送信（対局の初期化）"""

    async def submit_position(self, sfen: str, moves: tuple[str, ...]) -> None:
        """positionコマンドで局面を設定"""

    async def think(
        self,
        sfen: str,
        request: UsiThinkRequest,
        info_handler: InfoHandler | None = None,
    ) -> UsiThinkResult:
        """
        思考を実行
        - position設定
        - goコマンド送信
        - info行をハンドラに渡す
        - bestmove待機
        - 結果を返す
        """

    async def analyze(
        self,
        sfen: str,
        moves: tuple[str, ...] = (),
        info_handler: InfoHandler | None = None,
    ) -> AnalysisHandle:
        """無限解析を開始（go infinite）、ハンドルを返す"""

    async def stop(self, timeout: float = 5.0) -> UsiThinkResult | None:
        """stopコマンドで思考を中断"""

    async def close(self) -> None:
        """quitコマンドでエンジンを終了"""
```

**内部構造**:

```python
class AsyncUsiEngine:
    _process: AsyncUsiProcess         # プロセスラッパー
    _config: UsiEngineConfig          # エンジン設定
    _parser: UsiProtocolParser        # USIレスポンスのパーサ
    _active_requests: dict[int, ...]  # 進行中の思考/解析リクエスト
    _response_task: asyncio.Task      # レスポンス受信タスク
```

**思考フロー**:

```
1. submit_position("startpos", moves=("7g7f",))
   → "position startpos moves 7g7f"

2. think(sfen, request=UsiThinkRequest(byoyomi=1000))
   → "go byoyomi 1000"

3. info行を受信
   → info_handlerコールバック呼び出し
   → UsiProtocolParserでパース

4. bestmove受信
   → UsiThinkResult生成
   → 最新のPV情報も含める
```

**エラーハンドリング**:
- タイムアウト管理（各操作にデフォルトタイムアウト）
- プロセスクラッシュ検出
- 不正なレスポンスのログ記録

### Layer 5: SyncUsiEngine

**ファイル**: `sync_usi_engine.py`

**役割**: `AsyncUsiEngine`の同期的ラッパー

**責務**:
- 専用のasyncioイベントループをバックグラウンドスレッドで実行
- 同期コードから非同期APIを呼び出し可能にする
- コンテキストマネージャでリソース管理

**動作原理**:

```python
class SyncUsiEngine:
    def __init__(self, engine: AsyncUsiEngine):
        self._engine = engine
        self._loop = asyncio.new_event_loop()  # 専用ループ
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"sync-usi-{engine.name}",
            daemon=True
        )
        self._thread.start()  # バックグラウンドでループを実行

    def _run_loop(self) -> None:
        """バックグラウンドスレッドでイベントループを実行"""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_coroutine(self, coro: Coroutine[Any, Any, T]) -> T:
        """コルーチンを専用ループで実行して結果を取得"""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()
```

**主要メソッド**:

```python
class SyncUsiEngine:
    # 同期的なAPI（内部でasyncを呼ぶ）
    def start(self) -> None:
        self._run_coroutine(self._engine.start())

    def think(
        self,
        sfen: str,
        request: UsiThinkRequest,
        info_handler: InfoHandler | None = None,
    ) -> UsiThinkResult:
        return self._run_coroutine(
            self._engine.think(sfen, request, info_handler)
        )

    def close(self) -> None:
        self._run_coroutine(self._engine.close())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        self._loop.close()
```

**使用例**:

```python
# from_config_pathで構築
with SyncUsiEngine.from_config_path("config.yaml") as engine:
    result = engine.think(
        sfen="startpos",
        request=UsiThinkRequest(byoyomi=1000)
    )
    print(result.bestmove)
# コンテキスト終了時に自動でclose()

# 既存のAsyncUsiEngineをラップ
async_engine = await EngineFactory.create_engine(config_path)
sync_engine = SyncUsiEngine.from_async_engine(async_engine)
sync_engine.start()
result = sync_engine.think(sfen, request)
sync_engine.close()
```

**利点**:
- **既存の同期コードとの互換性**: `async`/`await`を使わずにエンジンを操作
- **スレッドセーフ**: 専用ループで他の非同期処理と干渉しない
- **簡潔なAPI**: コンテキストマネージャで自動リソース管理

**制約**:
- イベントループが別スレッドなので若干のオーバーヘッド
- デバッグ時にスタックトレースが複雑になる可能性

### Layer 6: EngineFactory

**ファイル**: `engine_factory.py`

**役割**: 設定ファイルから`AsyncUsiEngine`を構築するファクトリ

**責務**:
- YAML設定の読み込みと正規化（`UsiEngineConfig`）
- アーティファクトの解決（ビルドシステム連携）
- リモート実行時のプロビジョニング
- パスベースのUSIオプションの書き換え（リモート実行用）
- インスタンスプールとの連携

**主要メソッド**:

```python
class EngineFactory:
    @staticmethod
    async def create_engine(
        config_path: Path,
        timeout: float = 10.0,
        extra_options: dict[str, Any] | None = None,  # 実行時オプション上書き
        engine_name: str | None = None,
        instance_id: str | None = None,               # 実行先インスタンス指定
        instance_pool: InstancePool | None = None,    # インスタンスプール
        cpu_affinity: Sequence[int] | None = None,    # CPUアフィニティ
    ) -> AsyncUsiEngine:
        """設定ファイルから完全に初期化されたAsyncUsiEngineを生成"""
```

**構築フロー**:

```
1. 設定ファイルの読み込み（UsiEngineConfig.from_file）
   ├─ YAMLをパース
   ├─ パスを解決（engine_dir, output_dir基準）
   └─ オーバーレイ適用

2. インスタンス決定
   ├─ instance_id指定あり → プールから取得
   ├─ instance_pool指定あり → プールから割り当て
   └─ なし → ローカルインスタンス

3. エンジンバイナリの解決
   ├─ engine_path指定あり → そのまま使用
   └─ artifact指定あり:
       ├─ ArtifactResolver.resolve()
       ├─ ビルドシステム実行（必要に応じて）
       └─ 成果物パスを取得

4. リモート実行の準備（SSHインスタンスの場合）
   ├─ Provisioner.provision_files()
   │   ├─ ローカルバイナリをリモートへ転送
   │   └─ 実行権限を付与
   ├─ パスベースオプションの書き換え
   │   ├─ BookFile, EvalDir等をリモートパスに変換
   │   └─ ファイルも転送
   └─ リモートディレクトリの作成

5. SpawnerBackedUSIBridgeの構築
   ├─ Instance, engine_path, working_dir設定
   ├─ 環境変数、コマンドライン引数
   └─ CPUアフィニティ

6. AsyncUsiProcessでラップ

7. AsyncUsiEngineを生成
   ├─ プロセスと設定を渡す
   └─ start()は呼ばない（呼び出し側の責任）
```

**アーティファクト解決の例**:

```yaml
# config.yaml
name: "YaneuraOu"
artifact:
  type: makefile
  repository: yaneurao/YaneuraOu
  ref: v7.6.3
  source_subdir: source
  target: normal
  edition: YANEURAOU_ENGINE_NNUE
  compiler: clang++
  arch: AVX2
options:
  Threads: 4
  Hash: 256
```

```python
# EngineFactory内部の動作
1. ArtifactResolver.resolve(artifact_config)
   → ビルドシステムが "/path/to/build/YaneuraOu-AVX2" を返す

2. ローカル実行なら: そのまま使用

3. SSHリモート実行なら:
   - Provisioner.provision_files()
   - ローカル: /path/to/build/YaneuraOu-AVX2
   - リモート: /home/user/.shogiarena/engines/YaneuraOu-AVX2
   - リモートパスでSpawnerBackedUSIBridgeを構築
```

**オプション書き換えの例**:

```python
# ローカル実行時のオプション
options = {
    "BookFile": "/home/user/.shogiarena/books/standard.db",
    "EvalDir": "/home/user/.shogiarena/eval/nn.bin",
    "Threads": 4,
}

# SSHリモート実行時（_rewrite_options_for_remote後）
options = {
    "BookFile": "/home/remote/.shogiarena/books/standard.db",  # 転送済み
    "EvalDir": "/home/remote/.shogiarena/eval/nn.bin",        # 転送済み
    "Threads": 4,  # 値は書き換えない
}
```

**並行制御**:
- `_ensure_dir_locks`: リモートディレクトリ作成の排他制御
- `_ensure_file_locks`: ファイル転送の排他制御
- `_ensure_binary_locks`: バイナリビルドの排他制御

これにより、複数エンジンの並行構築時に同じファイルを二重転送しない。

## レイヤー間のデータフロー

### 起動時のフロー

```
User Code
  ↓
  SyncUsiEngine.from_config_path(config_path)
  │
  └→ [新スレッド] EngineFactory.create_engine(config_path)
      ↓
      1. UsiEngineConfig.from_file() でYAML読み込み
      2. ArtifactResolver.resolve() でバイナリ取得
      3. Provisioner.provision_files() でリモート転送（SSH時）
      4. SpawnerBackedUSIBridge構築
         ├─ instance: Instance (Local or SSH)
         ├─ engine_path: str
         ├─ working_dir: str
         └─ options: dict
      5. AsyncUsiProcess(bridge)
      6. AsyncUsiEngine(process, config)
      ↓
  SyncUsiEngine(async_engine)
  │
  └→ 専用イベントループ起動（バックグラウンドスレッド）

User Code
  ↓
  sync_engine.start()
  │
  └→ [イベントループ] async_engine.start()
      ↓
      AsyncUsiProcess.start()
      ↓
      SpawnerBackedUSIBridge.start_process()
      ↓
      EngineProcessSpawner.spawn(instance, engine_path, ...)
      ├─ Local: subprocess.Popen(...)
      └─ SSH: SSHTransport.exec_command(...)
      ↓
      USIハンドシェイク（usi → usiok）
      オプション設定（setoption）
      isready確認（isready → readyok）
```

### 思考時のフロー

```
User Code
  ↓
  result = sync_engine.think(sfen, request, info_handler)
  │
  └→ [イベントループ] async_engine.think(...)
      ↓
      1. submit_position(sfen, moves)
         └→ AsyncUsiProcess.send_line("position ...")
             └→ SpawnerBackedUSIBridge.send_line(...)
                 └→ EngineProcess.stdin.write(...)

      2. "go" コマンド送信
         └→ UsiThinkRequest → "go byoyomi 1000" 等

      3. レスポンス受信ループ（_response_task）
         ├─ SpawnerBackedUSIBridge.receive_lines()
         │   └→ EngineProcess.stdout.readline()
         ├─ "info ..." 行
         │   ├→ UsiProtocolParser.parse_info(line)
         │   └→ info_handler(UsiThinkPV) 呼び出し
         └─ "bestmove 7g7f ponder 3c3d"
             └→ UsiProtocolParser.parse_bestmove(line)
                 └→ UsiThinkResult生成

      4. 結果を返す
  │
  └→ [User Thread] result: UsiThinkResult
```

### 終了時のフロー

```
User Code
  ↓
  sync_engine.close()
  │
  └→ [イベントループ] async_engine.close()
      ↓
      AsyncUsiProcess.stop()
      ↓
      SpawnerBackedUSIBridge.stop_process()
      ├─ 1. stderr監視タスクをキャンセル
      ├─ 2. "quit" 送信
      ├─ 3. 5秒待機
      ├─ 4. タイムアウトなら kill()
      └─ 5. wait() で終了確認
  │
  └→ イベントループ停止
      ├─ loop.call_soon_threadsafe(loop.stop)
      ├─ thread.join()
      └─ loop.close()
```

## 拡張ポイント

### 1. 新しいプロセス起動方式の追加

新しい起動方式（例: Docker、Kubernetes、WebAssembly）を追加するには：

```python
from shogiarena.arena.engines.usi_bridge import AsyncUSIProcessBridgeProtocol

class DockerUSIBridge:  # Protocolを明示的に継承する必要はない
    """Docker経由でエンジンを起動するブリッジ"""

    def __init__(self, image: str, container_name: str):
        self._image = image
        self._container_name = container_name
        self._process: DockerProcess | None = None

    @property
    def name(self) -> str:
        return self._container_name

    async def start_process(self) -> None:
        """docker run でコンテナ起動"""
        self._process = await docker_client.run(
            self._image,
            name=self._container_name,
            stdin=True,
            stdout=True,
            stderr=True,
        )

    async def stop_process(self) -> None:
        """docker stop でコンテナ停止"""
        if self._process:
            await self._process.stop()

    async def send_line(self, command: str) -> None:
        """コンテナのstdinに送信"""
        await self._process.stdin.write(f"{command}\n")

    async def receive_lines(self) -> AsyncIterator[str]:
        """コンテナのstdoutから受信"""
        async for line in self._process.stdout:
            yield line.decode("utf-8").strip()

    def is_running(self) -> bool:
        """コンテナが稼働中か確認"""
        return self._process is not None and self._process.is_running()

# 使用例
bridge = DockerUSIBridge(image="usi-engine:latest", container_name="engine1")
process = AsyncUsiProcess(bridge)
engine = AsyncUsiEngine(process, config)
await engine.start()
```

### 2. カスタムプロトコルの実装

USI以外のプロトコル（例: UCI、XBoard）にも対応可能：

```python
class UciEngine:
    """UCIプロトコル対応エンジン（チェス用）"""

    def __init__(self, process: AsyncUsiProcess):
        self._process = process
        # UciProtocolParserを使用

    async def start(self) -> None:
        await self._process.start()
        await self._send_command("uci")
        # uciok を待機
        # setoption で設定
        # isready → readyok

    async def go(self, position: str, **kwargs) -> UciResult:
        await self._send_command(f"position {position}")
        await self._send_command(f"go {self._build_go_params(kwargs)}")
        # bestmove を待機
```

### 3. エンジンプールの実装

複数エンジンインスタンスを効率的に管理：

```python
class EnginePool:
    """エンジンインスタンスのプール"""

    def __init__(self, config_path: Path, pool_size: int):
        self._config_path = config_path
        self._pool_size = pool_size
        self._available: asyncio.Queue[AsyncUsiEngine] = asyncio.Queue()
        self._all_engines: list[AsyncUsiEngine] = []

    async def initialize(self) -> None:
        """プールを初期化（全エンジン起動）"""
        for i in range(self._pool_size):
            engine = await EngineFactory.create_engine(
                self._config_path,
                engine_name=f"engine-{i}",
            )
            await engine.start()
            self._all_engines.append(engine)
            await self._available.put(engine)

    async def acquire(self) -> AsyncUsiEngine:
        """エンジンを取得（利用可能まで待機）"""
        return await self._available.get()

    async def release(self, engine: AsyncUsiEngine) -> None:
        """エンジンを返却"""
        await engine.new_game()  # 状態をリセット
        await self._available.put(engine)

    async def close_all(self) -> None:
        """全エンジンを終了"""
        for engine in self._all_engines:
            await engine.close()

# 使用例
pool = EnginePool(config_path, pool_size=4)
await pool.initialize()

async with pool.acquire() as engine:
    result = await engine.think(sfen, request)

await pool.close_all()
```

### 4. モックエンジンでのテスト

テスト用のモックブリッジを実装：

```python
class MockUSIBridge:
    """テスト用モックエンジン"""

    def __init__(self, responses: dict[str, list[str]]):
        self._responses = responses
        self._running = False

    @property
    def name(self) -> str:
        return "MockEngine"

    async def start_process(self) -> None:
        self._running = True

    async def stop_process(self) -> None:
        self._running = False

    async def send_line(self, command: str) -> None:
        pass  # コマンドを記録してもOK

    async def receive_lines(self) -> AsyncIterator[str]:
        """事前定義されたレスポンスを返す"""
        if "usi" in self._responses:
            for line in self._responses["usi"]:
                yield line

    def is_running(self) -> bool:
        return self._running

# テストでの使用
bridge = MockUSIBridge(responses={
    "usi": [
        "id name MockEngine",
        "id author Test",
        "option name Threads type spin default 1 min 1 max 128",
        "usiok",
    ],
    "go": [
        "info depth 1 score cp 100 pv 7g7f",
        "bestmove 7g7f ponder 3c3d",
    ],
})
process = AsyncUsiProcess(bridge)
engine = AsyncUsiEngine(process, config)

await engine.start()
result = await engine.think(sfen, request)
assert result.bestmove == "7g7f"
```

## ベストプラクティス

### 1. リソース管理

**常にコンテキストマネージャを使用**:

```python
# Good: 自動でclose()
with SyncUsiEngine.from_config_path(config_path) as engine:
    result = engine.think(sfen, request)

# Bad: close()を忘れる可能性
engine = SyncUsiEngine.from_config_path(config_path)
engine.start()
result = engine.think(sfen, request)
# engine.close() を忘れた！
```

**非同期版も同様**:

```python
# Good
async with await EngineFactory.create_engine(config_path) as engine:
    await engine.start()
    result = await engine.think(sfen, request)

# または明示的に
engine = await EngineFactory.create_engine(config_path)
try:
    await engine.start()
    result = await engine.think(sfen, request)
finally:
    await engine.close()
```

### 2. タイムアウト設定

**適切なタイムアウトを設定**:

```python
# エンジン起動時（遅いマシンを考慮）
engine = await EngineFactory.create_engine(
    config_path,
    timeout=30.0  # 起動に時間がかかる場合は長めに
)

# 思考時（時間制御と独立）
result = await engine.think(
        sfen=sfen,
        request=UsiThinkRequest(byoyomi=1000),
        timeout=5.0  # 通信タイムアウト（思考時間とは別）
    )
```

### 3. エラーハンドリング

**適切な例外処理**:

```python
try:
    engine = await EngineFactory.create_engine(config_path)
    await engine.start()
except FileNotFoundError:
    logger.error("Engine binary not found")
except asyncio.TimeoutError:
    logger.error("Engine did not respond during initialization")
except RuntimeError as e:
    logger.error("Engine process error: %s", e)
finally:
    if engine:
        await engine.close()
```

### 4. ログ出力

**各レイヤーで適切にログを出力**:

```python
# Bridge層: プロセス起動/停止
logger.debug("Starting engine process: %s", engine_path)

# Process層: I/O操作
logger.debug("[%s] > %s", self.name, command)

# Engine層: プロトコルレベル
logger.debug("Received usiok from %s", self.name)

# Factory層: 構築フロー
logger.info("Created engine %s on instance %s", engine_name, instance.name)
```

### 5. テストの構造

**各レイヤーを独立してテスト**:

```python
# Layer 2のテスト（Bridge）
async def test_spawner_backed_bridge():
    bridge = SpawnerBackedUSIBridge(
        instance=local_instance,
        engine_path="/path/to/engine",
    )
    await bridge.start_process()
    assert bridge.is_running()
    await bridge.send_line("usi")
    async for line in bridge.receive_lines():
        if line == "usiok":
            break
    await bridge.stop_process()

# Layer 4のテスト（Engine）
async def test_async_usi_engine():
    mock_bridge = MockUSIBridge(mock_responses)
    process = AsyncUsiProcess(mock_bridge)
    engine = AsyncUsiEngine(process, config)
    await engine.start()
    result = await engine.think(sfen, request)
    assert result.bestmove
    await engine.close()

# Layer 5のテスト（Sync Wrapper）
def test_sync_usi_engine():
    with SyncUsiEngine.from_config_path(config_path) as engine:
        result = engine.think(sfen, request)
        assert result.bestmove
```

## パフォーマンス考慮事項

### 1. プロセスの再利用

対局ごとにプロセスを再起動せず、`new_game()`で初期化：

```python
# Good: プロセスを再利用
engine = await EngineFactory.create_engine(config_path)
await engine.start()

for game_idx in range(100):
    await engine.new_game()  # 初期化のみ
    result = await engine.think(sfen, request)

await engine.close()

# Bad: 毎回再起動（遅い）
for game_idx in range(100):
    engine = await EngineFactory.create_engine(config_path)
    await engine.start()
    result = await engine.think(sfen, request)
    await engine.close()
```

### 2. 並列実行

複数エンジンで並列に思考：

```python
# 複数エンジンを並列起動
engines = await asyncio.gather(*[
    EngineFactory.create_engine(config_path, engine_name=f"engine-{i}")
    for i in range(4)
])

for engine in engines:
    await engine.start()

# 並列に思考
results = await asyncio.gather(*[
    engine.think(sfen, request)
    for engine in engines
])

# 並列に終了
await asyncio.gather(*[engine.close() for engine in engines])
```

### 3. CPU アフィニティ

CPUコアを固定してキャッシュ効率を向上：

```python
engine1 = await EngineFactory.create_engine(
    config_path,
    cpu_affinity=[0, 1],  # Core 0-1
)

engine2 = await EngineFactory.create_engine(
    config_path,
    cpu_affinity=[2, 3],  # Core 2-3
)
```

## トラブルシューティング

### 問題1: エンジンが起動しない

**症状**: `RuntimeError: Engine process did not start`

**原因と対処**:

1. **バイナリが見つからない**:
   ```bash
   # パスを確認
   ls -la /path/to/engine
   # 実行権限を確認
   chmod +x /path/to/engine
   ```

2. **依存ライブラリ不足**:
   ```bash
   # ライブラリを確認
   ldd /path/to/engine
   # 不足している場合はインストール
   sudo apt install libstdc++6
   ```

3. **リモート実行時のパス問題**:
   ```python
   # ログで確認
   logger.setLevel(logging.DEBUG)
   # Provisionerが正しく転送しているか確認
   ```

### 問題2: タイムアウトが頻発

**症状**: `asyncio.TimeoutError` が発生

**原因と対処**:

1. **起動タイムアウト**:
   ```python
   # タイムアウトを延長
   engine = await EngineFactory.create_engine(
       config_path,
       timeout=60.0  # デフォルト10秒から延長
   )
   ```

2. **思考タイムアウト**:
   ```python
   # エンジンの思考時間とは別にI/Oタイムアウトを設定
result = await engine.think(
        sfen=sfen,
        request=UsiThinkRequest(byoyomi=10000),
        timeout=15.0  # 思考時間 + マージン
    )
   ```

3. **ネットワーク遅延（SSH）**:
   ```python
   # SSH接続のタイムアウトを調整
   instance = Instance(
       name="remote",
       type=InstanceType.SSH,
       ssh_config=SSHConfig(
           host="remote.example.com",
           connect_timeout=30.0,  # 接続タイムアウト
       ),
   )
   ```

### 問題3: プロセスゾンビ化

**症状**: エンジンプロセスが終了しない

**原因と対処**:

1. **quit送信失敗**:
   ```python
   # stop_process内部で確実にkill
   try:
       await asyncio.wait_for(proc.wait(), timeout=5.0)
   except asyncio.TimeoutError:
       proc.kill()  # 強制終了
       await proc.wait()
   ```

2. **close忘れ**:
   ```python
   # 必ずコンテキストマネージャを使う
   async with engine:
       ...
   ```

3. **スレッドが残る（SyncUsiEngine）**:
   ```python
   # 確実にclose
   try:
       with SyncUsiEngine.from_config_path(config_path) as engine:
           ...
   finally:
       # 念のため明示的にも呼ぶ
       engine.close()
   ```

## まとめ

ShogiArenaのエンジンラッパーは、以下の6層で構成されています：

1. **AsyncUSIProcessBridgeProtocol**: プロセス起動の抽象インターフェース
2. **SpawnerBackedUSIBridge**: ローカル/SSH対応の具体実装
3. **AsyncUsiProcess**: プロセス制御の軽量ラッパー
4. **AsyncUsiEngine**: USIセッション管理の高レベルAPI
5. **SyncUsiEngine**: 同期コード用のラッパー
6. **EngineFactory**: 設定からの構築を担うファクトリ

各レイヤーは明確な責務を持ち、上位レイヤーは下位レイヤーの実装詳細を知る必要がありません。この設計により、新しいプロトコルや起動方式を容易に追加できます。

## 関連ドキュメント

- [USI Engine の設計](usi-engine.md) - AsyncUsiEngineの詳細
- [インスタンス管理](instances.md) - InstanceとInstancePoolの解説
- [Runner と Orchestrator](runners-orchestrators.md) - エンジンを使った対局管理
- [エンジン設定](../user-guide/engine-configuration.md) - YAML設定の書き方
- [Python Library](../user-guide/python-library.md) - SyncUsiEngineの使用例
