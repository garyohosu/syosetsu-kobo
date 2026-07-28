# 既存Antigravity連携を小説工房へ移植し、第一話生成を再開する

## 1. 目的

`instruction-20260728-15.md`は、廃止済みの個人向けGemini CLIを本文担当としていたため停止しました。

これは利用者の認証不足ではなく、小説工房側のプロバイダー前提が古いことが原因です。人狼と神託会議ですでに実運用しているAntigravity CLI（`agy`）連携を小説工房へ最小移植し、同じ作業内で第一話本文まで生成してください。

アダプター移植だけを完了として停止してはいけません。今回の最終成果物は次です。

```text
novels/prototype-001/CHAPTER-001.v001.md
novels/prototype-001/CHAPTER-001-AUDIT.v001.md
novels/prototype-001/READER_FEEDBACK.v001.md
```

## 2. 正本と既存実績

最初に`AGENTS.md`を全文読み、Git状態と他プロセスを確認してください。

```powershell
git status --short --branch
git log --oneline --decorate -10
git fetch origin
git pull --ff-only
```

必読:

- `AGENTS.md`
- `instructions/instruction-20260728-15.md`
- `instructions/result-20260728-15.md`
- `novels/prototype-001/READER_PROFILE.v001.md`
- `novels/prototype-001/SELECTED_CONCEPT.v001.md`
- `C:\PROJECT\OracleCouncil\src\oracle_council\adapters\agy.py`（存在する場合）
- `C:\PROJECT\werewolf-game\config\agents.json`（存在する場合）

GitHub上の正本:

- `garyohosu/OracleCouncil`の`src/oracle_council/adapters/agy.py`
- `garyohosu/werewolf-game`の`config/agents.json`

確認済みの実呼出し契約:

```text
agy --print "<prompt>"
```

必要に応じて次を付加します。

```text
--model <model>
--dangerously-skip-permissions
```

`agy`はプロンプトを引数で受け、モデルの生テキストをstdoutへ返します。Gemini CLIの`--output-format text --prompt`契約を流用してはいけません。

## 3. 開始時の訂正

`instructions/result-20260728-15.md`の次の記述は、当時の観測記録として残して構いませんが、再開方針としては無効です。

```text
Gemini CLIの認証を完了する
py -3 -m kobo.cli gemini-doctor
py -3 -m kobo.cli gemini-smoke
```

今回の結果報告では、停止原因を次のように訂正してください。

```text
blocked原因: 個人向けGemini CLI廃止後も、小説工房が旧Geminiアダプターを本文担当としていたプロバイダー移行漏れ
```

## 4. 既存実装の調査

変更前に、Gemini依存箇所を機械的に列挙してください。

```powershell
rg -n -i "gemini|GeminiAdapter|gemini-doctor|gemini-smoke|adapter.*gemini|prose-writing" . `
  -g '!/.git/**' -g '!/.kobo/**'
