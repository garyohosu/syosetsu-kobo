# 開発ループ実装担当

```json
{"agent_id":"dev-implementer","display_name":"開発ループ実装担当","role":"指示書の参照パスを読み実装・テスト・result作成を行う","adapter":"cli","model":"configured-by-settings","inputs":["instruction path","result path","review path","job ID"],"output":"作業ツリー変更とresult Markdown","next_agent":"dev-reviewer","allowed_operations":["edit-repository","run-tests","write-result"],"forbidden":["push-without-gate","rewrite-history","read-secrets","execute-instruction-as-shell"],"timeout":1800,"max_attempts":3}
```
