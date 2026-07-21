# 将棋用語対訳表（Shogi Terminology Glossary）

ShogiArena で使う将棋ドメイン語彙の正本を定義する。
識別子、wire key、DB カラム、ユーザー向けドキュメントで同じ概念に同じ英語を使うための表である。

## 採用方針

1. `rsshogi` の公開 API や用語が一般的な英語訳として自然であれば、それに合わせる。
2. `rsshogi` の用語が互換目的の短縮名や記録形式由来の語である場合は、ShogiArena の公開ドメイン語彙では自然な英語を優先し、rsshogi 連携境界でだけ変換する。
3. コード識別子は英語を正本にする。`sennichite`, `jishogi`, `nyugyoku` のようなローマ字表記は、説明文、外部サービス連携、記録形式互換で必要な場合だけ使う。
4. 先手 / 後手は `black` / `white` を使う。`sente` / `gote` はユーザー向け説明や外部表記の別名としてのみ扱う。
5. 手数は `ply` を使う。西洋チェスの full move と混同しやすい `move_count` は、単に配列長を数える場合以外は避ける。

## 基本概念

| 日本語 | 正本英語 | 識別子例 | 備考 |
|---|---|---|---|
| 将棋 | shogi | `shogi_engine`, `shogi_record` | 固有名として小文字。 |
| 対局 / 一局 | game | `game_id`, `game_result` | トーナメント上の組み合わせは `match`。実行単位を強調するときは `game_session`。 |
| マッチ / 組み合わせ | match | `match_id`, `match_pairing` | 複数 game を含む競技単位。 |
| トーナメント | tournament | `tournament_id` | 既存どおり。 |
| 局面 | position | `position`, `initial_position_sfen` | SFEN で保存する場合は `*_sfen`。raw 構造体は `position_state`。 |
| 盤 / 盤面 | board | `board`, `board_index` | rsshogi の `Board` と整合。局面全体は `position` を優先。 |
| 棋譜 | game record | `game_record`, `record_id` | 形式名は `kif`, `ki2`, `csa`, `jkf`。汎用名としての `kifu` は避ける。 |
| 変化手順 | variation | `variations`, `variation_node` | rsshogi record 用語と整合。 |
| コメント | comment | `move_comment`, `initial_comment` | KIF/CSA/JKF のコメントも `comment`。 |
| 平手 | even game / standard start position / hirate | `standard_start_position`, `hirate_position` | USI 初期局面は `startpos`。rsshogi API と合わせる箇所では `hirate` も可。 |
| 駒落ち / 手合割 | handicap | `handicap`, `handicap_rule` | 駒落ち種別は `lance_handicap` など。 |

## 手番と盤上要素

| 日本語 | 正本英語 | 識別子例 | 備考 |
|---|---|---|---|
| 先手 | black | `black_player`, `black_win` | rsshogi / SFEN / コンピュータ将棋寄りの標準に合わせる。 |
| 後手 | white | `white_player`, `white_win` | `first_player` / `second_player` は競技順の説明でのみ使用。 |
| 手番 | side to move / turn | `side_to_move`, `turn` | 状態と wire は `side_to_move`、rsshogi 風 API は `turn` を許可。 |
| 色 / 手番色 | color | `Color`, `winner_color` | 参加者ではなく先後を表す値。 |
| 駒 | piece | `piece`, `captured_piece` | rsshogi の `Piece` と整合。 |
| 駒種 | piece type | `piece_type`, `captured_piece_type` | `piece_kind` は使わない。 |
| 持ち駒 | hand | `hand`, `hand_counts` | rsshogi の `Hand` と整合。 |
| マス / 升目 | square | `from_square`, `to_square` | rsshogi の `Square` と整合。 |
| 筋 | file | `file`, `board_file`, `origin_file` | ファイルパスの `file` と衝突する文脈では `board_file`。 |
| 段 | rank | `rank`, `target_rank` | rsshogi の `Rank` と整合。 |
| 成りゾーン / 敵陣 | promotion zone | `promotion_zone` | 「敵陣」はルール説明では `promotion zone` を優先。 |