```

対象を最低限次に分類してください。

1. 実行コード
2. CLI診断コマンド
3. `kobo.json`
4. エージェント定義
5. テスト
6. README／SPEC／QandA等の現行仕様
7. 過去の指示書・結果報告（履歴なので原則書換えない）

過去記録を一括置換して歴史を書き換えないでください。現在有効なコード、設定、エージェント定義、README、仕様だけを更新します。

## 5. Antigravityアダプター

### 5.1 実装

`kobo/agy.py`または既存構成に自然な同等ファイルを追加してください。

神託会議の`AgyAdapter`から、今回必要な次の部分だけを移植します。

- `agy --version`による存在・稼働確認
- `agy --print <prompt>`による単発実行
- optional `--model`
- `--dangerously-skip-permissions`
- UTF-8 stdout/stderr
- timeout
- 非ゼロ終了
- 空出力
- セッション／利用枠超過の識別
- コマンド不存在の識別
- Windowsコマンドライン長の事前検査
- 出力ファイルへの原子的保存

神託会議固有のphase schema、JSON抽出、claim、audit schema、AgentRequest／AgentResult等はコピーしないでください。

### 5.2 Windows引数長

`agy`はstdinやprompt-fileではなく、プロンプト全文をargvへ載せます。Windowsでは、完全にquoteされたコマンドラインをUTF-16 code unitで測定し、安全上限30,000を超える場合は起動前に診断可能なエラーへしてください。

```python
subprocess.list2cmdline(command)
```

で実コマンドライン相当を作り、UTF-16LEバイト数÷2で測ります。

長すぎる場合、`COMMAND_NOT_FOUND`へ誤分類してはいけません。

### 5.3 小説本文出力

AntigravityのstdoutはJSONではなくMarkdown本文です。本文生成ではstdoutをそのままUTF-8で原子的に保存してください。

次を不正出力とします。

- 空または空白のみ
- NULを含む
- 10,000,000文字を超える
- UTF-8として安全に扱えない

## 6. オーケストレーター統合

最低限、次を変更してください。

- `Orchestrator._adapter()`が`adapter: agy`を専用`AgyAdapter`へ解決する
- `run_step()`がAgy固有エラーを診断可能に記録する
- prose-writing工程で`agy`を許可する
- Geminiへの無言fallbackを作らない
- `--dummy`では引き続き外部AIを起動しない

文章作成担当を`agy`へ変更した後も、別プロバイダーへ無言で切り替えないという原則は維持します。

## 7. 設定とエージェント定義

`kobo.json`の現在値は旧Gemini前提です。現行設定を次の方向へ変更してください。

```json
"commands": {
  "dummy": ["dummy"],
  "agy": ["agy"]
}
```

モデルはAntigravityの既定モデルを使うなら、架空または旧Geminiモデル名を設定しないでください。実環境で`agy --help`と既存の人狼・神託会議設定から確認できる場合だけ指定してください。

現在有効なエージェント定義を調査し、本文・企画・バイブル・プロット等、従来Geminiを担当としていた創作生成工程を`adapter: agy`へ変更してください。

独立レビュー担当まで機械的にすべて同じアダプターへ変更する必要はありません。既存設計上の役割と実際に利用可能なプロバイダーを確認し、変更理由を結果報告へ記載してください。

## 8. CLI診断

次を追加してください。

```text
agy-doctor
agy-smoke
```

### `agy-doctor`

外部へ小説本文や秘密情報を送らず、最低限次を返します。

- executable
- resolved path/name
- installed
- version
- `--print`対応
- `--model`対応
- 非対話実行に必要なオプション

### `agy-smoke`

短い一般文だけを送信し、日本語で`接続確認成功`を返せるか確認します。

Gemini診断コマンドは過去互換のため残しても構いませんが、READMEや再開手順では`agy-doctor`／`agy-smoke`を正としてください。削除する場合は関連テストと説明を更新してください。

## 9. テスト

外部AIを呼ばないユニットテストを追加してください。

最低限:

1. `agy --print <prompt>`のコマンド形
2. model有無
3. `--dangerously-skip-permissions`
4. Windows `.cmd`解決が必要な場合の扱い
5. UTF-8日本語入出力
6. timeout
7. command not found
8. nonzero exit
9. session/quota limit識別
10. empty output
11. invalid output
12. 30,000 UTF-16 unit以下は実行可能
13. 超過は起動前にprompt-too-large
14. prose-writingの`adapter: agy`許可
15. dummyでは外部起動なし
16. `agy-doctor`／`agy-smoke`

神託会議のテストを参考にしてよいですが、OracleCouncil固有モデルを持ち込まないでください。

実装後:

```powershell
py -3 -m unittest discover -v
py -3 -m compileall -q kobo mail tests
git diff --check
```

## 10. 実機確認

ユニットテスト成功後、次を実行してください。

```powershell
py -3 -m kobo.cli agy-doctor
py -3 -m kobo.cli agy-smoke
```

`agy-smoke`が利用枠超過、未ログイン、コマンド不存在等で失敗した場合は、正確な分類を記録してください。ただし旧Gemini認証へ戻らないでください。

## 11. 第一話生成の再開

`agy-smoke`成功後、同じ作業を続けて`instruction-20260728-15.md`の未完了成果物を作成してください。

```text
novels/prototype-001/CHAPTER-001.v001.md
novels/prototype-001/CHAPTER-001-AUDIT.v001.md
novels/prototype-001/READER_FEEDBACK.v001.md
```

### 本文生成用入力

少なくとも次を固定参照として読み込み、Antigravityへ渡してください。

- `novels/prototype-001/READER_PROFILE.v001.md`
- `novels/prototype-001/SELECTED_CONCEPT.v001.md`
- 本指示書の本文条件

プロンプトは必要な部分だけを結合し、Windows安全上限内であることを事前確認してください。

Antigravityにリポジトリを自由探索させて無関係な資料を大量投入するのではなく、固定参照の内容を明示的に結合する方式を優先してください。

### 本文条件

`instruction-15`の条件をすべて維持します。

- 5,000〜10,000字
- 目標7,000字前後
- 冒頭800字以内に六台の給湯器故障と日没までの期限
- 2,000字以内にリオの独自仮説
- ミナの帳面が解決に不可欠
- 給湯器だけでなく検査・記録の仕組みも直す
- 工房、食堂、診療所、温かい食事
- ミナが正式な役割を得る
- 最後に禁止刻印を示す

### 監査と改稿

初稿後、独立した別実行で7項目監査を作成してください。最大1回だけ対象箇所を改稿して構いません。

アダプターの動作確認を理由に、本文生成を次セッションへ先送りしないでください。

## 12. コミット

最低限、次の単位でコミットしてください。

1. Antigravity移植、設定、テスト
2. 第一話本文、監査、読者評価票
3. 結果報告

各単位をpushしてください。利用枠低下時は`AGENTS.md`に従います。

## 13. 結果報告

作成先:

```text
instructions/result-20260728-16.md
```

記載項目:

- Gemini停止原因の訂正
- 参照した人狼・神託会議の実装
- Antigravity実呼出し契約
- 変更ファイル
- テスト件数
- `agy-doctor`結果
- `agy-smoke`結果
- 本文文字数
- 本文生成回数
- 監査・改稿回数
- 3つの成果物パス
- コミットSHA
- push結果
- 未解決事項

第一話が5,000字未満、ダミー、または未作成なら完了扱いにしないでください。
