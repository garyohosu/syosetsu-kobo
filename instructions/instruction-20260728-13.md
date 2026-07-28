# devloopのレビュー差分完全化とWindows外部コマンド処理の残課題修正

## 1. 目的

現在の`devloop`は、実装・テスト・レビュー・修正再実行の連鎖自体は動作するようになりました。しかし、レビューAIへ渡す差分を`git diff --binary`だけで生成しているため、未追跡の新規ファイルが差分へ含まれません。

`instruction-20260727-12.md`の再実行では、実装AIが次の新規ファイルを実際に作成しました。

- `kobo/canon.py`
- `agents/canon-updater.md`
- `agents/canon-auditor.md`

一方、レビューAIへ渡った差分には追跡済みファイルの変更しか含まれず、レビューAIは新規ファイルを「存在しない」と誤認して`revise`を返しました。これは実装AIの失敗ではなく、`devloop`のレビュー差分生成不具合です。このまま再試行しても同じ誤判定を繰り返し、修正回数上限で`blocked`になります。

今回の最優先完了条件は、実際のGitインデックスを変更せずに、追跡済み変更・未追跡新規ファイル・削除ファイルをすべて含むレビュー用パッチを生成し、レビューAIが実装全体を確認できるようにすることです。

あわせて、前回保守で未対応のまま残した外部コマンドのUTF-8復号問題を修正し、再発防止テストを追加してください。

## 2. 現在までに完了している修正

次の修正は既にローカルコミット済みです。履歴と実装を確認し、巻き戻しや重複実装をしないでください。

- `9f7370d fix: make development loop reliable on Windows`
  - `codex exec`に存在しない`--ask-for-approval`を`devloop.json`から削除
  - Windows StoreのPythonスタブを避けるため、テストコマンドを`py -3`へ変更
  - `kobo/devloop.py`の`default_runner`へUTF-8復号設定を追加
  - 日本語UTF-8出力の回帰テストを追加
- `fb60c47 docs: update development loop handover`
  - `kihitsugi.md`から`hikitsugi.md`への修正
  - `dream.md`への実行記録追記
  - 停止報告を`docs/devloop-runs/blocked-20260727-12.md`へ退避

開始時に実際のコミット履歴を確認し、短縮SHAが異なる場合はコミットメッセージと差分で同一性を確認してください。

## 3. 作業開始前の確認

1. 実行中の`devloop`、Codex、テストプロセスがないことを確認してください。現在の`instruction-20260727-12.md`実行が継続中なら、外部からkillせず自然終了を待ってください。外部killすると`dev_jobs.status='running'`が残り、自動再開できなくなるためです。
2. 次を確認してください。

   ```powershell
   git status --short --branch
   git remote -v
   git log --oneline --decorate -10
   py -3 -m kobo.cli devloop-status
   ```

3. 現在の実行が自然終了した場合、対象ジョブが`blocked`となり、`_claim_job`が同一`job_id`を再利用できることを確認してください。DBを手動更新しないでください。
4. 未コミットの`instruction-20260727-12.md`実装成果物が残っている場合、勝手に削除、reset、stash、checkoutしないでください。内容を保全したまま、今回の保守修正と混同しないよう確認してください。
5. 少なくとも次を読んでください。
   - `hikitsugi.md`
   - `dream.md`
   - `devloop.json`
   - `kobo/devloop.py`
   - `tests/test_devloop.py`
   - `kobo/gemini.py`
   - `mail/agent_mail.py`
   - `instructions/instruction-20260727-12.md`
   - `docs/devloop-runs/blocked-20260727-12.md`

## 4. 最優先修正：未追跡ファイルを含むレビュー差分

### 4.1 禁止する実装

実リポジトリのインデックスに対して、次のような処理を行ってはいけません。

```powershell
git add -A -N
git diff --binary
git reset --quiet
```

`git add -N`は内容をステージしなくても実インデックスを変更します。また、後続の`git reset`は利用者が意図してステージした変更を解除する危険があります。開始時点がクリーンであるという前提だけに依存せず、副作用のない方式にしてください。

### 4.2 必須方式：一時Gitインデックス

実インデックスを変更せず、一時的な`GIT_INDEX_FILE`を使用してレビュー用差分を生成してください。概念手順は次のとおりです。

1. `.kobo`配下またはOSの安全な一時領域に、一意な一時インデックスパスを確保する。
2. 一時インデックスだけを参照する環境変数`GIT_INDEX_FILE`を設定する。
3. 一時インデックスへ`HEAD`を読み込む。

   ```text
   git read-tree HEAD
   ```

4. 一時インデックス上だけで、未追跡ファイルをintent-to-addとして登録する。

   ```text
   git add -A -N -- .
   ```

5. 同じ一時インデックスを使って、レビュー用パッチを生成する。

   ```text
   git diff --binary -- .
   ```

