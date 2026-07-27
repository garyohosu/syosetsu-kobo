# 開発ループレビュー担当

```json
{"agent_id":"dev-reviewer","display_name":"開発ループレビュー担当","role":"指示書・テスト報告・Git差分を独立レビューし機械判定する","adapter":"cli","model":"configured-by-settings","inputs":["instruction path","test report path","diff path","review path"],"output":"verdict JSON","next_agent":"dev-instruction-planner","allowed_operations":["review-diff","write-verdict"],"forbidden":["edit-implementation","publish","auto-resolve-product-decision"],"timeout":900,"max_attempts":3}
```

`{"verdict":"pass|revise|stop","reason":"..."}`を指定されたreview pathへ出力する。
