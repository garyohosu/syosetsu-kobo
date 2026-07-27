# 次にやる作業

## 題目

正史・台帳更新工程の指示書を用いた開発ループ実地試験の再開（`codex` CLI起動不具合の解消）

## 現在の状況

`確定本文から正史・各種台帳を更新し、次章へ制作状態を引き継ぐ工程`の指示書は`instructions/instruction-20260727-12.md`として展開済み（対応する`instructions/result-20260727-12.md`は未作成＝pending）。この指示書を対象に`devloop-run --execute --max-cycles 3`（`--publish`なし）を実行する過程で、devloop実行層の不具合を2件発見・修正済み。

1. **実行ファイル解決**（修正済み）: Windowsで`subprocess.run(["codex",...],shell=False)`が`codex`（npmの`.cmd`シム）を解決できず`WinError 2`で失敗していた。`kobo/devloop.py`に`resolve_command`/`default_runner`を追加し、`shutil.which`経由でPATHEXTを含めて実行ファイルを解決してから起動するよう変更。codex/claude/gemini/grok等どの外部CLIにも適用される共通処理。テスト追加済み、既存テスト含め全件成功。
2. **blockedジョブの再試行**（修正済み）: `dev_jobs.instruction`はUNIQUE制約があるため、失敗（blocked）したジョブを`discover()`が再度pendingとして検出しても、`once()`が新しい`job_id`でINSERTしようとしてUNIQUE制約違反になっていた。`_claim_job`を追加し、既存instructionの状態を明示的に確認したうえで、blockedジョブは同一`job_id`を再利用して`status='running'`へ戻し、`stopped`ジョブは自動再試行しないよう変更。テスト追加済み、既存テスト含め全件成功。

## 残っているブロッカー

`devloop.json`の`implement`/`review`/`generate_next`コマンドはいずれも`codex exec --sandbox workspace-write --ask-for-approval never ...`という形式だが、現在インストールされている`codex-cli 0.145.0`の`codex exec`には`--ask-for-approval`オプションが存在しない（`codex exec --help`で確認済み。存在するのは`-s/--sandbox <read-only|workspace-write|danger-full-access>`と、サンドボックス自体を無効化する別物の`--dangerously-bypass-approvals-and-sandbox`）。そのため`codex exec`起動が`exit=2`（引数解析エラー）で即失敗し、ジョブ`dev-0b623a40003b424cba5557cb30584183`が`blocked`のまま止まっている。まだリポジトリへの実編集は一切発生していない。

## 次にやること

1. `devloop.json`の`implement`/`review`/`generate_next`から`--ask-for-approval never`を削除する（`--sandbox workspace-write`はそのまま維持する想定）。削除前に、承認プロンプトを避ける手段が現行版で本当に不要なのか（`codex exec`は非対話実行のため元々承認プロンプトを出さない可能性がある）を`codex exec --help`および実際の手動スモークで確認する。
2. 修正後、まず`codex exec --sandbox workspace-write -C <root> "<短い非機密プロンプト>"`で単発の起動確認を行う。
3. 既存のblockedジョブ（`dev-0b623a40003b424cba5557cb30584183`、対象は`instructions/instruction-20260727-12.md`）を無理に削除せず、そのまま`devloop-run`を再実行して`_claim_job`による再利用を確認する。

```powershell
python -m kobo.cli devloop-run --execute --max-cycles 3
```

4. 今回も`--publish`は付けず、コミット・pushは行わない。実装、テスト、独立レビュー、修正再依頼、result生成、次instruction生成が正しく連鎖したことを確認してから、必要に応じて`--publish`付きで実行する。

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
