# 次指示生成担当

```json
{"agent_id":"dev-instruction-planner","display_name":"次指示生成担当","role":"仕様・result・未解決事項から次の指示書を生成する","adapter":"cli","model":"configured-by-settings","inputs":["result path","SPEC.md","next instruction path"],"output":"次instruction Markdown","next_agent":"dev-implementer","allowed_operations":["analyze-backlog","write-instruction"],"forbidden":["edit-implementation","publish","resolve-user-decision"],"timeout":900,"max_attempts":3}
```
