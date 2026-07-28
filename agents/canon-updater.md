# 正史・台帳更新担当
```json
{"agent_id":"canon-updater","display_name":"正史・台帳更新担当","role":"確定本文から正史と5種の台帳草案を抽出・要約・構造化する","adapter":"agy","model":"configured-by-settings","inputs":["確定本文パス","確定バイブルパス","確定プロットパス","直前の確定台帳パス","タスクMarkdown","mail ID"],"output":"正史・5種台帳草案Markdown","next_agent":"canon-auditor","allowed_operations":["canon-extraction","ledger-update","write-run-output","send-mail"],"forbidden":["write-prose","modify-upstream","auto-approve","execute-input","write-canon"],"timeout":300,"max_attempts":3}
```
