# あらすじを先に比較し、面白そうな企画だけを制作する編集会議工程へ改修する

## 1. 目的

第一試作では、企画の面白さを十分に確認しないまま、約8,000字の本文、表紙、本文挿絵8枚、HTML版まで制作しました。その結果、読みやすさは改善したものの、利用者から次の評価を受けました。

- 引きを感じなかった
- 内容へ引き込まれなかった
- 主人公の性別が分かるまでかなり時間がかかった
- 内容が細かすぎた
- 設備・原因調査・修理の細部は、読んで面白い題材ではなかった

今後は、漫画家と編集者がネームや企画ラフで打ち合わせるように、**最初に複数の短いあらすじを利用者へ提示し、利用者が「面白そう」と明示した企画だけを本文・挿絵制作へ進める**工程に変更してください。

一章全文や画像を先に作ってはいけません。

## 2. 基本方針

既存の`ConceptManager`には、候補生成、比較、選択、保留、全却下、再生成、修正、確定が実装済みです。この仕組みを活かし、別の重複システムを新設しないでください。

現在の企画候補は項目数が多く、設計書のように重いため、利用者が短時間で「読みたいか」を判断できる編集会議用フォーマットへ変更します。

工程は次の順番に固定します。

```text
読者プロファイル
  ↓
短い企画ラフを5案生成
  ↓
利用者が読む
  ├─ 面白そう → 1案を選択・確定
  ├─ 惜しい   → 指定案だけ修正
  ├─ 保留     → 制作しない
  └─ 全部弱い → 全却下して別方向で再生成
  ↓
選択済み企画だけストーリー設計へ進む
  ↓
第一話本文
  ↓
利用者評価
  ↓
合格した場合だけ挿絵とHTML
```

挿絵付きHTMLは本文評価後の工程です。面白さが未確認の本文へ画像を作ってはいけません。

## 3. 開始前

最初に`AGENTS.md`を全文読み、状態を確認してください。

```powershell
git status --short --branch
git log --oneline --decorate -10
git fetch origin
git pull --ff-only
```

必読:

- `AGENTS.md`
- `MyLike.md`
- `MyLike_kousatsu.md`
- `novels/prototype-001/READER_PROFILE.v001.md`
- `novels/prototype-001/READER_FEEDBACK.v001.md`
- `kobo/concept.py`
- `kobo/story_design.py`
- `kobo/manuscript.py`
- `kobo/visual_publish.py`
- `kobo/cli.py`
- `agents/planner.md`
- `agents/concept-reviewer.md`
- README、SPEC、QandAの現行仕様

## 4. 編集会議用の企画ラフ

`concept-start`の既定候補数を5案としてください。明示指定された場合は既存どおり1〜5案を許可して構いません。

各候補は、長い企画設計書ではなく、次の必須項目だけで構成してください。

```markdown
# 企画候補 C01: 仮題

## 一文で言うと
80文字以内。

## 主人公
性別、年齢層、立場、現在の望みを簡潔に書く。

## 物語の始まり
最初の場面で何が起き、主人公が何を選ばされるか。

## 中心となる人物関係
誰と誰の関係を読み続ける物語なのか。

## 第一話のあらすじ
400〜700字。導入、転換、感情の山、第一話の終わりまでを記す。
技術手順や世界設定の説明を並べない。

## この先を読みたくなる疑問
読者が知りたくなることを1〜2個。

## 連載した場合の楽しみ
この設定で何話も読み続けられる理由を150字以内で書く。

## 主なリスク
既視感、説明過多、中盤停滞、主人公の受動性などを簡潔に書く。
```

一候補の全体は、見出しを除いて**900〜1,300字程度**にしてください。長大な企画書にしないでください。

## 5. 第一試作の失敗を反映する

企画生成プロンプトと評価軸へ、次を明示的に追加してください。

### 必須

- 主人公の性別、年齢層、立場が企画ラフですぐ分かる
- 冒頭から人物関係または感情的な問題が動く
- 第一話の途中にも「次を知りたい」と思わせる疑問がある
- 主人公が第一場面で選択する
- 説明より、会話、行動、感情、意外性が中心
- 利用者が詳しい職業分野を、そのまま娯楽上の好みと決めつけない

### 禁止

- 技術的な故障原因や作業手順を企画の中心にする
- 主人公が正解を説明し、周囲が感心するだけの構造
- 最後に謎を一つ置いただけで「引きがある」と判定する
- 世界設定の説明から始める
- AI評価が高いという理由だけで制作へ進む

## 6. 企画比較ページ

利用者がMarkdownファイルを一つずつ開かなくても比較できるよう、候補5案を一画面で読めるHTMLを生成してください。

推奨保存先:

```text
.kobo/works/{work_id}/concepts/{session_id}/editorial-board/index.html
```

確定・公開用ではなく、ローカルの編集会議用プレビューです。

要件:

