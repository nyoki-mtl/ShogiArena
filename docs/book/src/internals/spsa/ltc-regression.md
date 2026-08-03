# LTC 回帰テスト

> **前提知識**：[SPSA](./index.md)、[SPRT](../sprt/index.md)

## このページの要点

- 一定回数の SPSA 更新ごとに、長い持ち時間でチューニング済みパラメータとベースラインを対局させ、劣化していればパラメータをリバートする機能
- ShogiArena は設定項目として実装しているが、既定では無効であり、`examples/` の設定にも含めていない
- チューニングを止めずに挟める標本サイズでは、判定できるのが破綻に近い劣化に限られる。この機能を回す動機である 10〜30 Elo の劣化は、検査を通過してしまう
- Stockfish/fishtest は SPSA の実行中に LTC 検証を挟まず、チューニング終了後に独立したランで検証する

## 何をする機能か

SPSA チューニングは短い持ち時間（**STC**：Short Time Control）で実行します。
1 局あたりの費用を下げて、勾配推定に使える標本数を稼ぐためです。

ところが STC と長い持ち時間（**LTC**：Long Time Control）では探索の性質が変わります。
読みの深さが違えば、探索パラメータの最適値も違ってきます。
STC で得た改善が LTC では消える、あるいは劣化に転じる。
この乖離を **STC 過学習**と呼びます。

LTC 回帰テストは、この乖離をチューニングの実行中に検出しようとする機能です。
`every_n_updates` 回の更新ごとに、そのときのパラメータと直前の合格時点のパラメータを LTC で対局させます。
劣化と判定されればパラメータを合格時点まで戻し、そうでなければ現在のパラメータを新しい基準点として記録します。

```text
SPSA 更新ループ
  │
  ├─ 更新 1 ... 49
  │
  ├─ 更新 50 (every_n_updates=50)
  │     │
  │     └─► LTC 回帰テスト実行
  │           │
  │           ├─ パス → 現在のパラメータを基準点として保存
  │           │
  │           └─ フェイル → 前の基準点までリバート
  │
  ├─ 更新 51 ... 99
  │
  └─ ...
```

## どのくらいの劣化を検出できるか

### 判定は点推定の比較

`max_elo_drop` の判定は、推定した Elo 差をしきい値とそのまま比較します。
信頼区間は使いません。

```python
max_elo_drop = _criterion_value(criteria, "max_elo_drop")
allowed_elo_drop = abs(max_elo_drop) if max_elo_drop is not None else None
if allowed_elo_drop is not None and (winrate <= 0.0 or (elo is not None and elo < -allowed_elo_drop)):
    status = "failed"
```

したがって検出力は、推定量の標準誤差だけで決まります。

### 標準誤差と検出率

引き分け率を \\(d\\)、対局数を \\(n\\) とすると、勝率 50% 付近での Elo 推定量の標準誤差は次のようになります。

\\[
\sigma_{\text{Elo}} \approx \frac{400}{\ln 10} \cdot \frac{2\sqrt{1-d}}{\sqrt{n}}
\\]

`total_pairs: 100`（200 局）で引き分け率を 5% とすると 24 Elo です。
CRN と先後入れ替えによってペア内の結果に正の相関がつくぶんだけ下がり、実効的には 22 Elo 前後になります。

この標準誤差のもとで、しきい値を `max_elo_drop: 50.0` に置いたときの判定確率は次のとおりです。

| 真のレート差 | `failed` になる確率 |
|---:|---:|
| 0（差なし） | 1.2% |
| \\(-20\\) Elo | 9% |
| \\(-30\\) Elo | 18% |
| \\(-50\\) Elo | 50% |
| \\(-80\\) Elo | 91% |
| \\(-100\\) Elo | 99% |

しきい値は点推定と直接比較されるので、真にしきい値ちょうどだけ劣化しているとき、検出率は 50% になります。
実用的に捉えられるのは 80 Elo 規模から先です。

### 10〜30 Elo を検出するのに要る標本

STC 過学習として想定される劣化は 10〜30 Elo です。
有意水準 5%、検出力 90% で 20 Elo の劣化を検出するには、標準誤差を 6.8 Elo まで下げる必要があります。
上の式を逆に解くと、**2000 局規模**が要ります。

LTC を STC の 4 倍の持ち時間で回すなら、費用は STC 換算で 8000 局です。
`pairs_per_update: 2` で 1000 更新のチューニング本体が 4000 局ですから、検査 1 回でチューニング全体の 2 倍を使うことになります。
実行中に挟める標本サイズではありません。

## LTC でなければ捉えられないもの

標本サイズを増やせないなら、検出できる範囲だけを目的にすればよいのではないか。
つまり 80 Elo 規模の破綻を捕まえる装置として割り切る、という考え方はありえます。

その範囲の劣化は、LTC を使わなくても見えます。
パラメータが破綻した領域に入れば、STC の対局でも同じだけ勝率が落ちるからです。
LTC が STC より多くを語るのは、両者で最適値がずれる場合に限られます。
そしてそのずれの大きさが、前節で「検出できない」と結論した 10〜30 Elo です。

| 失敗モード | LTC が要るか | 検出に要る標本 |
|---|---|---|
| パラメータの破綻（80 Elo 規模） | 不要。STC で同じだけ見える | 少ない |
| STC 過学習（10〜30 Elo） | 必要 | 2000 局規模 |

