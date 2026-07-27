# AIエージェント間通信用メーリングシステム

`agent_mail.py`は、複数のAIエージェントがSQLiteを介して非同期に連絡するための
小さなメールボックスです。`mail`フォルダを丸ごとコピーすれば利用できます。
外部パッケージは不要で、Python標準ライブラリだけで動作します。

## コピーして使う

1. この`mail`フォルダを、AIエージェント同士で共有するプロジェクトへコピーする。
2. 各AIエージェントに重複しない`agent_id`を決める。
3. DBを初期化し、利用するAIエージェントを登録する。
4. 各AIエージェントの起動指示へ`AGENT_STARTUP.md`の内容を追加する。

特に指定しなければ、DBはこのフォルダ内の`agent_mail.db`に作られます。
プロジェクトのどのディレクトリからCLIを実行しても同じDBを参照します。

## 初期設定

```bash
python mail/agent_mail.py init
python mail/agent_mail.py register writer --name "執筆AI"
python mail/agent_mail.py register reviewer --name "監査AI"
```

エージェントIDは、送信元と受信先に使う一意な文字列です。
DBを別の場所に置く場合だけ、各コマンドへ`--db path/to/mail.db`を追加してください。

## AIエージェント起動時の処理

最初に未読数を確認します。

```bash
python mail/agent_mail.py unread --agent writer
```

出力例:

```json
{
  "received": 1,
  "replies": 2,
  "total": 3
}
```

次に未処理項目を取得します。受信メールは作成時刻、返信は返信時刻を基準として、
すべて時系列順で返ります。

```bash
python mail/agent_mail.py check --agent writer
```

各要素の`kind`に従って、先頭から順に処理します。

1. `kind`が`received`なら`body`を読み、回答を作る。
2. 回答を`reply`コマンドで登録する。返信と受信者既読フラグは同時に更新される。
3. `kind`が`reply`なら`reply`を読んで必要な処理を行う。
4. 処理完了後に`mark-reply-read`で送信者既読フラグを立てる。
5. 必要なら`send`で別の新規メールを送る。

受信メールへの返信:

```bash
python mail/agent_mail.py reply \
  --agent reviewer --message 1 --body "第1話に時系列の矛盾があります"
```

返信の処理完了:

```bash
python mail/agent_mail.py mark-reply-read \
  --agent writer --message 1
```

新規メールの送信:

```bash
python mail/agent_mail.py send \
  --from writer --to reviewer --body "第1話を監査してください"
```

すべてのコマンドはJSONを標準出力へ返します。失敗時は`error`を含むJSONを返し、
終了コードは1になります。

## Pythonから使う

```python
from mail.agent_mail import AgentMail

mail = AgentMail("mail/agent_mail.db")
counts = mail.unread_count("writer")

for item in mail.iter_work("writer"):
    if item.kind == "received":
        answer = create_answer(item.body)
        mail.reply("writer", item.message_id, answer)
    else:
        process_reply(item.reply)
        mail.mark_reply_read("writer", item.message_id)
```

`create_answer`と`process_reply`は、それぞれのAIエージェント側で実装します。
このシステムは連絡の保存・順序付け・既読管理のみを担当します。

## 待機ポーリング型ワーカー

メール処理をエージェントの常駐AIプロセスから分離する場合は、Pythonワーカーを待機させ、
メールを1件取得したときだけ登録済みPythonハンドラーまたはCLI子プロセスを起動します。
これはOSイベント通知ではなく、`poll_interval`でSQLiteを確認する待機ポーリング方式です。

処理状態は既読・返信済みとは別に管理されます。

- `pending`: 処理待ち
- `processing`: リースを持つワーカーが処理中
- `completed`: ハンドラー処理が正常終了
- `failed`: 最大試行回数到達または処理不能

`completed`になっても自動的に既読・返信済みにはなりません。

```python
from mail.agent_mail import AgentMail, HandlerRegistry, Worker, WorkerConfig

mail = AgentMail("mail/agent_mail.db")
registry = HandlerRegistry()
registry.register("default", lambda item, context: print(item.body))
worker = Worker(mail, "reviewer", registry, WorkerConfig(
    max_attempts=3, timeout=60, poll_interval=1, stale_after=900,
    max_hops=10, escalation_agent_id="manager",
))
worker.run()                 # Ctrl+Cの前に worker.stop() で安全停止
```

ハンドラーは`(item, context)`を受け取り、`context.send(recipient_id, body)`で派生メールを送信します。
系列ID、親メールID、ホップ数は親から自動導出され、矛盾する直接指定は拒否されます。
ハンドラーは`item.message_id`または系列IDを冪等性キーとして使えるようにし、外部副作用のexactly-onceは保証されません。
ホップ数が上限を超えると処理を停止し、設定されたエージェントへエスカレーションします。

`max_attempts`は初回を含む合計試行回数で、既定値は3です。旧名`max_retries`も互換エイリアスとして使えますが、
両方を異なる値で指定するとエラーになります。放置処理の回復ではリース・フェンシングにより古いワーカーのDB更新を拒否します。

CLIでは本文をシェルとして実行せず、指定したコマンドへ標準入力として渡します。
コマンドは引数配列として起動され、`shell=True`は使いません。

```bash
python mail/agent_mail.py worker-once --agent reviewer --timeout 60 --max-attempts 3 --max-hops 10 --command python tools/handle_mail.py
python mail/agent_mail.py worker-loop --agent reviewer --interval 2 --stale-after 900 --max-attempts 3 --command python tools/handle_mail.py
python mail/agent_mail.py recover --stale-after 900 --agent reviewer
python mail/agent_mail.py worker-status --agent reviewer
```

CLIワーカーは対象エージェントが未登録なら安全な既定値で自動登録します。既存の表示名などは上書きせず、矛盾指定はエラーです。
CLI子プロセスはタイムアウト時に終了・回収します。Pythonハンドラーは信頼済み内部用途で、スレッドや外部副作用の強制停止は保証しません。
本文は`AgentMail(max_body_length=...)`またはCLIの`--max-body-length`で設定可能な上限を持ち、超過時は保存せずエラーにします。
新規DBは対応OSで所有者のみ読み書きできる権限を設定し、既存DBの権限は広げません。
既存DBは`PRAGMA user_version`（現在3）を使って列と索引を冪等に追加します。既存の既読・返信APIは維持されます。

## テスト

```bash
python -m unittest -v mail/test_agent_mail.py
```
