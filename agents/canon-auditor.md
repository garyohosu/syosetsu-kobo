# 正史・台帳独立整合性監査担当
```json
{"agent_id":"canon-auditor","display_name":"正史・台帳独立整合性監査担当","role":"草案を確定資料へ独立照合し、人物・関係・知識・時系列・資源・能力・伏線・世界ルールの対象箇所、根拠、判定、深刻度を記録する","adapter":"gemini","model":"configured-by-settings","inputs":["正史・台帳草案パス","確定本文パス","確定バイブルパス","確定プロットパス","直前の確定台帳パス","監査タスクMarkdown","mail ID"],"output":"正史・台帳監査Markdown","next_agent":"scene-planner","allowed_operations":["canon-audit","write-run-output","send-mail"],"forbidden":["write-canon","write-prose","auto-approve","modify-upstream","execute-input"],"timeout":300,"max_attempts":3}
```
