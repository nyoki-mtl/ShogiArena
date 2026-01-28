# Dashboard Styles Guidelines

このディレクトリのCSSは以下の原則で運用します。

1. **エントリーポイントの単純化**  
   `src/style.css` は Tailwind の `@tailwind` 指示と `@import` のみを担当します。実スタイルは `styles/components/*.css` または `styles/theme.css` に配置してください。

2. **レイヤーごとの分類**  
   - `common.css` : 共通レイアウト・通知・ボタンなど全タブで共有するもの  
   - `tables.css` : テーブルの体裁（列グループや行ハイライトなど）  
   - `tournament.css` : トーナメント／サマリータブ（進行バー、KPI、マッチアップ等）  
   - `games.css` : ゲーム一覧タブ（フィルタ、ダイアログ、行操作等）  
   - `instances.css` / `spsa.css` / `rules.css` : 対応タブ専用スタイル  
   新しいタブを追加する場合は `styles/components/<tab名>.css` を作成し、`@layer components` 内に記述してください。

3. **セクションコメントの活用**  
   各ファイルでは `/* === Section Name === */` 形式のコメントで機能ごとにブロックを区切ります。既存のブロックを参考に、レイアウト→ナビゲーション→カード→補助要素の順で並べると検索しやすくなります。

4. **スコープの明示**  
   タブ固有のスタイルには `#gamesTab` や `#spsaTab` のようなラッパーセレクタを必ず付与し、他タブのクラスと衝突しないようにしてください。複数タブで共有する場合は `.dashboard-*` や `.games-*` といったプレフィックスを使います。

5. **Tailwind の併用**  
   ユーティリティは `@apply` を使い、Tailwind の `@layer components` を通して宣言します。複数箇所で再利用する装飾は `.btn`, `.games-status` のようなクラスにまとめ、直接的なスタイルの重複を避けます。

6. **追加時の手順**  
   1. 適切なコンポーネントファイルにスタイルを追加する  
   2. 新規ファイルを作成した場合は `src/style.css` の `@import` に追記する  
   3. `npm run frontend:build` でビルドを通し、`http://localhost:8080/index.html`（実行モードに応じてトーナメント/SPSAを表示）で表示確認する  

これらのルールを守ることで、スタイルの影響範囲が追いやすくなり、属性名のタイプミスや意図しない上書きを早期に検知できます。
