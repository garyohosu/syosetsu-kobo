# 正史・台帳工程の確定処理とメール系列を完成させる

## 1. 目的

`instructions/instruction-20260727-12.md`で実装した正史・台帳工程について、修正済みdevloopによる完全な差分レビューで残った次の2件だけを解決してください。

1. `CanonManager.finalize()`における版予約、5種成果物の公開、SQLite確定処理の原子性・中断再開性
2. `CanonManager.reject()`における、元の制作メール系列を維持した修正指示の送信

今回の目的は正史・台帳工程を完成させることであり、新しい制作工程、本文生成機能、一般的な大規模リファクタリングを追加することではありません。

現在の未完成実装はWIPコミット`c0a10da`として保全済みです。既存の`kobo/canon.py`、`tests/test_canon.py`、エージェント定義、仕様更新を活かし、最初から作り直さないでください。

## 2. 作業開始前の確認

最初に`AGENTS.md`を全文読み、その共通指示に従ってください。

次を確認してください。

```powershell
git status --short --branch
git remote -v
git log --oneline --decorate -12
py -3 -m kobo.cli devloop-status
```

開始条件:

- `main`と`origin/main`が同期している
- 作業ツリーがcleanである
- 他のClaude Code、Codex、AntiGravity、devloop等が同じ作業ツリーを書込み中ではない
- `instructions/result-20260728-13.md`が存在する
- `instructions/instruction-20260727-12.md`と停止記録を確認できる

少なくとも次を全文または関連箇所まで読んでください。

- `AGENTS.md`
- `instructions/instruction-20260727-12.md`
- `instructions/result-20260728-13.md`
- `docs/devloop-runs/stalled-20260727-12-retry-attempt3.md`
- `kobo/canon.py`
- `tests/test_canon.py`
- `mail/agent_mail.py`
- `mail/test_agent_mail.py`
- `kobo/manuscript.py`
- `kobo/story_design.py`
- `kobo/orchestrator.py`
- `SPEC.md`
- `QandA.md`

開始時点で想定外の未コミット変更、履歴分岐、実行中プロセスを見つけた場合は、勝手にstash、reset、checkout、clean、rebase、mergeせず停止して報告してください。

## 3. 現在確認されている問題

### 3.1 `finalize()`の不整合リスク

現状は次の順序で処理しています。

1. DBから次の版番号を取得
2. 5つの最終Markdownを1ファイルずつ公開
3. その後SQLiteへ`canon_documents`を登録
4. セッションと作品状態を完了へ更新
5. 次工程メールを送信

このため、途中で例外、プロセス終了、ディスク障害、DB障害が起きると、次のような半端な状態が残り得ます。

- 一部の最終ファイルだけ存在する
- 全ファイルは存在するがDBには確定記録がない
- DBでは完了だが必要なファイルが不足している
- 再実行時に別の版番号を予約してしまう
- 同じセッションが二重に公開される

ファイルシステムとSQLiteを単一のACIDトランザクションにはできません。したがって「例外が絶対に起きない」と仮定するのではなく、外部から見える公開単位を原子的にし、途中終了後に同じ確定処理を安全に再開できるプロトコルを実装してください。

### 3.2 `reject()`が新しい会話系列を開始している

現状の`reject()`は`conversation_id="work-..."`を直接指定して新しい親なしメールを送っており、元の本文制作・監査系列の`parent_message_id`を継承していません。

また`AgentMail.send()`では、派生メールの送信元が親メールの受信先でなければならないため、単に現在の`latest_mail_id`を渡すだけでは、監査完了時の受信者が`manager`になっていない場合に正しい系列を作れません。

監査完了から承認・却下へ至るメールの送信順自体を整え、承認と却下のどちらでも同じ`conversation_id`、連続した`parent_message_id`、単調増加する`hop_count`を維持してください。

## 4. 必須設計：確定処理の公開単位と再開

### 4.1 版単位のディレクトリ公開

5種の確定成果物を個別に最終位置へ書き込まず、同一ファイルシステム上の一時ディレクトリへすべて作成してください。

推奨する最終配置例:

```text
store/works/{work_id}/canon/vNNN/
  CANON.vNNN.md
  CHARACTER_LEDGER.vNNN.md
  TIMELINE.vNNN.md
  RESOURCE_LEDGER.vNNN.md
  FORESHADOWING_LEDGER.vNNN.md
```

