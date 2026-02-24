# Dashboard Styles Guidelines

最終更新: 2026-02

このドキュメントはダッシュボードフロントエンドの CSS/スタイル設計の運用ルールをまとめます。

## ディレクトリ構造

```
frontend/src/styles/
├── components/       # 機能別のコンポーネントスタイル
│   ├── common.css    # 共通レイアウト・通知・ボタン
│   ├── tables.css    # テーブル体裁
│   ├── tournament.css # トーナメント/サマリータブ
│   ├── games.css     # ゲーム一覧タブ
│   ├── instances.css # インスタンスタブ
│   ├── spsa.css      # SPSAタブ
│   └── rules.css     # ルールタブ
├── theme.css         # テーマ設定
└── README.md         # （このドキュメントに統合）
```

## 運用原則

### 1. エントリーポイントの単純化

`src/style.css` は Tailwind の `@tailwind` 指示と `@import` のみを担当します。実スタイルは `styles/components/*.css` または `styles/theme.css` に配置してください。

```css
/* src/style.css */
@tailwind base;
@tailwind components;
@tailwind utilities;

@import './styles/components/common.css';
@import './styles/components/tables.css';
/* ... */
```

### 2. レイヤーごとの分類

- **`common.css`**: 共通レイアウト・通知・ボタンなど全タブで共有するもの
- **`tables.css`**: テーブルの体裁（列グループや行ハイライトなど）
- **`tournament.css`**: トーナメント／サマリータブ（進行バー、KPI、マッチアップ等）
- **`games.css`**: ゲーム一覧タブ（フィルタ、ダイアログ、行操作等）
- **`instances.css` / `spsa.css` / `rules.css`**: 対応タブ専用スタイル

新しいタブを追加する場合は `styles/components/<tab名>.css` を作成し、`@layer components` 内に記述してください。

### 3. セクションコメントの活用

各ファイルでは `/* === Section Name === */` 形式のコメントで機能ごとにブロックを区切ります。既存のブロックを参考に、レイアウト→ナビゲーション→カード→補助要素の順で並べると検索しやすくなります。

**例**:

```css
/* === Layout === */
.tournament-container {
  /* ... */
}

/* === Navigation === */
.tournament-tabs {
  /* ... */
}

/* === Cards === */
.tournament-card {
  /* ... */
}

/* === Utilities === */
.tournament-badge {
  /* ... */
}
```

### 4. スコープの明示

タブ固有のスタイルには `#gamesTab` や `#spsaTab` のようなラッパーセレクタを必ず付与し、他タブのクラスと衝突しないようにしてください。複数タブで共有する場合は `.dashboard-*` や `.games-*` といったプレフィックスを使います。

**例**:

```css
/* ❌ 悪い例: グローバルに影響 */
.status {
  color: green;
}

/* ✅ 良い例: スコープ付き */
#gamesTab .games-status {
  color: green;
}

/* ✅ 良い例: 共有スタイル */
.dashboard-status {
  /* 全タブで使用 */
}
```

### 5. Tailwind の併用

ユーティリティは `@apply` を使い、Tailwind の `@layer components` を通して宣言します。複数箇所で再利用する装飾は `.btn`, `.games-status` のようなクラスにまとめ、直接的なスタイルの重複を避けます。

**例**:

```css
@layer components {
  .btn {
    @apply px-4 py-2 rounded-md font-medium transition-colors;
  }

  .btn-primary {
    @apply bg-blue-600 text-white hover:bg-blue-700;
  }

  .games-status {
    @apply inline-flex items-center px-2 py-1 text-xs font-semibold rounded;
  }
}
```

### 6. 追加時の手順

新しいスタイルを追加する際は以下の手順に従います：

1. **適切なコンポーネントファイルにスタイルを追加する**
   - 既存タブの場合: 対応する `styles/components/<tab名>.css` を編集
   - 新規タブの場合: 新しいファイルを作成

2. **新規ファイルを作成した場合は `src/style.css` の `@import` に追記する**
   ```css
   @import './styles/components/newtab.css';
   ```

3. **ビルドを通して表示確認する**
   ```bash
   npm run frontend:build
   npm run frontend:dev
   # または
   shogiarena dashboard serve --run-dir <path>
   ```

## 命名規則

### クラス名

- **タブ固有**: `<tab>-<element>` （例: `games-filter`, `spsa-progress`）
- **共有コンポーネント**: `dashboard-<element>` （例: `dashboard-card`, `dashboard-badge`）
- **ユーティリティ**: `<purpose>-<modifier>` （例: `status-active`, `badge-success`）

### ID

- **タブコンテナ**: `#<tab>Tab` （例: `#gamesTab`, `#spsaTab`）
- **固有要素**: `#<tab><Element>` （例: `#gamesFilterForm`, `#spsaChart`）

## ベストプラクティス

### 1. レスポンシブデザイン

Tailwind のレスポンシブユーティリティを活用します：

```css
@layer components {
  .dashboard-grid {
    @apply grid grid-cols-1 gap-4;
    @apply md:grid-cols-2 lg:grid-cols-3;
  }
}
```

### 2. ダークモード対応

必要に応じて `dark:` プレフィックスを使用：

```css
@layer components {
  .dashboard-card {
    @apply bg-white dark:bg-gray-800;
    @apply border border-gray-200 dark:border-gray-700;
  }
}
```

### 3. アニメーション

Tailwind の `transition` と `animate` を使用：

```css
@layer components {
  .games-row {
    @apply transition-colors duration-150;
  }

  .games-row:hover {
    @apply bg-gray-50 dark:bg-gray-700;
  }
}
```

### 4. アクセシビリティ

フォーカススタイルを明示的に定義：

```css
@layer components {
  .btn:focus {
    @apply outline-none ring-2 ring-blue-500 ring-offset-2;
  }
}
```

## トラブルシューティング

### スタイルが適用されない

1. **`@import` の順序を確認**: `src/style.css` で正しい順序でインポートされているか
2. **Tailwind の purge 設定を確認**: `tailwind.config.cjs` で対象ファイルが含まれているか
3. **セレクタの詳細度を確認**: より詳細なセレクタが上書きしていないか
4. **ビルドを再実行**: `npm run frontend:build` で再ビルド

### クラス名の衝突

1. **スコープを追加**: タブIDやプレフィックスでスコープを明示
2. **命名規則を見直す**: より具体的で一意な名前を使用
3. **既存のクラスを確認**: `grep -r "\.classname" styles/` で既存の使用を確認

## 参照先

- モジュール構成は [architecture.md](architecture.md) を参照
- Tailwind 設定: `frontend/tailwind.config.cjs`
- Vite 設定: `frontend/vite.config.ts`
