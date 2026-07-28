# 実Antigravityで企画ラフ5案を生成し、ダミー候補を編集会議から排除する

## 1. 目的

`instruction-20260728-19.md`に基づく企画ラフ工程は実装されましたが、現在利用者向けに生成された5案は`--dummy`による決定論的な固定文です。

ダミー候補は、書式、検証、状態遷移、HTML表示をテストするためのfixtureであり、利用者が「面白そうか」を判断する創作成果物ではありません。

今回の作業では、次を行ってください。

1. ダミー候補と実AI候補をシステム上で明確に区別する
2. ダミー候補の選択・確定をコードで拒否する
3. 実Antigravity（`agy`）で、互いに異なる企画ラフ5案を生成する
4. 実Antigravityの`concept-reviewer`で独立評価する
5. 利用者が一画面で比較できる編集会議HTMLを生成する
6. 実候補とHTMLをGit追跡下にも保存し、別環境や後続作業から確認できるようにする

本文、ストーリーバイブル、挿絵、公開用HTML、note素材はまだ作成してはいけません。

## 2. 開始前

最初に`AGENTS.md`を全文読み、状態を確認してください。

```powershell
git status --short --branch
git log --oneline --decorate -10
git fetch origin
git pull --ff-only
```

必読:

- `AGENTS.md`
- `instructions/instruction-20260728-18.md`
- `instructions/result-20260728-18.md`
- `instructions/instruction-20260728-19.md`
- `instructions/result-20260728-19.md`
- `kobo/concept.py`
- `kobo/agy.py`
- `kobo/cli.py`
- `kobo/orchestrator.py`
- `agents/planner.md`
- `agents/concept-reviewer.md`
- `novels/prototype-001/READER_PROFILE.v001.md`
- `novels/prototype-001/READER_FEEDBACK.v001.md`
- `MyLike.md`
- `MyLike_kousatsu.md`

開始条件:

- `main`と`origin/main`が同期
- 作業ツリーclean
- 他プロセスが同じ作業ツリーを書込み中ではない

## 3. 現在のダミーセッション

現在の利用者向けダミーセッション:

```text
concept-6e2707118fa8400fbc35b6913b24bb74
```

状態は`awaiting_selection`ですが、これは実AI生成ではありません。

候補ファイル、DB行、旧セッションを削除・上書きしてはいけません。履歴として保持し、`superseded`へ遷移させてください。

## 4. 出自の正確な記録

### 4.1 adapter欄

非ダミーの企画セッションを`gemini`と記録している旧実装を修正してください。

- ダミー実行: `adapter = dummy`
- 実Antigravity実行: `adapter = agy`

実際に使用したアダプターと異なる文字列を保存してはいけません。

必要なら既存テーブルへ、後方互換性を保ったマイグレーションで次の情報を追加してください。

- provider / adapter
- model
- dummyか否か
- generation source

ただし、今回不要な大規模スキーマ変更は避けてください。セッションの`adapter`で確実に判別できるなら、それを正本として構いません。

### 4.2 成果物表示

候補Markdownと編集会議HTMLの目立つ位置に、次を表示してください。

```text
生成種別: 実Antigravity
アダプター: agy
モデル: <実際の設定またはCLI出力から取得できる値>
セッションID: <session-id>
```

ダミーの場合は次を表示します。

```text
生成種別: テスト用ダミー。企画選定禁止
```

## 5. ダミー候補の選択禁止

`session.adapter == "dummy"`の企画セッションでは、次をコードで拒否してください。

- `concept-select`
- `concept-revise`
- `concept-preview`
- `concept-finalize`
- 次工程への引渡し

許可する操作:

- status
- list/show/compare/board
- hold
- reject-all
- regenerate

エラーメッセージは、ダミーがテスト専用であり、実AI生成セッションが必要だと明示してください。

AI候補であるという表示だけを信頼せず、DB上のadapterを検査してください。

## 6. 実Antigravity接続確認

実生成前に次を実行してください。

```powershell
py -3 -m kobo.cli agy-doctor
py -3 -m kobo.cli agy-smoke
```

失敗した場合は、ダミーへfallbackしてはいけません。認証、利用枠、command not found、timeout等の分類を報告して停止してください。

## 7. 5案の生成方法

### 7.1 `--dummy`禁止

実地生成コマンドでは`--dummy`を付けてはいけません。

現在のダミーセッションを履歴として`superseded`へ遷移させた後、実Antigravityの新規セッションを作ってください。

### 7.2 方向性を候補ごとに固定する

独立呼出しで5案が似ることを避けるため、候補番号ごとに創作方向をプロンプトへ明記してください。

- C01: 恋愛またはラブコメ。人物同士の距離と誤解が主軸
- C02: 不思議な日常。日常へ一つだけ異常が入り、関係が変わる
- C03: 秘密、約束、過去の選択。感情的な謎が主軸
- C04: 冒険または危機。危機の中の選択で人物関係が変わる
- C05: 猫が中心的役割を持つ。ただの案内役や飾りではない

これは題材を固定するものではありません。各案の読書体験を明確に分けるための方向指定です。

全案を店、工房、修理、設備、制度改善、職業知識の披露へ寄せてはいけません。

### 7.3 既生成案との差別化

C02以降を生成する際は、同じセッション内ですでに検証合格した候補のログライン、一行コンセプト、中心人物関係をプロンプトへ含めてください。

次の違いを明示的に要求します。

- 主人公の願望
- 中心人物関係
- ジャンル
- 感情的な読書体験
- 第一話の転換
- 連載の推進力

タイトルや舞台だけが違い、構造が同じ案を5案として扱ってはいけません。

