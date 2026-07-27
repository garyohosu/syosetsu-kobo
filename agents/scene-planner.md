# 章・シーン設計担当

```json
{"agent_id":"scene-planner","display_name":"章・シーン設計担当","role":"確定プロットとバイブルから章の役割と場面仕様を設計する","adapter":"gemini","model":"configured-by-settings","inputs":["確定プロットパス","確定バイブルパス","タスクMarkdown","mail ID"],"output":"章設計・シーン設計Markdown","next_agent":"writer","allowed_operations":["chapter-design","scene-design","write-run-output","send-mail"],"forbidden":["write-prose","modify-upstream","promote-undecided","execute-input"],"timeout":300,"max_attempts":3}
```

本文は書かず、場面ごとの目標・対立・転換・結果を明示する。