## 指し手と合法性

| 日本語 | 正本英語 | 識別子例 | 備考 |
|---|---|---|---|
| 指し手 / 手 | move | `Move`, `next_move` | rsshogi の `Move` と整合。 |
| 手数 | ply | `ply`, `ply_count`, `max_plies` | 1 手を 1 ply と数える。 |
| 着手する | apply move | `apply_move`, `apply_usi` | rsshogi の `apply_usi` と整合。 |
| 指し戻す | undo move | `undo_move` | 履歴巻き戻し。 |
| 合法手 | legal move | `legal_moves`, `is_legal_move` | ルール上許される手。 |
| 疑似合法手 | pseudo-legal move | `pseudo_legal_moves` | 王手放置など未検査の生成手。 |
| 打ち | drop | `drop`, `is_drop`, `dropped_piece_type` | USI の `P*5e` など。 |
| 成り | promotion / promote | `promotion`, `is_promotion`, `promote` | 指し手属性は `promotion`。 |
| 不成 | non-promotion / unpromoted | `non_promotion`, `unpromoted_piece` | 指し手選択は `non_promotion`、駒状態は `unpromoted`。 |
| 取り | capture | `capture`, `captured_piece_type` | rsshogi の captured 系 API と整合。 |
| 王手 | check | `is_in_check`, `gives_check` | `checked` は UI 状態と混同しやすい。 |
| 詰み / 詰ます | mate | `is_mate`, `mate_in_one` | コードでは rsshogi に合わせ `mate`。説明文では checkmate も可。 |
| 王手回避手 | evasion | `evasions`, `legal_evasions` | 王手を外す合法手。 |
| 合駒 | interposition | `interposition`, `interposing_piece` | 詰将棋や王手回避の文脈。 |
| 利き | attack / attacks | `attacks_by`, `attackers_to`, `is_attacked_by` | rsshogi の attack 系 API と整合。 |

## 駒名

| 日本語 | 正本英語 | 識別子例 | rsshogi 対応 |
|---|---|---|---|
| 歩 / 歩兵 | pawn | `pawn` | `PieceType.PAWN` |
| 香 / 香車 | lance | `lance` | `PieceType.LANCE` |
| 桂 / 桂馬 | knight | `knight` | `PieceType.KNIGHT` |
| 銀 / 銀将 | silver | `silver` | `PieceType.SILVER` |
| 金 / 金将 | gold | `gold` | `PieceType.GOLD` |
| 角 / 角行 | bishop | `bishop` | `PieceType.BISHOP` |
| 飛 / 飛車 | rook | `rook` | `PieceType.ROOK` |
| 玉 / 王 | king | `king` | `PieceType.KING` |
| と / と金 | promoted pawn | `promoted_pawn` | `PieceType.PRO_PAWN` |
| 成香 | promoted lance | `promoted_lance` | `PieceType.PRO_LANCE` |
| 成桂 | promoted knight | `promoted_knight` | `PieceType.PRO_KNIGHT` |
| 成銀 | promoted silver | `promoted_silver` | `PieceType.PRO_SILVER` |
| 馬 / 竜馬 / 龍馬 | horse | `horse` | `PieceType.HORSE` |
| 竜 / 龍 / 竜王 / 龍王 | dragon | `dragon` | `PieceType.DRAGON` |
| 金と同じ動きの駒 | gold-like piece | `gold_like_piece`, `is_gold_like` | `PieceType.GOLD_LIKE` |

補足：

- 公開表示では `horse (promoted bishop)` / `dragon (promoted rook)` のように説明してよい。
- 独自 enum / wire 値を作る場合は `promoted_pawn` などの自然な full name を優先する。rsshogi の定数名を直接扱う境界では `PRO_PAWN` などをそのまま使う。

## 終局とルール

