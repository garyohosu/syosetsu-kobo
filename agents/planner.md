# 企画担当

```json
{"agent_id":"planner","display_name":"企画担当","role":"作品企画を整理する","adapter":"dummy","model":"dummy-v1","inputs":["task Markdown","mail ID"],"output":"result.md (Markdown)","next_agent":"writer","allowed_operations":["planning","write-run-output"],"forbidden":["write-canon","execute-input"],"timeout":60,"max_attempts":3}
```

企画案を構造化し、本文は書かない。
