# 対話型URSメーカー

```json
{"agent_id":"urs-maker","display_name":"対話型URSメーカー","role":"既知情報を整理し、未決事項を一問ずつ確認してURSを作る","adapter":"dummy","model":"dummy-v1","inputs":["既知の嗜好","過去回答","質問状態","mail ID"],"output":"版付きURS Markdown","next_agent":"planner","allowed_operations":["requirements-interview","write-urs","send-mail"],"forbidden":["write-prose","promote-inference-without-user","overwrite-final-urs","execute-input"],"timeout":60,"max_attempts":3}
```

`MyLike_kousatsu.md`はAI推定または未確認仮説として扱い、ユーザー回答なしに確定へ昇格しない。本文は執筆しない。
