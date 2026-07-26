# トラブルシューティング

ShogiArena の使用中に起きやすい問題を、症状ごとに原因と対処の順でまとめています。
見出しには実際に表示されるエラーメッセージを載せているので、手元のメッセージで検索してください。

## インストール関連

### pip install でエラーが発生する

#### Python バージョンが古い

```
ERROR: Package 'shogiarena' requires a different Python: 3.10.0 not in '>=3.11'
```

**解決**：Python 3.11 以上にアップグレードしてください。

```bash
# Python バージョン確認
python --version

# pyenv を使用している場合
pyenv install 3.11.0
pyenv global 3.11.0
```

#### 依存パッケージのビルドエラー

**原因**：ビルドに必要なシステムライブラリやコンパイラが不足しています。

**解決**：

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install build-essential python3-dev

# macOS (Homebrew)
brew install python@3.11

# Windows
# Visual Studio Build Tools をインストール
```

## エンジン関連

### エンジンが起動しない

#### エラー: `FileNotFoundError: [Errno 2] No such file or directory`

**原因**：エンジンのパスが間違っています。

**解決**：
1. パスが正しいか確認
   ```bash
   ls -l /path/to/engine
   ```

2. プレースホルダーを使用している場合、設定を確認
   ```bash
   shogiarena config show
   ```

3. 絶対パスで試す
   ```yaml
   engine_path: "/full/path/to/engine"
   ```

#### エラー: `PermissionError: [Errno 13] Permission denied`

**原因**：エンジンに実行権限がありません。

**解決**：
```bash
chmod +x /path/to/engine
```

#### エラー: `Engine startup timeout`

**原因**：エンジンの起動に時間がかかりすぎています（ニューラルネットワークモデルの読み込みなど）。

**解決**：engine 設定の `handshake_timeout` を長くします。

```yaml
# engine.yaml
name: "SlowEngine"
engine_path: "/path/to/engine"
handshake_timeout: 60  # 秒
```

トーナメント全体の既定値として指定したい場合は run 設定側に書きます。

```yaml
system:
  engine_handshake_timeout: 60
```

#### エラー: `error while loading shared libraries`

**原因**：必要な共有ライブラリがシステムにインストールされていません。

**解決**：
```bash
# 不足しているライブラリを確認
ldd /path/to/engine