6. 成功・失敗・タイムアウト・例外のいずれでも、`finally`相当で一時インデックスと関連ロックファイルを削除する。
7. 実際の`.git/index`、ステージ状態、作業ツリー内容を変更しない。

実装は専用メソッド（例：`_build_review_patch()`）へ分離し、通常の`_run()`から環境変数を安全に渡せるようにしてください。親プロセスの環境を丸ごと置換せず、既存環境を複製した上で`GIT_INDEX_FILE`だけを上書きしてください。

### 4.3 パッチに含める対象

レビュー用パッチには最低限、次を含めてください。

- 追跡済みファイルの変更
- 未追跡の新規テキストファイル
- 未追跡の新規バイナリファイル
- 追跡済みファイルの削除
- ファイルモード変更をGitが検出する環境ではその変更

次は含めてはいけません。

- `.gitignore`で除外された`.kobo/`、一時DB、レビュー成果物
- `.git/`内部
- 一時インデックス自身
- リポジトリ外のファイル

パッチが空の場合は、実装AIが「変更なし」を明示した場合を除き、そのままレビューへ進まず診断可能なエラーにしてください。少なくとも`git status --porcelain`が非空なのにパッチが空なら異常です。

## 5. レビュー前の必須検査

レビューAIを呼ぶ前に、次をコード側で確認してください。

1. `git status --porcelain`で変更対象を取得する。
2. 生成したパッチが0バイトでないことを確認する。
3. 未追跡ファイルが存在する場合、その各パスがパッチの`diff --git`または`new file mode`節へ現れることを確認する。
4. 指示書が必須テスト追加を要求している場合、テスト変更が見当たらないことを自動で合格扱いしない。これは一般的な仕様判定をコードへ埋め込むのではなく、レビューAIへ新規ファイルを含む完全な差分を渡して判断可能にすることを主眼としてください。

今回の`instruction-20260727-12.md`では`tests/`更新が明示要求されています。再実行時にテスト追加がない場合は、レビューAIが完全なパッチを見た上で正しく`revise`を返せる状態にしてください。

## 6. 残存するUTF-8復号問題

前回と同じ原因が少なくとも次に残っています。

- `kobo/gemini.py`
  - Gemini実行
  - `--version`
  - `--help`
- `mail/agent_mail.py`の`SubprocessHandler`
- リポジトリ検索で見つかる、`text=True`または文字列入出力を使いながら`encoding`が暗黙の外部コマンド実行

次を満たしてください。

- 日本語Windowsのロケール既定cp932へ依存しない。
- 成果物となるGemini応答やメール応答はUTF-8で厳密に復号し、復号不能時は診断可能な例外として停止する。
- 診断表示だけに`errors="replace"`を使う場合は、成果物処理と明確に分離する。
- 呼出し側が明示した`encoding`・`errors`を上書きしない。
- バイナリモードへ文字列用引数を付けない。
- 共通runnerへ統合する場合、既存の例外分類とタイムアウト動作を維持する。

## 7. 状態管理の追加改善

最優先修正とUTF-8修正が完了した後、可能な範囲で次も実装してください。ただし、範囲が大きくなり`instruction-20260727-12.md`の再開を遅らせる場合は、独立した次指示書へ分離して構いません。

- dirtyな作業ツリーではAI呼出し・job claim・`git pull`より前に停止する。
- 実装AIの試行報告を`.kobo/devloop/{job_id}/`へ保存し、レビュー`pass`後だけ正式な`instructions/result-*.md`へ昇格する。
- `blocked`と利用者判断が必要な`stopped`を区別する。
- 再試行時のattempt番号とtests/diff/review/resultを単調増加させ、過去成果物を上書きしない。
- 複数サイクルのローカルcheckpointとpushを分離する。

これらを今回分離した場合は、`instructions/result-20260728-13.md`へ未実装境界と次指示書名を明記してください。

## 8. 必須テスト

既存テストを維持した上で、最低限次を追加してください。

1. 追跡済み変更がレビュー用パッチへ含まれる。
2. 未追跡の新規テキストファイルが`new file`としてパッチへ含まれ、本文も確認できる。
3. 未追跡の新規バイナリファイルがバイナリ差分としてパッチへ含まれる。
4. 追跡済みファイルの削除がパッチへ含まれる。
5. `.gitignore`対象ファイルはパッチへ含まれない。
6. 実際のGitインデックスの内容・ステージ状態が差分生成前後で変化しない。
7. 利用者が事前にステージした変更を想定した単体テストでも、実インデックスを解除・変更しない。
8. 一時インデックスと`.lock`が正常終了後に残らない。
9. `git read-tree`、`git add -N`、`git diff`のいずれかが失敗しても一時ファイルが残らない。
10. 日本語を含む新規ファイルのパッチがUTF-8で欠落しない。
11. `git status --porcelain`が非空なのにパッチが空の場合、レビューAIを呼ばず失敗する。
12. `kobo/canon.py`、`agents/canon-updater.md`、`agents/canon-auditor.md`相当の新規ファイルがレビュー用パッチに現れる回帰テスト。
13. Geminiの日本語UTF-8入出力が欠落・置換・空出力化しない。
14. メール`SubprocessHandler`が日本語本文を渡し、日本語応答を正しく取得する。
15. 呼出し側の明示encoding/errorsを尊重する。
16. 全既存テストが成功する。

