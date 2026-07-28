# 小説工房 MVP

Python標準ライブラリだけで動く、ファイル参照型のマルチAIエージェント基盤です。工程状態はSQLite、作業指示と成果物はMarkdownへ保存し、エージェント間連絡には既存の`mail/`を使います。本文執筆・部分改稿・文体調整はGemini担当として検証され、利用不能時に別AIへ無言で切り替わりません。

## 最小セットアップ

Python 3.11以降を用意し、リポジトリ直下で実行します。外部パッケージは不要です。

```powershell
py -3 -m kobo.cli --config kobo.json --dummy work-create sample-story "サンプル作品"
py -3 -m kobo.cli --config kobo.json --dummy agents
py -3 -m kobo.cli --config kobo.json --dummy run --work sample-story
py -3 -m kobo.cli --config kobo.json history --work sample-story
```

`--dummy`はGeminiを含む全アダプターを外部起動しないダミーへ差し替えます。実際の小説本文は生成しません。状態と実行成果物は既定で`.kobo/`へ保存されます。

## エージェントMarkdown

`agents/*.md`は説明文と、厳密なJSONメタデータブロックを持ちます。YAMLライブラリは使いません。

````markdown
# 本文執筆担当

```json
{"agent_id":"writer","display_name":"Gemini本文執筆担当","role":"本文初稿","adapter":"gemini","model":"gemini-2.5-pro","inputs":["task Markdown","mail ID"],"output":"result.md","next_agent":"critic","allowed_operations":["prose-writing"],"forbidden":["write-canon","fallback-provider"],"timeout":300,"max_attempts":3}
```
````

`agent_id`は英小文字で始まる英小文字・数字・`_`・`-`、`next_agent`は同じディレクトリ内の定義を参照します。`prose-writing`を許可する定義は`adapter: gemini`でなければ検証に失敗します。

## 設定と優先順位

設定は`kobo.json`に置きます。設定ファイルの場所は`--config`、環境変数`KOBO_CONFIG`、`./kobo.json`の順です。保存先は`KOBO_STORE`、`KOBO_STATE_DB`、`KOBO_MAIL_DB`、`KOBO_AGENTS_DIR`で個別に上書きできます。相対パスは設定ファイルのあるディレクトリ基準です。エージェントのモデルは`models.<agent_id>`、`models.<adapter>`、Markdown既定値の順です。

Gemini CLI 0.45.2で確認した登録例です。WindowsではPythonが`.cmd`ランチャーを安全に解決し、POSIXでは同じ`gemini`名を使えます。

```json
{"commands":{"gemini":["gemini","--approval-mode","plan"]}}
```

実際の非対話契約は`gemini --model <短いモデル名> --output-format text --prompt <短い固定指示>`で、タスクMarkdownの内容は標準入力へ渡し、stdoutをPythonが`result.md`へ原子的に保存します。架空の`--input`、`--output`オプションは使いません。未知のアダプター、存在しない定義、不正値、許可範囲外パスはエラーになります。

```powershell
py -3 -m kobo.cli --config kobo.json gemini-doctor
py -3 -m kobo.cli --config kobo.json gemini-smoke
```

`gemini-doctor`は導入、版、非対話・stdin・モデル・出力形式の対応を、認証情報やプロンプトなしで確認します。`gemini-smoke`は明示実行時だけ、一般的な短文を外部送信します。通常テストと`--dummy`はネットワークを使いません。未導入、認証、タイムアウト、非ゼロ終了、空出力、不正出力を区別し、Gemini失敗時に他AIへ切り替えません。

## 参照渡しとdry-run

```powershell
py -3 -m kobo.cli --config kobo.json --dummy dry-run --work sample-story
```

実際の引数例は次の形です。

```text
dummy --agent-definition C:\repo\agents\writer.md --task C:\repo\.kobo\works\sample-story\runs\run-...\task.md --mail-db C:\repo\.kobo\mail.db --mail-id 2 --run-id run-... --output C:\repo\.kobo\...\result.md
```

メール本文、小説本文、結合済みプロンプトは引数に含みません。Windowsのコマンドラインには約3万文字規模の上限があり、長文を直接載せると起動前に失敗するうえ、秘密情報の露出や再現性低下を招くためです。すべてのプロセス起動は引数配列と`shell=False`を使います。

## 中断と再開

`run-step`は1工程、`run`は完了まで実行します。安全停止要求後やプロセス中断後は次で再開します。

