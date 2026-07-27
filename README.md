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

Gemini CLIの登録例です。テンプレートに展開できるのは、モデル、入力・出力・エージェント定義のパス、メールDB／ID、実行ID／ディレクトリという短い参照だけです。

```json
{"commands":{"gemini":["gemini","--model","{model}","--input","{task_path}","--output","{output_path}"]}}
```

未知のアダプター、存在しないエージェント定義、不正な値、許可範囲外のパスはエラーになります。CLIは`{"ok":...}`形式のJSONを返し、失敗時は終了コード1です。

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

## テスト

```powershell
py -3 -m unittest discover -v
py -3 -m compileall -q kobo mail tests
```