一時配置例:

```text
store/works/{work_id}/canon/.staging/{publication_id}/
```

要件:

- 5ファイルをすべて一時ディレクトリへ生成する
- すべてUTF-8で非空であることを確認する
- 必須ヘッダー、版、章、固定参照パスが揃っていることを確認する
- 同じボリューム内のディレクトリrenameまたは`os.replace`相当で、版ディレクトリ全体を一度に公開する
- 5ファイルを最終場所へ1件ずつ公開しない
- 既存の最終版ディレクトリやファイルを上書きしない
- `.staging`は正史として参照しない

既存の公開パス契約を維持する別の安全な最小実装がある場合は採用して構いません。ただし、5ファイルの一部だけが正式な公開場所へ見える期間を作らず、障害注入テストで安全性を証明してください。

### 4.2 版予約と公開状態

作品単位の版番号を並行実行でも重複予約しないよう、`BEGIN IMMEDIATE`等を使ってSQLite側で排他的に予約してください。

必要に応じて、次の責務を持つ公開管理テーブルを追加してください。名称は既存設計に合わせて変更可能です。

```text
publication_id
session_id
work_id
version
status
staging_path
final_path
error
created_at
updated_at
```

状態例:

```text
preparing
prepared
published
completed
failed
```

要件:

- `(work_id, version)`を一意にする
- 1つの`session_id`に進行中または完了済みの公開処理を複数作らない
- 再実行時は新しい版を予約せず、既存の未完了`publication_id`と版番号を再利用する
- 完了済みセッションの二重確定は従来どおり拒否する
- 版予約後に失敗しても、次回再開時に同じ予約を検査・再利用できる
- 手動でDB行を削除しなければ復旧できない設計にしない

### 4.3 再開規則

`finalize()`を再度呼んだ場合、公開状態に応じて決定的に処理してください。

- `preparing`かつ一時ディレクトリが完全: 検証後に公開を続行
- `preparing`かつ一時ディレクトリが不完全: 安全に作り直すか、診断可能な失敗として停止
- `prepared`かつ最終ディレクトリなし: 原子的renameを実行
- 最終ディレクトリあり、DB文書未登録: ファイル一式と内容を検証してDB確定を続行
- DB文書登録済み、セッション未完了: 整合性を検証してセッション・作品状態更新を続行
- `completed`: 二重確定として拒否
- ファイルとDBが矛盾し、安全に自動判定できない: 上書きや削除をせず、具体的な不整合を示して停止

途中状態を単に削除して最初から別版でやり直さないでください。

### 4.4 DB確定

公開された5ファイルを検証した後、次を1つのSQLiteトランザクションで実行してください。

- 5件の`canon_documents`登録
- 公開管理状態の`completed`更新
- `canon_sessions.status='completed'`
- `works.current_agent`、`next_agent='scene-planner'`、`status`の更新

トランザクション失敗時に、DBの一部だけがcommitされないことをテストしてください。

メール送信はSQLite状態DBと同一トランザクションではないため、DB確定の成否とメール送信の再試行を区別してください。メール失敗を理由に確定済み文書を別版として再公開してはいけません。

必要なら、確定通知メールの送信済みIDまたは送信状態をセッションへ保存し、同じ通知を重複送信しない冪等性を追加してください。

## 5. 必須設計：メール系列

### 5.1 監査完了時にmanagerへ渡す

監査完了後、利用者承認待ちへ入る前に、メール系列を次のようにしてください。

```text
canon-updater -> canon-auditor
canon-auditor -> manager
```

`canon-auditor -> manager`のメールには最低限次を含めてください。

- `session_id`
- 対象章
- 監査結果パス
- 状態が承認待ちであること

このメールを`latest_mail_id`として保存してください。これにより、次の送信元である`manager`が親メールの受信者となります。

### 5.2 承認

承認時は新しい系列を開始せず、最新の`canon-auditor -> manager`メールを親として、次の派生メールを送信してください。

```text
manager -> canon-updater
```

本文には承認済みであることと`session_id`を含めてください。

`conversation_id`は親から継承し、直接新しい値を指定しないでください。`parent_message_id`と`hop_count`は`AgentMail.send()`の既存検証へ従ってください。