```powershell
py -3 -m kobo.cli --config kobo.json --dummy continue --work sample-story
```

前回`running`だった履歴を`interrupted`として確定し、完了済み工程を飛ばして`next_agent`から新しい実行IDで再開します。失敗履歴は`retry <run-id>`で再試行でき、以前の成果物は上書きしません。

## 対話型URS

作品作成後、一問ずつ要求を収集できます。全コマンドはJSONを返すため、将来のUIからも利用できます。

```powershell
py -3 -m kobo.cli --config kobo.json --dummy work-create my-story "新しい作品" --first-agent urs-maker
py -3 -m kobo.cli --config kobo.json --dummy urs-start --work my-story
py -3 -m kobo.cli --config kobo.json --dummy urs-question --work my-story
py -3 -m kobo.cli --config kobo.json --dummy urs-answer work-name "仮題" --work my-story
py -3 -m kobo.cli --config kobo.json --dummy urs-defer core-axis --work my-story
py -3 -m kobo.cli --config kobo.json --dummy urs-answer work-name "改訂仮題" --work my-story --revise
py -3 -m kobo.cli --config kobo.json --dummy urs-status --work my-story
py -3 -m kobo.cli --config kobo.json --dummy urs-preview --work my-story
py -3 -m kobo.cli --config kobo.json --dummy urs-finalize --work my-story
```

`urs-interactive`は同じAPIを端末から対話的に使います。回答は`confirmed`、`provisional`、`deferred`を持ち、根拠は`user`、`known`、`ai_inference`、`source`を区別します。既知情報の一括投入は本文をargvへ載せず、`urs-start --known-json <path>`で行います。回答履歴はSQLiteへ残り、再起動後も未回答の次の一問から再開します。

プレビューは`URS.preview.md`、確定版は作品ごとの`URS.v001.md`以降へ保存されます。確定版を上書きせず、再改訂は新しい版になります。未回答と保留をAIが補完して確定へ昇格させることはありません。確定時はメールIDと会話系列を保ったまま企画担当へMarkdownパスを渡します。

## 複数企画候補の生成・比較・選択

確定済み`URS.vNNN.md`または読者プロファイルから既定5案を作り、生成担当とは別の`concept-reviewer`が比較します。候補数はCLIで1〜5に変更できます。候補は編集会議用の短い8項目ラフで、`concept-board`が画像なし・通信なしのカードHTMLを作ります。利用者が選択・確定するまでストーリー設計、本文、挿絵へ進みません。

```powershell
py -3 -m kobo.cli --config kobo.json --dummy concept-start --work my-story --count 3
py -3 -m kobo.cli --config kobo.json --dummy concept-status --work my-story
py -3 -m kobo.cli --config kobo.json --dummy concept-list --work my-story
py -3 -m kobo.cli --config kobo.json --dummy concept-show C01 --work my-story
py -3 -m kobo.cli --config kobo.json --dummy concept-compare --work my-story
py -3 -m kobo.cli --config kobo.json --dummy concept-board --work my-story
py -3 -m kobo.cli --config kobo.json --dummy concept-select C02 --work my-story
py -3 -m kobo.cli --config kobo.json --dummy concept-preview --work my-story
py -3 -m kobo.cli --config kobo.json --dummy concept-finalize --work my-story
```

選択以外に`concept-hold`、`concept-reject-all`、`concept-regenerate`があります。長い修正指示はargvへ載せず、`concept-revise C02 --instructions revision.md`でUTF-8 MarkdownまたはJSONのパスを渡します。`concept-history`は選択・修正履歴、`concept-resume`は中断後の未完了地点を返します。

実行時はplannerの創作生成だけをGeminiへルーティングします。`--dummy`成果物にはダミーであることを明記します。比較は独立成果物としてURS適合性、禁止条件、独自性、先読み欲求、主人公の能動性、持続性、中盤停滞、模倣、矛盾リスクについて根拠・長所・弱点・改善案を残します。AI推奨だけでは確定せず、利用者の明示選択後に限り非上書きの`CONCEPT.vNNN.md`を作ります。

## ストーリーバイブルと全体プロット

確定済み`CONCEPT.vNNN.md`を固定参照し、バイブル生成、独立整合性監査、明示承認、版付き確定、全体プロット生成、独立プロット監査、明示承認、版付き確定の順に進めます。

