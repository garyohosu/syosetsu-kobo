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

## テスト

```bash
python -m unittest -v mail/test_agent_mail.py
```