# 例: libtbb.so.2 が必要な場合
sudo apt install libtbb2  # Ubuntu
brew install tbb          # macOS
```

### エンジンオプションが反映されない

**原因**：オプション名が間違っているか、エンジンがそのオプションをサポートしていません。

**解決**：
1. エンジンを手動で起動してオプションを確認
   ```bash
   /path/to/engine
   > usi
   # option name ... が表示される
   > quit
   ```

2. 大文字小文字に注意（`Threads` vs `threads`）

3. エンジンのドキュメントを確認

## トーナメント実行関連

### トーナメントが開始しない

#### エラー: `No instances available`

**原因**：インスタンス設定が正しくないか、インスタンスに空きがありません。

**解決**：
1. インスタンス設定ファイルを確認
   ```bash
   cat examples/configs/resources/instances/README.md
   ```

2. `slots` と `max_engines` の設定を確認
   ```yaml
   name: "local"
   type: "local"
   slots: 4
   max_engines: 8
   ```

3. デフォルトインスタンスを使用
   ```bash
   # run 設定の instances を外してローカル既定設定に任せる
   shogiarena run tournament tournament.yaml
   ```

#### エラー: `Config validation failed`

**原因**：設定ファイルに構文エラーがあるか、必須フィールドが欠けています。

**解決**：
1. YAML の構文エラーを確認
   ```bash
   # Python で YAML を読み込んで確認
   python -c "import yaml; yaml.safe_load(open('tournament.yaml'))"
   ```

2. 必須フィールドが揃っているか確認
   - `engines` リスト（少なくとも 2 つ）
   - `rules`（時間制御などのルール定義）

3. サンプル設定と比較
   ```bash
   cat examples/configs/run/tournament/example.yaml
   ```

#### dry-run で設定を検証する

```bash
shogiarena run tournament tournament.yaml --dry-run
```

設定の検証とスケジュール生成のみを行い、実際の対局は行いません。

### 対局が途中で止まる

#### エラー: `Engine timeout during game`

**原因**：エンジンの思考時間が長すぎるか、エンジンがクラッシュしています。

**解決**：
1. 時間制御を確認
   ```yaml
   rules:
     time_control:
       time_ms: 10000
       increment_ms: 100
   ```

2. エンジンのログを確認
   ```bash
   shogiarena results summary /path/to/run --format json
   ls /path/to/run/transcripts
   ```

3. エンジンを単体でテスト
   ```bash
   shogiarena run mate myengine.yaml startpos --ply-limit 5
   ```

#### `completion_status.json` の読み方

run の終了状態は `status` と `termination_reason` の組で読みます。

`status=failed` は「この run の結果を完了した測定として扱えない」という意味で、異常終了とは限りません。

| `termination_reason` | 何が起きたか | 対処 |
| --- | --- | --- |
| `schedule-complete` | 予定を消化して正常終了 | 対処不要 |
| `sprt-finished` | SPRT が結論へ到達して早期終了 | 対処不要。`not_played` は予定との差 |
| `cancelled` | 利用者が停止した | 故障ではない。resume で再開できる |
| `timeout-burst` | 停滞起因の時間切れが閾値に達した | 下の項目を参照 |
| `timeout-attribution-unknown` | 原因を断定できない時間切れが閾値に達した | 下の項目を参照 |
| `transport-timeout` | 通信・プロトコル待ちの失敗が閾値に達した | エンジンの応答性とリモート接続を確認 |
| `incomplete` | 正常終了の証拠がないまま予定が残っている | 実行ログで中断の原因を確認 |
| `runtime-error` | 実行中のエラーで終了した | 実行ログを確認 |
| `finalization-error` | 最終処理に失敗した | 実行ログを確認。結果は再集計が必要 |
| `cleanup-error` | 後始末に失敗した | プロセスやポートの残留を確認 |

`is_provisional` が `true` の場合は、中断された run で後始末の結果を反映できないまま
暫定の status が残っています。`status` と `termination_reason` はそのまま読んで構いません。
`cleanup_error` があれば、そこに後始末の失敗理由が入っています。
プロセスやポートが残っていないかを確認してください。

`completion_status.json` が **存在しない** 場合は、`manifest.json` の `shogiarena_version` を確認してください。

- 1.0.x の run：この artifact はそもそも出力されません。欠落は異常ではありません。
- 1.1.0 以降、または version が読めない run：最終処理に到達する前に中断された可能性があります。

#### run が `timeout-burst` / `timeout-attribution-unknown` で停止する

**原因**：無効と判定された時間切れが由来ごとの閾値に達したため、新規対局の投入を止めています。
一度の停滞は並行中の全対局を同時に無効化しうるので、そのまま続けると 0 手の無効局が量産されます。

`timeout-attribution-unknown` は「ShogiArena 側の停滞と断定できた」わけではなく、
**エンジン起因か停滞起因かを区別できない時間切れが増えた**という意味です。

**解決**：
1. 停滞の規模と内訳を確認
   ```bash
   cat /path/to/run/completion_status.json
   ```
   `watchdog.max_loop_lag_ms` が大きいほど、ホスト側の負荷や I/O 待ちが疑われます。
   `timeouts_by_origin` に由来別の件数が入っています。
   `watchdog.is_coverage_complete` が `false` なら、監視記録が溢れており判定材料自体が不足しています。

2. 並列数を下げる（`tournament.num_parallel`）か、dashboard を無効にして負荷を減らす

3. ホスト側の要因（他プロセスの負荷、スリープ・サスペンド、ウイルス対策のスキャン）を確認

停止した run は完了済みの対局を保持しているので、resume で続きから再開できます。

#### 対局が `ERROR` として記録される

**原因**：エンジンの起動失敗やクラッシュのほか、engine 起因と断定できない時間切れも `ERROR`（無効局）として記録されます。
無効局はレーティングと SPRT の標本から除外されるため、対局数は増えても検定は進みません。

**解決**：`completion_status.json` の `timeouts_by_origin` で内訳を確認します。

- `orchestrator_stall` が計上されていれば停滞起因なので、上の項目と同じ対処を行います。
- `unknown` が計上されていれば原因を確定できていません。負荷を下げるか、監視記録の不足（`watchdog.is_coverage_complete`）を確認します。
- `transport_timeout` はプロトコル待ちの失敗です。リモート実行の接続とエンジンの応答性を確認します。
- いずれの計上もなければエンジン側の問題なので、transcripts とエンジンのログを確認してください。

由来の意味は[トーナメント](user-guide/tournaments.md)を参照してください。

#### 対局数が想定より少ない

**原因**：`games_per_pair` の設定が小さいか、SPRT が早期停止しています。

**解決**：
1. `games_per_pair` を増やす
   ```yaml
   tournament:
     games_per_pair: 100  # デフォルトは 4
   ```

2. SPRT の場合、早期停止条件を確認
   ```yaml
   sprt:
     elo0: 0.0
     elo1: 5.0
     alpha: 0.05
     beta: 0.05
     max_games: 400
   ```

## ダッシュボード関連

### ダッシュボードが開かない

#### エラー: `Address already in use`

**原因**：指定したポートを別のプロセスが使用しています。

**解決**：
1. 別のポートを指定
   ```yaml
   dashboard:
     enabled: true
     api_port: 8081  # デフォルトは 8080
   ```

2. 使用中のポートを解放
   ```bash
   # ポート 8080 を使用しているプロセスを確認
   lsof -i :8080
   
   # プロセスを停止
   kill <PID>
   ```

#### ブラウザで接続できない

**原因**：ファイアウォールまたはネットワーク設定が接続を遮っています。

**解決**：
1. ローカルホストで確認
   ```
   http://localhost:8080
   ```

2. 別のブラウザで試す

3. ファイアウォールを確認
   ```bash
   # Linux (ufw)
   sudo ufw allow 8080/tcp
   ```

### ダッシュボードが更新されない

**原因**：Live WebSocket または画面別の stream 接続が切れています。

**解決**：
1. ブラウザをリロード（F5）

2. ブラウザのコンソールでエラーを確認
   - F12 キー → Console タブ

3. Network タブで接続を確認
   - Live View: `/ws`
   - WebSocket diagnostics: `/api/ws/diagnostics`
   - Tournament summary: `/api/tournament/summary/stream`
   - SPSA: `/api/spsa/.../stream`

## リモート実行関連

### SSH 接続エラー

#### エラー: `Host key verification failed`

**原因**：リモートサーバーのホストキーが登録されていません。

**解決**：
```bash
# 手動で接続してホストキーを登録
ssh user@remote-server

