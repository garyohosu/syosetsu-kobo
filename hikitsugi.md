# 次にやる作業

## 題目

正史・台帳更新工程の指示書を用いた開発ループ実地試験の再開（作業ツリーをクリーンにしてから再実行）

## 現在の状況

`確定本文から正史・各種台帳を更新し、次章へ制作状態を引き継ぐ工程`の指示書は`instructions/instruction-20260727-12.md`として展開済み。この指示書を対象に`devloop-run --execute`を実行する過程で、devloop実行層の不具合を**合計3件**発見・修正した。前回引き継ぎ時点の2件に加え、今回1件を追加で発見している。

1. **実行ファイル解決**（修正済み・前回）: Windowsで`subprocess.run(["codex",...],shell=False)`が`codex`（npmの`.cmd`シム）を解決できず`WinError 2`で失敗していた。`kobo/devloop.py`に`resolve_command`/`default_runner`を追加し、`shutil.which`経由でPATHEXTを含めて解決してから起動する。
2. **blockedジョブの再試行**（修正済み・前回）: `dev_jobs.instruction`のUNIQUE制約により、blockedジョブの再検出時に新`job_id`でINSERTしてUNIQUE制約違反になっていた。`_claim_job`で既存instructionの状態を確認し、blockedは同一`job_id`を再利用、`stopped`は自動再試行しない。
3. **出力の無言消失（cp932復号エラー）**（修正済み・今回）: `_run`が`subprocess.run(...,text=True)`だけを指定していたため、Pythonがロケール既定エンコーディング（日本語Windowsでは**cp932**）で復号していた。`git diff`やAI CLIが出力する日本語UTF-8を復号できず、reader threadが`UnicodeDecodeError`で落ちる。このとき**returncodeは0のまま、stdoutだけが空文字列として返る**ため、`diff-N.patch`が0バイトになり、テスト結果やエラーメッセージも失われていた。`default_runner`で`encoding="utf-8"`／`errors="replace"`を明示（呼び出し側の明示指定は尊重）。回帰テスト3件を追加。

また、`devloop.json`について2点修正した。

- `implement`/`review`/`generate_next`から`--ask-for-approval never`を削除した。`codex-cli 0.145.0`の`codex exec`にこのオプションは存在せず、`exit=2`（引数解析エラー）で即失敗していた。削除しても承認プロンプトは出ない（`codex exec`は非対話実行で、起動バナーが`approval: never`を表示することをスモークで確認済み）。
- `tests`の`python`を`py -3`へ変更した。この環境の`python`はMicrosoft Storeのアプリ実行エイリアス（`WindowsApps\python.exe`）に解決され、実行すると`exit=9009`で何も動かない。`resolve_command`はこのスタブを正しく見つけてしまうため、テスト工程が必ず失敗する状態だった。実体は`py -3`（Python 3.14.0）。

## 実行結果（今回）

`devloop-once --execute`を1回実行し、**実装→テスト→差分→レビュー→revise→再実装**の連鎖が動作することを確認した。上記1〜3の修正により、devloop実行層自体のブロッカーは解消している。

ただしジョブは3回のattemptすべてで`revise`となり、最終的に`blocked`（`修正回数上限に達しました`）で停止した。

- ジョブ: `dev-96739631084e41e3a8c039b1a41b0001`（`instruction-20260727-12.md`）
- リポジトリへの実装変更は依然として一切発生していない。

## 残っているブロッカー（要判断）

**実装AIが、未コミット変更を検出して意図的に停止している。** これは不具合ではなく指示書どおりの正しい挙動である。

- `instructions/instruction-20260727-12.md` §2-2 が「`main`と`origin/main`が同期し、作業ツリーがクリーンであること」を開始条件にしている。
- 同L37が「今回の破棄対象ではない未コミット変更を発見した場合は、勝手に削除、上書き、stash、reset、checkout、rebase継続をせず、作業を停止して報告」と定めている。

実装AIが生成した`instructions/result-20260727-12.md`は、この停止を正直に報告している（全項目「未実施」。虚偽の完了報告ではない）。

停止時点の未コミット変更は次のとおり。

| 変更 | 由来 |
|---|---|
| `M devloop.json` | 上記のdevloop修正（codexオプション削除、`py -3`化） |
| `M kobo/devloop.py` | 上記の不具合3（cp932）の修正 |
| `M tests/test_devloop.py` | 不具合3の回帰テスト追加 |
| `D kihitsugi.md` / `?? hikitsugi.md` | 引き継ぎファイルのリネーム（利用者の操作） |
| `?? instructions/result-20260727-12.md` | 実装AIが書いた停止報告 |

