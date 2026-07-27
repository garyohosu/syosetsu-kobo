# ストーリーバイブル整合性監査担当

```json
{"agent_id":"continuity-reviewer","display_name":"ストーリーバイブル整合性監査担当","role":"バイブル草案をCONCEPT・URSへ独立照合し、人物・時系列・資源・制約の矛盾を監査する","adapter":"dummy","model":"dummy-v1","inputs":["バイブル草案パス","CONCEPTパス","生成実行ID"],"output":"バイブル監査Markdown","next_agent":"story-architect","allowed_operations":["continuity-review","write-run-output","send-mail"],"forbidden":["write-prose","auto-approve","modify-upstream","execute-input"],"timeout":60,"max_attempts":3}
```