**LTC でしか捉えられない劣化と、実行中に検出できる劣化が重ならない。**
ShogiArena がこの機能を既定で無効にし、`examples/` の設定からも外しているのはこのためです。

なお、SPSA の更新値は空間定義の `bounds` でクリップされます（[勾配推定](./gradient.md) を参照）。
破綻領域への逸脱そのものが起きにくいため、破綻検知としての需要も小さくなります。

## fishtest のワークフロー

fishtest ではランの停止規則が `sprt`、`spsa`、`numgames` の排他選択です。
SPSA ランに SPRT の設定は存在せず、パラメータ更新にも検証やロールバックの段階がありません。

```python
def apply_spsa_result_updates(spsa, w_params, *, result, game_pairs):
    for param, w_param in zip(spsa["params"], w_params):
        param["theta"] = clip_spsa_param_value(
            param, w_param["R"] * w_param["c"] * result * w_param["flip"])
```

fishtest における LTC はランの持ち時間に対する分類であって、SPSA の一部ではありません。
`tc_base` が 40 秒以上のランを LTC として扱い、用途は UI のフィルタと PGN の保持期間です。
分類関数が `"sprt" in args` を要求するため、SPSA ランは LTC 判定の対象にもなりません。

検証は、SPSA が終わったあとに独立したランとして行います。

1. STC で SPSA を回し、パラメータを得る
2. 得られたパラメータで別のランを立て、STC の SPRT で master と比較する
3. 通れば LTC（STC の 6 倍）の SPRT ランで再検証する
4. 通ればマージする

SPRT は結論が出るまで対局を続けられるので、固定 200 局の検査と違って、検出したい差に見合う標本サイズへ到達します。
ShogiArena で同じことをするには、チューニング終了後に `run sprt` を使ってください（[SPRT](../sprt/index.md) を参照）。

## 設定

```python
class LtcRegressionConfig(BaseModel):
    enabled: bool = False
    every_n_updates: int = 0        # 何回の更新ごとに実行
    total_pairs: int = 0            # 実行するゲームペア数
    time_control: TimeControlLimits | None = None  # LTC 用タイムコントロール
    pass_criteria: LtcPassCriteria | None = None   # パス判定基準
```

### パス判定基準

```python
class LtcPassCriteria(BaseModel):
    min_winrate: float | None = None    # 最低勝率（例: 0.55）
    max_elo_drop: float | None = None   # 最大 Elo 低下
    sprt: SprtConfig | None = None      # SPRT による判定
```

3 種類の基準を組み合わせられます。
複数を指定した場合、いずれか一つでも劣化を捉えた時点でフェイルになります。
`max_elo_drop` は正値と負値のどちらでも同じ許容低下量として扱います。

### リバートの挙動

フェイルするとパラメータは前回のパス時点に戻り、SPSA 更新は次の反復から継続します。
ゲインスケジュールは**リセットされません**（\\(k\\) は増加し続けます）。

ゲインスケジュールを据え置くことで、フェイル後のチューニングは以前より小さいステップサイズで進みます。
リバート直前と同じ経路をそのままなぞる可能性は下がり、同じ局所最適へ再突入しにくくなります。

## 有効化する場合の注意

### pass_criteria に sprt を置くとき

`total_pairs` を使い切っても SPRT が決着しない場合、`fail_closed_ltc_status_at_budget()` が `pending` を `failed` に変換します。

```python
def fail_closed_ltc_status_at_budget(status, fail_reasons, *, pairs_played, total_pairs):
    if status != "pending" or pairs_played < total_pairs:
        return status, fail_reasons
    return "failed", [*fail_reasons, LTC_SPRT_BUDGET_EXHAUSTED_REASON]
```

判定不能を安全側へ倒す設計ですが、結果としてパラメータはリバートされます。
SPRT が決着するには数千局規模の `total_pairs` が要るため、それより小さい予算で `sprt` を指定すると、劣化していなくても高い確率でリバートが起きます。

### しきい値を標本サイズと釣り合わせる

`max_elo_drop` は点推定と比較されるので、標準誤差より小さいしきい値には意味がありません。
上の式で標準誤差を求め、その 2 倍以上を目安にしてください。
200 局なら標準誤差が 22 Elo 前後で、50 Elo のしきい値に対する誤検知率が 1.2% です。

誤検知はラン全体で累積します。
1000 更新を `every_n_updates: 250` で回せば検査は 4 回になり、どこかで誤ってリバートする確率は 5% 前後です。

## 実装リファレンス

| ファイル | 関数/クラス | 役割 |
|---------|----------|------|
| `_core/contexts/spsa/adapters/runtime/ltc_regression.py` | `run_ltc_regression()` | LTC 回帰テストの本体 |
| `_core/contexts/spsa/adapters/runtime/ltc_regression_events.py` | `determine_ltc_status()` | パス/フェイル/保留の判定 |
| `_core/contexts/spsa/adapters/runtime/ltc_regression_events.py` | `fail_closed_ltc_status_at_budget()` | 予算枯渇時の fail-closed 変換 |
| `_core/contexts/game_session/adapters/orchestration/config_spsa_models.py` | `LtcRegressionConfig` | LTC 設定 |
| `_core/contexts/game_session/adapters/orchestration/config_spsa_models.py` | `LtcPassCriteria` | パス判定基準 |

## 次に読む

→ **[分散削減テクニック](../variance-reduction/index.md)**：CRN、ペアゲーム、バッチ処理といった推定精度を高める手法。