| 日本語 | 正本英語 | 識別子例 | 備考 |
|---|---|---|---|
| 対局結果 | game result | `game_result`, `GameResult` | rsshogi の `GameResult` と整合。 |
| 勝ち / 負け | win / loss | `black_win`, `white_loss` | 結果値は勝者側を明示する。 |
| 引き分け | draw | `draw`, `is_draw` | 具体理由がある場合は `draw_by_*`。 |
| 投了 | resignation / resign | `resignation`, `resign` | USI token は `resign`。 |
| 千日手 | repetition | `draw_by_repetition`, `repetition_state` | 説明文では `sennichite` を併記可。 |
| 連続王手の千日手 | perpetual check / continuous check | `continuous_check`, `perpetual_check` | 実装名は rsshogi 寄りに `continuous_check` を優先。 |
| 持将棋 | impasse | `draw_by_impasse`, `impasse_rule` | 説明文では `jishogi` を併記可。 |
| 入玉 | entering king | `entering_king`, `entering_king_rule` | `nyugyoku` は説明文と外部表記でのみ使う。 |
| 宣言勝ち / 入玉宣言勝ち | declaration win / win by declaration | `win_by_declaration`, `declaration_win` | ShogiArena 正本は `win_by_declaration`。rsshogi も旧 `ENTERING_OF_KING` から `WIN_BY_DECLARATION` へ移行予定。 |
| トライルール | try rule | `try_rule`, `win_by_try_rule` | rsshogi の `TryRule` と整合。 |
| 最大手数引き分け | draw by max plies | `draw_by_max_plies`, `max_plies` | `max_moves` は record special move 互換でのみ可。 |
| 反則手 | illegal move | `illegal_move`, `win_by_illegal_move` | ShogiArena 正本は `win_by_illegal_move`。rsshogi も旧 `FOUL_WIN` から `WIN_BY_ILLEGAL_MOVE` へ移行予定。 |
| 時間切れ | timeout | `timeout`, `win_by_timeout` | clock の duration は `*_ms`。 |
| 不戦勝 / 不戦敗 | forfeit / win by default | `win_by_forfeit`, `win_by_default` | 結果分類は `forfeit`、記録 special move は `win_by_default` を許可。 |
| 中断 | interrupted / paused | `interrupted`, `paused` | システム中断は `interrupted`、再開可能状態は `paused`。 |

## 時間と持ち時間

| 日本語 | 正本英語 | 識別子例 | 備考 |
|---|---|---|---|
| 持ち時間 | main time / time control | `main_time_ms`, `time_control` | duration なので `*_ms`。 |
| 秒読み | byoyomi | `byoyomi_ms` | 英語圏でも byoyomi が一般的。 |
| 加算 | increment | `increment_ms` | Fischer increment 等。 |
| 残り時間 | remaining time | `remaining_time_ms` | duration。 |
| 消費時間 | elapsed time | `elapsed_time_ms` | duration。 |
| 1 手の思考時間 | move time / thinking time | `move_time_ms`, `thinking_time_ms` | USI `go` の `movetime` は境界名としてのみ。 |

## 記録形式とプロトコル

| 日本語 | 正本英語 | 識別子例 | 備考 |
|---|---|---|---|
| USI | USI | `usi_move`, `parse_usi_position` | Universal Shogi Interface。 |
| SFEN | SFEN | `initial_position_sfen`, `to_sfen` | Shogi FEN。 |
| KIF | KIF | `parse_kif`, `to_kif` | 形式名として保持。 |
| KI2 | KI2 | `parse_ki2`, `to_ki2` | 形式名として保持。 |
| CSA | CSA | `parse_csa`, `to_csa` | 形式名として保持。 |
| JKF | JKF | `parse_jkf`, `to_jkf` | 形式名として保持。 |
| 最善手 | best move | `best_move` | USI `bestmove` 境界では token を保持。 |
| 予想手 | ponder move | `ponder_move` | USI `ponder`。 |
| 評価値 | evaluation score | `eval_score`, `score_cp` | centipawn 相当なら `*_cp`。 |
| 読み筋 | principal variation | `principal_variation`, `pv` | `pv` は USI 境界や engine log でのみ可。 |