最低限、次を実行してください。

```powershell
py -3 -m unittest tests.test_devloop -v
py -3 -m unittest discover -v
py -3 -m compileall -q kobo mail tests
git diff --check
```

テスト用一時リポジトリを作成し、実Gitコマンドを使う統合テストも最低1件追加してください。モックだけで一時インデックス方式を検証したことにしないでください。

## 9. 実装・コミット・再実行の順序

1. 現在の`instruction-20260727-12.md`実行を自然終了させる。
2. ジョブが`blocked`となり、同一`job_id`で再利用可能なことを確認する。
3. 今回のdevloop保守修正だけを実装する。
4. 必須テスト、全回帰テスト、`compileall`、`git diff --check`を実行する。
5. devloop保守修正を、`instruction-20260727-12.md`の未完了成果物と混ぜずに明示的にステージしてコミットする。

   推奨コミットメッセージ：

   ```text
   fix: include untracked files in devloop review patches
   ```

6. 作業ツリーをクリーンにする。`instruction-20260727-12.md`の失敗試行が残した成果物をどう扱うかは、内容を確認して保全する。無断削除しない。
7. `py -3 -m kobo.cli devloop-status`で`instruction-20260727-12.md`が再試行可能であることを確認する。
8. `instruction-20260727-12.md`を再実行する。
9. レビュー用`diff-N.patch`に、少なくとも次が含まれることを確認する。
   - `kobo/canon.py`
   - `agents/canon-updater.md`
   - `agents/canon-auditor.md`
   - 追加されたcanon関連テスト
10. レビューAIが完全な差分を読んで判定したことを確認する。

今回の指示書実装では、利用者から別途明示された場合を除きpushしないでください。force pushは禁止です。

## 10. 変更範囲

実装に必要な範囲で次を変更して構いません。

- `kobo/devloop.py`
- `kobo/gemini.py`
- `mail/agent_mail.py`
- subprocess共通処理を置く新規モジュール
- `tests/test_devloop.py`
- Gemini・メール処理の関連テスト
- `kobo/cli.py`
- `README.md`
- `SPEC.md`
- `QandA.md`
- `hikitsugi.md`
- `dream.md`
- `instructions/result-20260728-13.md`

正史・台帳機能そのものの仕様や`kobo/canon.py`の機能設計は今回の保守対象外です。未完了のinstruction-12成果物を、devloop修正の都合で書き換えないでください。

## 11. 受入条件

- レビューAIが追跡済み変更だけでなく、未追跡の新規ファイルの内容も確認できる。
- 実際のGitインデックス、ステージ状態、作業ツリーへ差分生成由来の副作用を残さない。
- 正常終了・異常終了のいずれでも一時インデックスを残さない。
- `.gitignore`対象や`.kobo/`をレビュー用パッチへ混入させない。
- 日本語UTF-8のGit差分、Gemini応答、メール応答が無言で空にならない。
- `instruction-20260727-12.md`を同一`job_id`で再実行できる。
- 再実行時のレビュー用パッチに`kobo/canon.py`と`agents/canon-*.md`とcanon関連テストが含まれる。
- レビューAIが完全な実装差分に基づいて`pass`または正当な`revise`を返す。
- 全回帰テスト、`compileall`、`git diff --check`が成功する。

## 12. 結果報告

結果を`instructions/result-20260728-13.md`へ記録してください。最低限、次を含めてください。

- 開始時のブランチ、HEAD、originとの差
- 実行中だったinstruction-12ジョブの最終状態とjob ID
- 未追跡ファイル漏れの再現結果
- 採用した一時インデックス方式の詳細
- 実インデックスが変化していないことの検証方法
- パッチへ含まれた追跡済み変更、新規ファイル、削除、バイナリの例
- 一時インデックスのcleanup確認
- 残存UTF-8箇所の監査・修正結果
- 変更ファイル一覧
- 追加したテスト一覧
- テスト件数、成功、失敗、スキップ
- `compileall`、`git diff --check`の結果
- 作成したコミットハッシュと対象ファイル
- pushの有無
- instruction-12再実行時のattempt番号
- 再実行時のレビュー用パッチに`kobo/canon.py`、`agents/canon-updater.md`、`agents/canon-auditor.md`、canon関連テストが含まれた証拠
- レビューAIの最終判定
- 未解決事項と次の指示書
- 最終の`git status --short --branch`

完了していない工程を完了と記載しないでください。利用者判断が必要な場合は、停止地点、選択肢、既に安全に完了した範囲、再開コマンドを具体的に報告してください。
