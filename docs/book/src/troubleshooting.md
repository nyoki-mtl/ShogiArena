# トラブルシューティング

ShogiArena の使用中に発生する可能性のある問題と解決方法をまとめています。

## インストール関連

### pip install でエラーが発生する

#### Python バージョンが古い

```
ERROR: Package 'shogiarena' requires a different Python: 3.9.0 not in '>=3.10'
```

**解決**: Python 3.10 以上にアップグレードしてください。

```bash
# Python バージョン確認
python --version

# pyenv を使用している場合
pyenv install 3.11.0
pyenv global 3.11.0
```

#### 依存パッケージのビルドエラー

**解決**: システムライブラリが不足している可能性があります。

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

**原因**: エンジンのパスが間違っている。

**解決**:
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
   path: "/full/path/to/engine"
   ```

#### エラー: `PermissionError: [Errno 13] Permission denied`

**原因**: エンジンに実行権限がない。

**解決**:
```bash
chmod +x /path/to/engine
```

#### エラー: `Engine startup timeout`

**原因**: エンジンの起動に時間がかかりすぎている（ニューラルネットワークモデルの読み込みなど）。

**解決**: `startup_timeout_sec` を長くする。

```yaml
# engine.yaml
name: "SlowEngine"
path: "/path/to/engine"
startup_timeout_sec: 60  # デフォルトは 10 秒
```

#### エラー: `error while loading shared libraries`

**原因**: 必要な共有ライブラリがシステムにインストールされていない。

**解決**:
```bash
# 不足しているライブラリを確認
ldd /path/to/engine

# 例: libtbb.so.2 が必要な場合
sudo apt install libtbb2  # Ubuntu
brew install tbb          # macOS
```

### エンジンオプションが反映されない

**原因**: オプション名が間違っている、またはエンジンがサポートしていない。

**解決**:
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

**原因**: インスタンス設定が正しくない、またはインスタンスに空きがない。

**解決**:
1. インスタンス設定ファイルを確認
   ```bash
   cat configs/resources/instances/local.yaml
   ```

2. `slots` と `max_engines` の設定を確認
   ```yaml
   instances:
     - instance_id: "local"
       type: "local"
       slots: 4          # 同時実行対局数
       max_engines: 8    # 同時起動エンジン数
   ```

3. デフォルトインスタンスを使用
   ```bash
   # --instances を指定しない
   shogiarena run tournament tournament.yaml
   ```

#### エラー: `Config validation failed`

**原因**: 設定ファイルの構文エラーまたは必須フィールドの欠落。

**解決**:
1. YAML の構文エラーを確認
   ```bash
   # Python で YAML を読み込んで確認
   python -c "import yaml; yaml.safe_load(open('tournament.yaml'))"
   ```

2. 必須フィールドが揃っているか確認
   - `experiment_name`
   - `engines` リスト
   - `tournament.scheduler`

3. サンプル設定と比較
   ```bash
   cat examples/configs/run/tournament/example.yaml
   ```

#### dry-run で確認

```bash
shogiarena run tournament tournament.yaml --dry-run
```

設定の検証とスケジュール生成のみを行い、実際の対局は行いません。

### 対局が途中で止まる

#### エラー: `Engine timeout during game`

**原因**: エンジンの思考時間が長すぎる、またはエンジンがクラッシュした。

**解決**:
1. 時間制御を確認
   ```yaml
   rules:
     time_control:
       time_ms: 10000
       increment_ms: 100
   ```

2. エンジンのログを確認
   ```bash
   # ログディレクトリを確認
   cat {output_dir}/tournament/.../logs/game_*.log
   ```

3. エンジンを単体でテスト
   ```bash
   shogiarena run mate configs/engine/myengine.yaml \
     --position startpos --ply-limit 5
   ```

#### 対局数が想定より少ない

**原因**: `games_per_pair` の設定が小さい、または SPRT で早期停止した。

**解決**:
1. `games_per_pair` を増やす
   ```yaml
   tournament:
     games_per_pair: 100  # デフォルトは 4
   ```

2. SPRT の場合、条件を確認
   ```yaml
   sprt:
     enabled: true
     elo0: 0
     elo1: 5
   ```

## ダッシュボード関連

### ダッシュボードが開かない

#### エラー: `Address already in use`

**原因**: 指定したポートが既に使用されている。

**解決**:
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

**原因**: ファイアウォールまたはネットワーク設定の問題。

**解決**:
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

**原因**: Server-Sent Events (SSE) の接続が切れている。

**解決**:
1. ブラウザをリロード（F5）

2. ブラウザのコンソールでエラーを確認
   - F12 キー → Console タブ

3. ネットワークタブで SSE 接続を確認
   - F12 キー → Network タブ → `/api/events` を確認

## リモート実行関連

### SSH 接続エラー

#### エラー: `Host key verification failed`

**原因**: リモートサーバーのホストキーが登録されていない。

**解決**:
```bash
# 手動で接続してホストキーを登録
ssh user@remote-server

# または known_hosts をバイパス（非推奨）
ssh -o StrictHostKeyChecking=no user@remote-server
```

#### エラー: `Permission denied (publickey)`

**原因**: 公開鍵認証が正しく設定されていない。

**解決**:
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

**解決**:
```bash
# リモートでディレクトリを作成
ssh user@remote-server "mkdir -p ~/shogiarena"
```

#### エンジンが見つからない

**原因**: リモート側にエンジンが配置されていない。

**解決**:
1. プロビジョニングを強制
   ```bash
   shogiarena run tournament tournament.yaml \
     --instances ssh.yaml --provision force
   ```

2. または、リモート側に手動で配置
   ```bash
   scp /local/path/to/engine user@remote-server:/remote/path/to/engine
   ```

## データベース関連

### データベースが破損した

#### エラー: `database disk image is malformed`

**原因**: SQLite データベースファイルが破損している。

**解決**:
1. データベースを削除して再実行（**データは失われます**）
   ```bash
   rm {output_dir}/tournament/.../game.db
   ```

2. またはバックアップから復元
   ```bash
   cp game.db.backup game.db
   ```

### データベースがロックされる

#### エラー: `database is locked`

**原因**: 複数のプロセスが同時にデータベースにアクセスしている。

**解決**:
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

**原因**:
- エンジンの思考時間が長い
- 並列実行数が少ない
- システムリソースが不足

**解決**:
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

**原因**: エンジンのメモリ使用量が大きい。

**解決**:
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
   instances:
     - instance_id: "local"
       max_engines: 4
   ```

## その他

### ログファイルの場所がわからない

```bash
# 設定を確認
shogiarena config show

# 出力ディレクトリを確認
ls {output_dir}/tournament/
```

デフォルトは以下の通り：
- Linux: `~/.local/share/shogiarena/output`
- macOS: `~/Library/Application Support/shogiarena/output`
- Windows: `%LOCALAPPDATA%\shogiarena\output`

### 設定をリセットしたい

```bash
# 設定ファイルを削除
rm ~/.config/shogiarena/settings.yaml  # Linux
rm ~/Library/Application\ Support/shogiarena/settings.yaml  # macOS
del %LOCALAPPDATA%\shogiarena\settings.yaml  # Windows

# 再初期化
shogiarena config init
```

### プレースホルダーが展開されない

**原因**: `shogiarena config init` を実行していない。

**解決**:
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

- ShogiArena のバージョン: `pip show shogiarena`
- Python のバージョン: `python --version`
- OS とバージョン
- エラーメッセージの全文
- 再現手順
- 設定ファイル（機密情報は削除してください）

GitHub Issues: https://github.com/nyoki-mtl/ShogiArena/issues