- 候補ごとにカード表示
- 仮題、一文、主人公、第一話あらすじ、疑問、リスクを表示
- スマートフォンでも読みやすい
- 画像を生成しない
- 外部CDN、JavaScript、通信を不要とする
- 候補の全文を省略しない
- AI推奨順位は補助情報として末尾へ置き、先頭で選択を誘導しない

新しいCLI例:

```powershell
py -3 -m kobo.cli concept-board --work <work-id> --session <session-id>
```

コマンド名は既存規則に合わせて調整して構いません。

## 7. 人間承認ゲート

次の状態遷移を厳守してください。

```text
generating
  → evaluating
  → awaiting_selection
  → selected
  → final
  → story design
```

次をコードで拒否してください。

- 候補未生成での選択
- 利用者選択なしでの企画確定
- 企画確定前の`story-start`
- 企画確定前の`manuscript-start`
- 企画確定前の`visual-start`
- AI推奨順位だけを根拠にした自動選択

`hold`と`reject_all`では、作品制作へ進めてはいけません。

## 8. 制作コストを段階化する

企画を選択した後も、次の順番を守ってください。

### 段階A: 企画ラフ

- 5案
- 各900〜1,300字
- 画像なし

### 段階B: ストーリー設計

- 利用者が「面白そう」と選んだ1案だけ
- 人物、第一話、長期の方向性を設計
- まだ挿絵なし

### 段階C: 第一話本文

- 選択・確定済み企画だけ
- 最初は挿絵なしの本文を生成
- 利用者が内容を評価する

### 段階D: 挿絵付き出版

- 利用者が本文を「面白い」「続きを読みたい」と評価した場合だけ
- 表紙、本文挿絵、HTML、note用素材を生成

本文が不合格の場合、画像生成へ進んではいけません。

## 9. CLIと表示

既存CLIを維持してください。

```powershell
py -3 -m kobo.cli concept-start --work <work-id> --count 5
py -3 -m kobo.cli concept-list --work <work-id> --session <session-id>
py -3 -m kobo.cli concept-show C01 --work <work-id> --session <session-id>
py -3 -m kobo.cli concept-compare --work <work-id> --session <session-id>
py -3 -m kobo.cli concept-select C01 --work <work-id> --session <session-id>
py -3 -m kobo.cli concept-hold --work <work-id> --session <session-id>
py -3 -m kobo.cli concept-reject-all --work <work-id> --session <session-id>
py -3 -m kobo.cli concept-regenerate --work <work-id> --session <session-id>
py -3 -m kobo.cli concept-revise C01 --instructions <file> --work <work-id> --session <session-id>
py -3 -m kobo.cli concept-preview --work <work-id> --session <session-id>
py -3 -m kobo.cli concept-finalize --work <work-id> --session <session-id>
```

追加した編集会議HTMLのパスを、`concept-status`または`concept-board`で返してください。

## 10. テスト

最低限、次を追加・更新してください。

- 既定で5候補を生成する
- 候補が必須8見出しを持つ
- 各候補の文字数上限・下限を検証する
- 主人公の性別・年齢層・立場が空でない
- 第一話あらすじが400〜700字
- 編集会議HTMLに全候補が入る
- HTMLに画像参照がない
- HTMLが外部通信を要求しない
- 未選択の企画確定を拒否する
- 未確定企画でstory/manuscript/visual開始を拒否する
- hold/reject_allから制作へ進めない
- 人間の選択後だけ次工程へ進める
- UTF-8日本語を保持する

全テストを実行してください。

```powershell
py -3 -m unittest discover -v
py -3 -m compileall -q kobo mail tests
git diff --check
```

## 11. 実地試験

実装だけで停止せず、第一試作の読者評価を反映した新しい作品候補を5案生成してください。

保存先は現行の作品管理構造に従ってください。候補には最低限、互いに異なる次の方向性を含めます。

1. 日常ファンタジーと猫
2. 再会、記憶、すれ違い
3. 恋愛または強い人物関係
4. 境界世界または異世界の異常な一場面
5. 上記と重ならない意外な案

ただし、利用者が選択する前に企画を確定したり、本文・画像を生成したりしてはいけません。

完了時点は、利用者が編集会議HTMLを開き、5案を比較して「面白そう」「全部弱い」「この案を直して」と判断できる状態です。

## 12. 結果報告

作成先:

```text
instructions/result-20260728-18.md
```

記載項目:

- 変更した企画工程
- 候補フォーマット
- 人間承認ゲート
- 編集会議HTMLのパス
- 実地生成した5候補の仮題
- 本文・画像を生成していないこと
- テスト結果
- コミットSHA
- push結果
- 利用者が最初に開くファイル

## 13. 禁止事項

- 新しい長編本文を作る
- 新しい挿絵を作る
- 選ばれていない案のストーリーバイブルを作る
- AIが利用者の代わりに採用案を決める
- 第一作を細部だけ直して続行する
- 技術的専門性を面白さと同一視する
- 企画ラフを長大な仕様書にする
