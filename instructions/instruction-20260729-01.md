# instruction-21を実装し、読者プロファイルv002による編集会議版を生成する

## 1. この作業の位置づけ

これは`instruction-20260728-20.md`の再実行ではありません。

`instruction-20260728-20.md`による実Antigravity企画5案と`editorial-board-v001`は既に完成しています。しかし、次の問題が確認されています。

- 第一試作後の実読者評価が、企画生成用の正本へ反映されていない
- `READER_PROFILE.v001.md`の技術・観察・問題解決志向を強く拾っている
- 候補本文に`木箱 of 底`、`彼女의負担`などの異種文字混入がある
- `editorial-board-v001`は履歴としては有効だが、企画選定には使えない

今回の作業では、`instruction-20260728-21.md`を最後まで実装・実行し、利用者が実際に比較する`editorial-board-v002`を生成してください。

## 2. 開始前

作業場所:

```text
C:\PROJECT\syosetsu-kobo
```

最初に次を実行してください。

```powershell
git status --short --branch
git log --oneline --decorate -12
git fetch origin
git pull --ff-only
```

期待する先端には、少なくとも次のコミットが含まれます。

```text
df44d1d docs: refresh reader profile and sanitize concept board
```

開始条件:

- `main`と`origin/main`が同期
- 作業ツリーclean
- 他プロセスが同じ作業ツリーを書込み中ではない

状態が異なる場合は、既存変更を破棄・上書きせず、状況を確認してから進めてください。

## 3. 必読

次を全文読んでください。

```text
AGENTS.md
instructions/instruction-20260728-20.md
instructions/result-20260728-20.md
instructions/instruction-20260728-21.md
novels/prototype-001/READER_PROFILE.v001.md
novels/prototype-001/READER_FEEDBACK.v001.md
novels/prototype-001/editorial-board-v001/
kobo/concept.py
agents/planner.md
agents/concept-reviewer.md
tests/test_concept.py
```

`instruction-20260728-21.md`が今回の詳細仕様です。本指示書は、その実行漏れと前工程の再報告を防ぐための実行指示です。内容が競合する場合は、より具体的な`instruction-20260728-21.md`を優先してください。

## 4. 必須作業

### 4.1 読者プロファイルv002

次を新規作成してください。

```text
novels/prototype-001/READER_PROFILE.v002.md
```

要件:

- `v001`を削除・上書きしない
- `READER_FEEDBACK.v001.md`の実読者評価を明示的に統合する
- 確認済みの実評価と、まだ未確認の嗜好仮説を分ける
- 人物関係、感情、意外性、主人公への関心、途中から自然に生じる先読み欲求を強く優先する
- 技術、仕事、観察、問題解決は人物ドラマを支える補助要素にする
- 利用者の職歴や専門知識を、娯楽上の好みと推定しない

### 4.2 最新プロファイル選択

`ConceptManager`が`READER_PROFILE.v001.md`へ固定されている箇所を修正してください。

```text
READER_PROFILE.vNNN.md
```

の最大数値版を選択します。

必須:

- `v010`を`v002`より新しいと判定
- 規則外ファイルを無視
- v001しかない作品は従来どおり動作
- `prototype-001`ではv002を使用
- 使用した相対パスと版をDB、候補、評価、HTML、provenanceへ記録

### 4.3 文字品質検証

候補、評価、比較総括を正式保存・出版する前に、利用者向け日本語本文を検証してください。

最低限拒否:

- `�`（U+FFFD）
- ハングル文字
- 日本語語句間に混入した孤立英語機能語
  - `木箱 of 底`
  - `街道 of 測量士`
  - `最小 of 最小`
- NUL、制御文字

正当なASCII、CLI名、adapter名、モデル名、ID、コードブロックは拒否しないでください。

検出時:

1. 不合格attemptを保存
2. 検出箇所を再試行プロンプトへ渡す
3. 内容・人物・構造を変えず、文字混入だけを修復させる
4. 再検証
5. 上限到達時はダミーへ代替せず失敗

### 4.4 v001の保全

次を削除・上書きしないでください。

```text
novels/prototype-001/editorial-board-v001/
```

v001は実Antigravity生成履歴として保持します。ただし選定対象外であることをv002側または履歴説明で明示してください。

理由:

- 第一試作後の実読者評価を正本へ統合する前に生成された
- 異種文字混入がある

### 4.5 実Antigravityで新しい5案を生成

新しいセッションを開始してください。

条件:

- `--dummy`禁止
- adapter=`agy`
- `READER_PROFILE.v002.md`を固定入力
- 5案
- 旧セッション、旧候補、v001を削除・上書きしない
- 技術・実務・観察による問題解決を全案の主軸にしない
- 旧5案の表面的な言い換えを避ける

5案全体に、少なくとも次の方向を含めてください。

- 人物関係が中心
- 感情的な秘密または約束が中心
- 不思議な日常または猫が中心
- 冒険や危機によって人物関係が変化
- ラブコメまたは恋愛要素