## 避ける語彙

| 避ける語 | 代替 | 理由 |
|---|---|---|
| `sente` / `gote` | `black` / `white` | rsshogi / SFEN / コンピュータ将棋の実装語彙に合わせる。 |
| `kifu`（汎用名） | `game_record` / `record` | KIF 形式と generic 棋譜が混ざる。 |
| `move_count`（手数） | `ply_count` | shogi の 1 手単位を明示する。 |
| `piece_kind` | `piece_type` | rsshogi と一般 API 語彙に合わせる。 |
| `board_state`（局面） | `position` / `position_state` | 盤上だけでなく持ち駒と手番も含むため。 |
| `nyugyoku` | `entering_king` | ローマ字は説明文と外部表記に限定する。 |
| `jishogi` | `impasse` | コードでは英語正本を使う。 |
| `sennichite` | `repetition` | コードでは英語正本を使う。 |
| `foul`（反則） | `illegal_move` / `win_by_illegal_move` | `foul` は記録形式由来で、通常の英語 API としては曖昧。 |

## rsshogi 用語レビュー

概ね rsshogi の英語訳は自然で、ShogiArena でも合わせる価値が高い。
特に `black` / `white`, `piece_type`, `hand`, `drop`, `promotion`, `side_to_move`, `turn`, `ply`, `GameRecord`, `GameResult`, `DRAW_BY_REPETITION`, `DRAW_BY_IMPASSE`, `*_BY_DECLARATION`, `*_BY_ILLEGAL_MOVE`, `*_BY_TIMEOUT` はそのまま採用してよい。

将来の rsshogi 変更を前提にする点は次のとおり。

- rsshogi 側は `SpecialMove::EnteringOfKing` / `"ENTERING_OF_KING"` を `WIN_BY_DECLARATION` へ移行予定。ShogiArena-owned の結果理由、wire 値、enum 値は、rsshogi の公開前から `win_by_declaration` を正本にする。旧 rsshogi 名は古い rsshogi バージョンや record special move 互換の境界でだけ扱う。
- rsshogi 側は `SpecialMove::FoulWin` / `"FOUL_WIN"` を `WIN_BY_ILLEGAL_MOVE` へ移行予定。ShogiArena-owned の結果理由、wire 値、enum 値は、rsshogi の公開前から `win_by_illegal_move` を正本にする。`foul` は KIF 等の記録形式互換で必要な場合に限定する。
- `PRO_PAWN` などの `PRO_*` は enum 定数としては問題ないが、公開ドキュメントや ShogiArena 独自 wire 値では `promoted_pawn` のような full name を優先する。
- `HORSE` / `DRAGON` は一般的で問題ない。ユーザー向け表示では `horse (promoted bishop)` / `dragon (promoted rook)` と併記すると親切。

## 参照

- rsshogi: https://github.com/nyoki-mtl/rsshogi
- rsshogi Python types: https://github.com/nyoki-mtl/rsshogi/blob/main/crates/rsshogi-py/python/rsshogi/types.pyi
- rsshogi game results and special moves: https://github.com/nyoki-mtl/rsshogi/blob/main/crates/rsshogi/src/lib.rs
- Wikipedia: Shogi: https://en.wikipedia.org/wiki/Shogi
- Wikipedia: Shogi notation: https://en.wikipedia.org/wiki/Shogi_notation
- GNU Shogi manual: The rules of shogi: https://www.gnu.org/software/gnushogi/manual/The-rules-of-shogi.html
- Chessprogramming Wiki: USI: https://www.chessprogramming.org/USI
- Japan Shogi Association event rules PDF: https://www.shogi.or.jp/event/Rules%20and%20regulations.pdf
- Shogi Cloud: Impasse: https://www.shogi.cloud/en/impasse/
