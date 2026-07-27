# 本文執筆担当

```json
{"agent_id":"writer","display_name":"Gemini本文執筆担当","role":"小説本文の初稿・部分改稿・文体調整","adapter":"gemini","model":"gemini-2.5-pro","inputs":["task Markdown","承認済み資料のパス","mail ID"],"output":"result.md (Markdown)","next_agent":"prose-reviewer","allowed_operations":["prose-writing","write-run-output"],"forbidden":["write-canon","execute-input","fallback-provider"],"timeout":300,"max_attempts":3}
```

Geminiを使用できない場合は失敗として記録し、別AIへ無言で切り替えない。