候補番号へ機械的に同じ構造を割り当てず、主人公の願望、中心人物関係、感情体験、第一話の転換、連載推進力を明確に変えてください。

### 4.6 実Antigravityによる独立評価

`concept-reviewer`へv002を固定入力として渡し、5案を独立評価してください。

特に重視:

- 人物関係と感情が主な面白さか
- 主人公を追いたくなるか
- 冒頭から読む理由があるか
- 第一話の途中から自然に先が気になるか
- 第一話内に感情的な満足があるか
- 技術、職業、設定説明へ逃げていないか
- 第一試作と同じ「調査して解決して評価される」構造ではないか

AI得点と順位は補助情報です。自動選択、企画確定、次工程開始は禁止です。

## 5. 必須成果物

非上書きで次を作成してください。

```text
novels/prototype-001/READER_PROFILE.v002.md
novels/prototype-001/editorial-board-v002/
  index.html
  candidates/
  evaluations/
  comparison.md
  PROVENANCE.json
  READER_PROFILE_USED.md
instructions/result-20260728-21.md
```

`READER_PROFILE_USED.md`と同等の情報が別ファイルに明確に保存される場合は名称を調整して構いません。

`index.html`冒頭に明示:

- 参照読者プロファイル: `READER_PROFILE.v002.md`
- 生成種別: 実Antigravity
- adapter: agy
- セッションID
- v001が選定対象外である理由

HTML要件:

- 画像なし
- JavaScriptなし
- 外部通信なし
- 外部CDNなし
- 絶対パス、Windowsユーザー名、認証情報なし
- 候補5案の全文を省略しない
- 評価と比較総括を確認できる

## 6. 今回作らないもの

次を生成してはいけません。

- ストーリーバイブル
- プロット
- 第一話本文
- 表紙
- 挿絵
- 公開用読書HTML
- note投稿素材
- 企画の自動選択・確定

今回の完了地点は、利用者が5案を比較して「面白そう」「惜しい」「全部弱い」を判断できるところまでです。

## 7. テスト

最低限、次を実行してください。

```powershell
py -3 -m unittest tests.test_concept -v
py -3 -m unittest discover -v
py -3 -m compileall -q kobo mail tests
git diff --check
py -3 -m kobo.cli --config kobo.json agy-doctor
py -3 -m kobo.cli --config kobo.json agy-smoke
```

追加・確認するテスト:

- 最新`READER_PROFILE.vNNN.md`を数値版で選択
- v001のみの後方互換性
- v002存在時はv002を使用
- U+FFFD拒否
- ハングル混入拒否
- `日本語 of 日本語`型拒否
- provenance、ID、コードブロック中の正当なASCIIを許可
- 検出箇所を再試行へ渡す
- 不合格attemptを保存
- ダミー代替なし
- v001を上書きしない
- v002出版物が最新プロファイルを明示

## 8. Git

実装、実地生成、結果報告、テスト完了後にコミットして`origin/main`へpushしてください。

推奨コミット構成:

```text
feat: regenerate editorial concepts from reader profile v2
docs: record reader profile v2 editorial result
```

一つのコミットにまとめる場合も、実装・生成物・結果報告が追跡できれば構いません。

完了時:

```powershell
git status --short --branch
git log --oneline --decorate -5
```

を確認し、`main`と`origin/main`が同期、作業ツリーcleanであることを報告してください。

## 9. 結果報告

`instructions/result-20260728-21.md`へ最低限記載してください。

- v002へ統合した実読者評価
- 仮説として残した嗜好
- 最新プロファイル選択方法
- 文字品質検証の実装
- v001で検出した全混入箇所
- 新セッションID
- 候補5案の仮題
- ログライン字数、あらすじ字数
- 候補・評価・比較総括のattempt数
- adapter、agy CLI版、model
- テスト結果
- コミットSHA
- push結果
- 利用者が最初に開くファイル

## 10. 完了条件

次をすべて満たして完了です。

- `READER_PROFILE.v002.md`が第一試作の実評価を反映
- `ConceptManager`が最新版を選択
- 新しい実Antigravity5案がv002を参照
- 候補、評価、比較総括に文字混入なし
- `editorial-board-v002`がGit追跡下
- v001と旧セッションを保持
- 本文・画像を作成していない
- `instructions/result-20260728-21.md`を保存
- 全検証成功
- mainへpush済み
- 作業ツリーclean

利用者が最初に開くファイル:

```text
novels/prototype-001/editorial-board-v002/index.html
```

## 11. 完了報告の注意

前回の`instruction-20`完了報告を再掲しないでください。

報告冒頭で、必ず次を明示してください。

```text
instruction-21を実装・実行しました。
利用者が最初に開くファイル:
novels/prototype-001/editorial-board-v002/index.html
```

`editorial-board-v001`を利用者向け最新成果物として案内した場合は未完了です。