# または known_hosts をバイパス（非推奨）
ssh -o StrictHostKeyChecking=no user@remote-server
```

#### エラー: `Permission denied (publickey)`

**原因**：公開鍵認証が正しく設定されていません。

**解決**：
1. 公開鍵を登録
   ```bash
   ssh-copy-id -i ~/.ssh/id_rsa.pub user@remote-server
   ```

2. 秘密鍵のパーミッション確認
   ```bash
   chmod 600 ~/.ssh/id_rsa
   ```

3. SSH エージェントに鍵を登録
   ```bash
   eval $(ssh-agent)
   ssh-add ~/.ssh/id_rsa
   ```

### ファイル同期エラー

#### エラー: `Remote project_root does not exist`

**解決**：
```bash
# リモートでディレクトリを作成
ssh user@remote-server "mkdir -p ~/shogiarena"
```

#### エンジンが見つからない

**原因**：リモート側にエンジンが配置されていません。

**解決**：
1. プロビジョニングを強制
   ```bash
   shogiarena run tournament tournament.yaml --provision force
   ```

2. または、リモート側に手動で配置
   ```bash
   scp /local/path/to/engine user@remote-server:/remote/path/to/engine
   ```

## データベース関連

### データベースが破損した

#### エラー: `database disk image is malformed`

**原因**：SQLite データベースファイルが破損しています。

**解決**：
1. データベースを削除して再実行（**データは失われます**）
   ```bash
   rm {output_dir}/tournament/runs/.../game.db
   ```

2. またはバックアップから復元
   ```bash
   cp game.db.backup game.db
   ```

### データベースがロックされる

#### エラー: `database is locked`

**原因**：複数のプロセスが同時にデータベースにアクセスしています。

**解決**：
1. 他のプロセスを停止
   ```bash
   # ShogiArena のプロセスを確認
   ps aux | grep shogiarena
   
   # 停止
   kill <PID>
   ```

2. ダッシュボードを停止してから再実行

## パフォーマンス関連

### 対局が遅い

**原因**：
- エンジンの思考時間が長い
- 並列実行数が少ない
- システムリソースが不足

**解決**：
1. 時間制御を短縮
   ```yaml
   rules:
     time_control:
       time_ms: 5000  # 5秒
       increment_ms: 50
   ```

2. 並列実行数を増やす
   ```yaml
   tournament:
     num_parallel: 8  # CPU コア数に応じて調整
   ```

3. システムリソースを確認
   ```bash
   # CPU 使用率
   top
   
   # メモリ使用状況
   free -h
   ```

### メモリ不足

#### エラー: `MemoryError` または OOM Killer

**原因**：エンジンのメモリ使用量が大きすぎます。

**解決**：
1. ハッシュサイズを減らす
   ```yaml
   options:
     Hash: 256  # MB 単位（デフォルトより小さく）
   ```

2. 並列実行数を減らす
   ```yaml
   tournament:
     num_parallel: 2
   ```

3. `max_engines` を制限
   ```yaml
   name: "local"
   type: "local"
   max_engines: 4
   ```

## その他

### ログファイルの場所がわからない

```bash
# 設定を確認
shogiarena config show