## 次にやること

1. **上記の未コミット変更の扱いを決める。** 開発ループを進めるには作業ツリーをクリーンにする必要があり、そのためにはdevloop修正のコミットが要る。前回引き継ぎの「コミット・pushは行わない」方針と衝突するため、ここは利用者の判断事項。想定される選択肢は次のとおり。
   - devloop修正3ファイル（`devloop.json`、`kobo/devloop.py`、`tests/test_devloop.py`）と引き継ぎファイルのリネームをコミットしてから、ループを再実行する。
   - 別ブランチを切ってからコミットする。
   - `git stash`で退避する（ただし実装AIはstashを禁じられているため、人間側の操作として行う）。
2. **`instructions/result-20260727-12.md`の扱いを決める。** `discover()`は`result-*.md`が存在するinstructionを完了扱いにするため、このファイルが残っていると`instruction-20260727-12.md`は二度と検出されない。実装は一切行われていないので、再実行するなら削除または改名が必要。内容は停止報告として意味があるため、勝手に削除していない。
3. 作業ツリーをクリーンにしたうえで再実行する。blockedジョブは`_claim_job`が同一`job_id`を再利用するため、DBの手動操作は不要。

```powershell
py -3 -m kobo.cli devloop-run --execute --max-cycles 3
```

4. 今回も`--publish`は付けず、devloop自身によるコミット・pushは行わない。実装、テスト、独立レビュー、修正再依頼、result生成、次instruction生成が正しく連鎖したことを確認してから、必要に応じて`--publish`付きで実行する。

## 未修正の同種不具合（今回は範囲外・要対応）

不具合3と**同じ原因**の箇所が他に3つ残っている。いずれも今回のdevloop実行を妨げないため、範囲を広げず未修正のままにした（修正すると未コミット変更がさらに増え、上記のブロッカーを悪化させるため）。ただし放置すると創作工程で確実に問題になる。

| 箇所 | 内容 | 危険度 |
|---|---|---|
| `kobo/gemini.py:64` | `runner(..., input=prompt, text=True, capture_output=True, ...)`。エンコーディング未指定。**プロンプトの送信と応答の受信の両方**が日本語。Geminiは創作文章工程の担当なので、応答は必ず日本語UTF-8になる | 高 |
| `mail/agent_mail.py:449` | `subprocess.run(self.command, input=item.body, text=True, ...)`。メール本文は日本語 | 高 |
| `kobo/gemini.py:88-89` | `--version`／`--help`の取得。通常はASCIIのため実害は出にくい | 低 |

対応は`kobo/devloop.py`の`default_runner`と同じで、`encoding="utf-8"`／`errors="replace"`を明示するだけでよい。`kobo/orchestrator.py:197`の`subprocess.run`は`capture_output`を使っていないため、この問題の対象外。

## 環境メモ

- `python`は使用不可（Storeエイリアス、`exit=9009`）。**必ず`py -3`を使う。**
- `codex-cli 0.145.0`の`codex exec`に`--ask-for-approval`は存在しない。`-s/--sandbox`と`--dangerously-bypass-approvals-and-sandbox`のみ。
- コンソールのロケールはcp932。外部コマンドの出力を扱うコードでは、エンコーディングを明示しないと日本語出力が無言で消える。

## 安全条件（継続）

- 未承認本文、監査指摘、破棄された改稿を正史へ取り込まない。
- 上流資料や過去の確定台帳を上書きしない。
- 不明な設定を推測で確定せず、未解決事項として停止または承認待ちにする。
- Geminiが必要な創作文章工程で利用不能な場合、別AIへ無言で切り替えない。
- 長文本文をコマンドライン引数へ載せず、ファイルパス・実行ID・メールIDで参照する。
- Git競合、認証失敗、AIコマンドの実行不能、仕様矛盾、破壊的変更が必要な場合、開発ループ自体の不具合で状態を破損しうる場合は停止する。

## 完了条件

- 確定本文だけから正史と5種の台帳を生成・監査・承認・確定できる。
- 次章が直前の承認済み状態を固定参照できる。
- 中断再開しても完了済み工程を重複実行しない。
- 既存のURS、企画、バイブル、プロット、本文、メール、開発ループの全回帰テストが成功する。

### 現時点の回帰テスト状況

```text
py -3 -m unittest discover -v   -> Ran 113 tests, OK (skipped=1)
py -3 -m compileall -q kobo mail tests -> exit=0
git diff --check -> exit=0
```

前回は110件。今回の不具合3の回帰テスト3件を追加して113件になっている。