### 5.3 却下

却下時も同じ最新メールを親として、次を送信してください。

```text
manager -> canon-updater
```

本文には最低限次を含めてください。

- `session_id`
- 却下理由
- 修正指示ファイルのパス（指定された場合）
- 直前の草案・監査を削除せず、新しいrevisionとして修正すること

新しい`conversation_id`を作らず、元の本文制作から続く系列を維持してください。

却下後の再生成・再監査は次の順で同じ系列を継続してください。

```text
manager -> canon-updater
canon-updater -> canon-auditor
canon-auditor -> manager
```

### 5.4 確定後の引き継ぎ

承認メールの受信者は`canon-updater`です。確定完了後は、そのメールを親として次を送信してください。

```text
canon-updater -> scene-planner
```

本文には次を含めてください。

- `work_id`
- 章番号
- 確定プロットパス
- 確定バイブルパス
- 5種の確定成果物パス
- 次工程が`scene-planner`であること

現行の`manager -> canon-updater`確定通知を別に送る必要がある場合は、親子制約を壊さず、重複通知を作らない系列を設計してください。不要なら削除し、`canon-updater -> scene-planner`を正式な確定・引き継ぎ通知として一本化してください。

## 6. 必須テスト

実Gemini、外部ネットワーク、有料APIを使用せず、dummy adapterと一時DB・一時ディレクトリでテストしてください。

最低限、次を追加または更新してください。

### 6.1 確定処理

1. 正常確定で同一版ディレクトリに5ファイルが揃う
2. 各ファイル名、版ヘッダー、固定参照情報が正しい
3. `canon_documents`が5件同一版で登録される
4. `canon_sessions`と`works`が同じDBトランザクションで完了状態になる
5. 完了後の再`finalize()`は二重確定として拒否される
6. 同一作品の並行版予約で版番号が重複しない
7. 一時ファイル生成の途中失敗では最終版ディレクトリが存在しない
8. 5ファイル検証失敗では公開されない
9. rename直前の失敗から同じ版・同じ公開処理で再開できる
10. rename直後・DB登録前の失敗から、新しい版を作らずDB確定を再開できる
11. DB登録中の例外で`canon_documents`が一部だけcommitされない
12. DB確定済み・メール未送信の状態から、文書を再公開せずメールだけ再試行できる
13. `.staging`の残骸が正史や次章参照として選ばれない
14. 既存の版ディレクトリ・確定ファイルを上書きしない
15. 自動判定不能な不整合では破壊的修復をせず診断可能なエラーになる

障害注入は、ファイル書込み、検証、rename、DB INSERT、状態更新、メール送信の境界へ決定的に入れられる形にしてください。実装の内部詳細へ過度に依存した脆いテストではなく、外部状態を確認してください。

### 6.2 メール系列

1. 監査完了メールが`canon-auditor -> manager`になる
2. 承認メールがその子として`manager -> canon-updater`になる
3. 却下メールもその子として`manager -> canon-updater`になる
4. 全メールの`conversation_id`が元系列と一致する
5. `parent_message_id`が直前メールを指す
6. `hop_count`が1ずつ増える
7. 却下後の再監査完了まで同じ系列が続く
8. 修正指示パスが却下メール本文に含まれる
9. 確定後の`canon-updater -> scene-planner`が承認メール系列を継承する
10. 確定メール再試行で重複メールを作らない
11. 不正な送信元・親関係を既存`AgentMail.send()`が拒否する挙動を壊さない

### 6.3 既存回帰

次を維持してください。

- 未承認状態では確定できない
- 未承認草案を正史・次章へ渡さない
- 却下・修正履歴は追記型で残る
- 旧revisionを削除しない
- resumeで完了済み生成・監査を不必要に再実行しない
- 第1章の空台帳規則
- 第2章以降の直前確定版継承
- 既存のメールワーカー、devloop、Gemini関連テスト

## 7. 実装上の注意