# 出力ディレクトリを確認
ls {output_dir}/tournament/runs/
ls {output_dir}/spsa/runs/
ls {output_dir}/generate/runs/
```

デフォルトは以下の通り：
- Linux：`~/.local/share/shogiarena/output`
- macOS：`~/Library/Application Support/shogiarena/output`
- Windows：`%LOCALAPPDATA%\shogiarena\output`

### 設定をリセットしたい

```bash
# 設定ファイルを削除
rm ~/.config/shogiarena/settings.yaml  # Linux
rm ~/Library/Application\ Support/shogiarena/settings.yaml  # macOS
del %APPDATA%\shogiarena\settings.yaml  # Windows

# 再初期化
shogiarena config init
```

### プレースホルダーが展開されない

**原因**：`shogiarena config init` を実行していません。

**解決**：
1. 設定を初期化
   ```bash
   shogiarena config init
   ```

2. または、絶対パスを使用
   ```yaml
   path: "/full/path/to/engine"
   ```

## サポート

問題が解決しない場合は、以下の情報を含めて GitHub Issues で報告してください。

- ShogiArena のバージョン：`uv run shogiarena --version`
- Python のバージョン：`python --version`
- OS とバージョン
- エラーメッセージの全文
- 再現手順
- 設定ファイル（機密情報は削除してください）

GitHub Issues: https://github.com/nyoki-mtl/ShogiArena/issues

脆弱性、credential、未公開の exploit は public Issue へ投稿しないでください。
セキュリティ上の問題は [Security Policy](https://github.com/nyoki-mtl/ShogiArena/security/policy) に従い、private vulnerability report で連絡してください。
