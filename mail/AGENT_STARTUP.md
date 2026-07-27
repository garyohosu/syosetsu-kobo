# AIエージェント起動時のメール確認指示

このプロジェクトでは、AIエージェント間の連絡に`mail/agent_mail.py`を使う。
自分に割り当てられた`agent_id`を、以下の`<自分のID>`へ入れて実行すること。

作業開始時には、ほかの作業より先に次を実行する。

```bash
python mail/agent_mail.py unread --agent <自分のID>
python mail/agent_mail.py check --agent <自分のID>
```

`check`の結果を`event_at`が古い順に処理する。

1. `kind`が`received`の場合
   - `body`を読む。
   - 依頼された作業または判断を行う。
   - 回答を次のコマンドで登録する。

```bash
python mail/agent_mail.py reply \
  --agent <自分のID> \
  --message <message_id> \
  --body "<回答>"
```

2. `kind`が`reply`の場合
   - `reply`を読む。
   - 返信内容に基づく作業を完了する。
   - 完了後、次のコマンドで送信者既読フラグを立てる。

```bash
python mail/agent_mail.py mark-reply-read \
  --agent <自分のID> \
  --message <message_id>
```

3. 別のAIエージェントへ新しい依頼や連絡が必要な場合

```bash
python mail/agent_mail.py send \
  --from <自分のID> \
  --to <相手のID> \
  --body "<本文>"
```

注意事項：

- `check`で取得した項目は、必ず古いものから順に処理する。
- 受信メールは、回答の登録が終わるまで既読にしない。
- 返信は、その内容に基づく処理が終わってから既読にする。
- 返信に対してさらに回答が必要なら、元メールを再利用せず新しいメールを送る。
- エラーになった処理を、完了したものとして扱わない。

## 待機ポーリング型ワーカーとして起動する場合

AIプロセスを待機中に常駐させず、Pythonワーカーがポーリングで処理可能なメールを確認し、取得時だけハンドラーを起動する。
ワーカーは一度に1件だけリース付きで排他的に取得し、成功時は`completed`、失敗時は合計試行回数に従って再試行または`failed`へ遷移させる。
`completed`、既読、返信済みは別概念である。放置処理の回復後は古いリースのDB更新を拒否するが、外部副作用はハンドラーの冪等性で防ぐ。

```bash
python mail/agent_mail.py worker-loop \
  --agent <自分のID> --interval 1 --max-attempts 3 --timeout 300 \
  --stale-after 900 --max-hops 10 --command <許可済みCLI> <固定引数>
```

本文はCLIの標準入力へ渡されるだけで、シェルコマンドとして解釈されない。CLI子プロセスのタイムアウトは終了・回収を保証するが、
Pythonハンドラーのスレッドや外部副作用の強制停止は保証しない。派生メールの系列ID、親ID、ホップ数は親から自動導出される。
未登録の対象エージェントは安全な既定値で自動登録され、既存属性は上書きされない。
停止時はCtrl+Cを使うか、Python APIの`Worker.stop()`を呼び出す。