- 既存の`atomic_write()`は単一ファイルの安全な置換であり、5ファイル全体の公開原子性を保証しません。単独使用だけで問題を解決したとしないでください。
- Windowsで動作する実装にしてください。
- rename元とrename先は同じボリュームに置いてください。
- ファイルハンドルを閉じてからディレクトリrenameしてください。
- SQLite接続の自動commit境界を確認し、複数の`with connection()`へ分けて一部確定しないでください。
- `BEGIN IMMEDIATE`後の例外では明示的にrollbackされることを確認してください。
- SQL文字列へ値を直接連結せずプレースホルダーを使用してください。
- パスは既存の`safe_path()`方針に従い、作品ストア外へ公開しないでください。
- 失敗時に既存の確定成果物、別セッション、別作品の成果物を削除しないでください。
- メール本文へ長文草案そのものを複製せず、パスと状態を渡してください。
- APIキー、認証情報、環境変数をログや結果報告へ記録しないでください。

## 8. 非対象

今回は次を行わないでください。

- 小説本文の生成・改稿
- 新しい企画、プロット、章の作成
- 読者プロファイル機能の追加
- 正史・台帳の種類追加
- devloopの追加改造
- AgentMail全体の再設計
- CLI全体の大規模整理
- 無関係なリファクタリング
- 実Gemini呼出し
- Web検索
- 有料API利用
- force push
- 履歴書換え

## 9. 検証

対象テストを先に実行し、その後に全回帰を実行してください。

最低限:

```powershell
py -3 -m unittest tests.test_canon -v
py -3 -m unittest mail.test_agent_mail -v
py -3 -m unittest discover -v
py -3 -m compileall -q kobo mail tests
git diff --check
git status --short --branch
```

さらに、テスト用の一時ストアで次を確認してください。

- 確定版ディレクトリが一度に公開される
- 5ファイルの版番号が一致する
- DBとファイルの対応が一致する
- rename後・DB確定前からの再開が同じ版になる
- メール系列のID、親ID、hop数が連続する
- テスト終了後に一時ディレクトリ、ロック、未処理公開状態が不要に残らない

結果にはテスト総数、成功、失敗、スキップ、compileall終了コード、`git diff --check`結果を記録してください。

## 10. 完了判定

次をすべて満たした場合だけ完了です。

- `finalize()`が版単位で安全に公開できる
- 途中終了後に同じ版で再開できる
- DBに部分確定を残さない
- 二重確定を拒否する
- メール系列が承認・却下・再監査・次章引き継ぎまで連続する
- 必須障害注入テストが成功する
- 全通常テストが成功する
- `compileall`と`git diff --check`が成功する
- instruction-12の他の受入条件を壊していない

テストが通っただけで、上記を確認せず完了扱いしないでください。

## 11. 結果報告

`instructions/result-20260728-14.md`へ最低限次を記録してください。

1. 開始時HEADとブランチ
2. 変更前の2つの問題
3. 採用した公開プロトコル
4. 版予約方法
5. 一時ディレクトリと最終ディレクトリの構成
6. 各障害点からの再開規則
7. DBトランザクション境界
8. メール系列の変更前後
9. 変更ファイル
10. 追加・更新したテスト
11. 障害注入テスト結果
12. 全テスト結果
13. compileallと`git diff --check`結果
14. 未実施事項
15. 未解決事項
16. コミットSHA
17. push結果
18. 最終`git status --short --branch`

また、instruction-12の全受入条件を再照合し、今回の修正によって正史・台帳工程が完成したと確認できた場合だけ、`instructions/result-20260727-12.md`を正式な完了報告として新規作成してください。

正式な`result-20260727-12.md`には、過去のblocked・stalled記録への参照と、「instruction-14で残件を解消して完成した」ことを明記してください。未解決事項が残る場合は作成しないでください。

## 12. コミットとpush

`AGENTS.md`の共通規則に従い、検証可能な小さな単位でコミットしてください。

推奨コミット例:

```text
fix: make canon finalization crash recoverable
fix: preserve canon approval mail threads
docs: complete canon workflow reports
```

変更を明示的に確認し、無関係なファイルを含めないでください。

全検証成功後、push前に`git fetch origin`でリモート先端を確認してください。履歴分岐や競合がなければ現在の`main`をpushしてください。force pushは禁止です。

利用枠が25%以下になった場合は`AGENTS.md`に従い、新しい工程へ着手せず、現在の整合した変更をテスト・コミット・pushし、未完了事項を結果報告へ記録して停止してください。