### 7.4 検証失敗時の限定再試行

実AI出力が書式検証に失敗する可能性を考慮してください。

- 最初の出力を直接、正式候補パスへ確定しない
- attempt単位の一時ファイルへ保存
- 検証エラーの具体的内容を次の修正プロンプトへ渡す
- 最大再試行数は既存agent設定以内、推奨2回
- 合格した出力だけ正式候補パスへ原子的保存
- 不合格attemptも診断用に残す
- 最大回数を超えたらダミーへ置換せずfailedで停止

## 8. 実`concept-reviewer`評価

現在の`_evaluate_one()`は、非ダミーでも固定の定型文と候補番号順の暫定順位を出しています。これを修正してください。

### ダミー時

既存の決定論的評価をテストfixtureとして維持して構いません。ただし、冒頭にダミー評価であることを明記します。

### 実AI時

`concept-reviewer`エージェントを`agy`で実行し、候補本文と読者プロファイルを独立に照合してください。

必須評価軸:

1. ログライン明瞭度
2. 主人公の願望と能動性
3. 主人公への共感または関心
4. 中心人物関係の強さ
5. 第一話の満足
6. 意外な転換の有効性
7. 先読み欲求
8. 想定読者と読後感の明瞭さ
9. 説明過多リスク
10. 連載の推進力

各軸に次を記載してください。

- 根拠となる候補中の具体的内容
- 長所
- 弱点
- 限定的な改善案
- 5段階評価

評価者は候補本文を書き換えません。

5案すべての評価後、全候補を同時に比較した比較総括も1回だけ生成してください。順位は補助情報であり、自動選択には使いません。

候補番号をそのまま順位にしてはいけません。

## 9. 編集会議HTML

新しい実AIセッションについて、`concept-board`を生成してください。

カード先頭:

- 仮題
- ログライン
- 一行コンセプト
- 主人公の性別、年齢層、立場、願望
- 想定読者と読後感
- `実Antigravity生成`の表示

続いて:

- 中心人物
- 物語の始まり
- 第一話のあらすじ
- 連載の推進力
- 企画の弱点

カード末尾:

```text
面白そう度: 1 2 3 4 5
続きを読みたい: はい / いいえ
最も気になる人物:
弱いと感じる点:
判定: 選ぶ / 修正候補 / 保留 / 却下
```

AI比較総括は全カードの後へ置き、利用者の第一印象を誘導しないようにしてください。

画像、外部CDN、JavaScript、外部通信は不要です。

## 10. Git追跡下の編集会議成果物

`.kobo/`はGit管理外なので、利用者が選択する実AI企画ラフを追跡下にも出版してください。

保存先:

```text
novels/prototype-001/editorial-board-v002/
  index.html
  candidate-c01.md
  candidate-c02.md
  candidate-c03.md
  candidate-c04.md
  candidate-c05.md
  evaluation-c01.md
  evaluation-c02.md
  evaluation-c03.md
  evaluation-c04.md
  evaluation-c05.md
  comparison.md
  PROVENANCE.json
```

`v002`が既に存在する場合は上書きせず、次の未使用版を予約してください。

`PROVENANCE.json`に最低限記録:

- session_id
- work_id
- adapter
- model
- dummy: false
- generated_at
- source reader profile path
- candidate run IDs
- evaluation run IDs
- source `.kobo` paths

この出版は企画の確定ではありません。状態は`awaiting_selection`のままです。

候補内容とHTMLはUTF-8で保存し、個人情報、本名、ローカル絶対パス、認証情報を含めないでください。

## 11. 検証

最低限:

```powershell
py -3 -m unittest tests.test_concept -v
py -3 -m unittest discover -v
py -3 -m compileall -q kobo mail tests
git diff --check
py -3 -m kobo.cli agy-doctor
py -3 -m kobo.cli agy-smoke
```

追加テスト:

- 非ダミーsessionのadapterが`agy`
- ダミーsessionのselect/revise/preview/finalize拒否
- ダミーsessionのboard/reject/regenerate許可
- 実AI生成経路がplanner adapterを呼ぶ
- 候補番号ごとの方向指定がプロンプトへ入る
- C02以降のプロンプトに既生成候補の要約が入る
- 検証失敗時の限定再試行
- 最大失敗後にダミーへfallbackしない
- 非ダミー評価がconcept-reviewer adapterを呼ぶ
- 非ダミー評価が固定定型文・候補番号順位ではない
- 編集会議HTMLに生成種別が表示される
- Git追跡用成果物に絶対パスや機密情報が入らない

## 12. 完了条件

次がすべて満たされた場合だけ完了です。

- ダミー候補を企画確定できない
- 実Antigravity候補5案が生成されている
- 5案が書式検証を通過している
- 実Antigravityによる独立評価と比較総括がある
- 実AI編集会議HTMLがある
- Git追跡下の`editorial-board-vNNN`がある
- セッションは`awaiting_selection`
- 本文、ストーリーバイブル、挿絵、公開HTMLを作っていない
- 全テストと検証が成功
- mainへcommit・push済み

結果報告:

```text
instructions/result-20260728-20.md
```

記載項目:

- 実セッションID
- adapterとmodel
- 各候補の仮題、ログライン、文字数
- 生成attempt数
- 評価・比較の実行回数
- 編集会議HTMLのローカルパス
- Git追跡版の相対パス
- テスト結果
- commit SHA
- push結果
- 未解決事項

利用者が最初に開くファイルは、Git追跡下の次の形式で明記してください。

```text
novels/prototype-001/editorial-board-vNNN/index.html
```