```powershell
py -3 -m kobo.cli --dummy story-start --work my-story
py -3 -m kobo.cli story-show bible_audit --work my-story
py -3 -m kobo.cli story-approve-bible --work my-story
py -3 -m kobo.cli story-finalize-bible --work my-story
py -3 -m kobo.cli --dummy story-start-plot --work my-story
py -3 -m kobo.cli story-show plot_audit --work my-story
py -3 -m kobo.cli story-approve-plot --work my-story
py -3 -m kobo.cli story-finalize-plot --work my-story
```

実運用では`story-architect`と`plotter`だけをGeminiへルーティングし、監査は別担当・別実行ID・別成果物にします。`story-resume`は完成済み成果物を再生成しません。監査は自動承認ではなく、利用者承認後だけ`STORY_BIBLE.vNNN.md`と`PLOT.vNNN.md`を非上書きで確定します。

## 章・シーン設計と本文制作

確定プロットから章単位で開始し、章設計、シーン設計、Geminiによる本文初稿、`prose-reviewer`による独立監査、Geminiによる対象箇所の改稿、差分再監査、利用者承認、版付き本文確定の順に進めます。

```powershell
py -3 -m kobo.cli --dummy manuscript-start 1 --title "第1章" --work my-story
py -3 -m kobo.cli manuscript-show audit --work my-story
py -3 -m kobo.cli manuscript-show revision --work my-story
py -3 -m kobo.cli manuscript-show reaudit --work my-story
py -3 -m kobo.cli manuscript-approve --work my-story
py -3 -m kobo.cli manuscript-finalize --work my-story
```

`--dummy`は実際の小説を書かず、ダミー本文で状態と契約だけを検証します。実運用の初稿・改稿は`writer`のGeminiアダプター専任です。初稿、監査、改稿、再監査、確定本文は別ファイルで保存され、`manuscript-resume`は完成済み工程を再実行しません。監査だけでは確定せず、利用者承認後に`CHAPTER-NNN.vNNN.md`を非上書きで作ります。

## 連続開発ループ

`devloop-status`は`instructions/instruction-*.md`と対応する`result-*.md`、SQLite実行履歴を照合します。`devloop-once`は既定でdry-runです。`devloop.json`へ実装AI・レビューAIの固定コマンドを設定し、明示的に`--execute`を付けた場合だけpull、実装、テスト、レビューを行い、さらに`--publish`を付けた場合だけcommit・pushします。長文はコマンド引数ではなく`{instruction_path}`と`{result_path}`で渡します。

```powershell
py -3 -m kobo.cli devloop-status
py -3 -m kobo.cli devloop-once
py -3 -m kobo.cli devloop-once --execute
py -3 -m kobo.cli devloop-once --execute --publish
py -3 -m kobo.cli devloop-run --execute --max-cycles 3
py -3 -m kobo.cli devloop-run --execute --publish --max-cycles 3
```

実装AI・レビューAI・次指示生成AIは`agents/dev-*.md`で責務を分離し、実コマンドは`devloop.json`へ明示設定します。レビューは`pass/revise/stop`のJSONを返し、`revise`は同じjob IDで上限まで修正、`stop`は仕様判断として停止します。サイクル数、修正回数、AI呼出数（費用単位）、経過時間を上限管理します。実装AIコマンド未設定、テスト・レビュー失敗、result未生成、Git競合・認証等のコマンド失敗では停止します。既定設定のまま外部AIやGit書込みを実行することはありません。

## テスト

```powershell
py -3 -m unittest discover -v
py -3 -m compileall -q kobo mail tests
```

## 正史・台帳更新

ダミー検証: py -3 -m kobo.cli --dummy canon-start 1 --work work-id。状態、草案・監査表示、承認、確定は canon-status、canon-show、canon-approve、canon-finalize。長文修正指示は canon-reject --reason 短文 --instructions Markdownパス。

## 挿絵付きHTML公開

確定本文から、計画・Visual Bible・Antigravity画像生成・オフラインHTML公開を再開可能なセッションとして実行できます。

```powershell
py -3 -m kobo.cli visual-start 1 --work prototype-001
py -3 -m kobo.cli visual-resume --work prototype-001 --session SESSION_ID
py -3 -m kobo.cli visual-approve --work prototype-001 --session SESSION_ID
py -3 -m kobo.cli visual-finalize --work prototype-001 --session SESSION_ID
```

画像は`agy --print`からAntigravityの画像生成機能を呼び出し、生成物の実体・形式・寸法を検証してからHTMLへ取り込みます。公開物は`novels/{work}/illustrated-html-vNNN/`に保存され、外部CDNやJavaScriptに依存しません。
