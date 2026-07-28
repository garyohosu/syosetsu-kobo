# 本文へ一定間隔で挿絵を入れ、全文を読みやすいHTMLとして出版する

## 1. 目的

小説工房へ、確定済みの小説本文から次を自動生成する恒常機能を追加してください。

1. 本文を場面単位で解析する
2. 一定文字数以上、画像なしの文章が連続しないよう挿絵位置を決める
3. 登場人物と画風の一貫性を保った挿絵を生成する
4. 小説本文を一文字も要約・省略せず、挿絵を差し込んだ読みやすいHTMLを出力する
5. 利用者がブラウザでそのまま読める状態にする

これは今回の第一話だけを手作業でHTML化する作業ではありません。以後の各章にも再利用できる「挿絵付き読書版の出版工程」として実装してください。

前回作成された要約ダイジェストHTMLのように、本文を短くまとめてはいけません。**正本の全文を保持することが絶対条件です。**

## 2. 利用者の意図

利用者はQuoraやnoteの長文でも、文字だけが長く続くと読みにくいため、一定量の文章ごとに画像を挿入しています。

画像は単なる表紙や装飾ではありません。

- 文字の連続による疲労を切る
- 人物、場所、戦況、出来事を視覚的に理解させる
- 長文の現在位置を読者へ感じさせる
- 次の段落を読む気力を回復させる
- 重要な転換点の印象を残す

したがって、章の冒頭に一枚だけ置く実装では不十分です。本文中の複数箇所へ、意味のある挿絵を配置してください。

## 3. 開始前

最初に`AGENTS.md`を全文読み、現在の状態を確認してください。

```powershell
git status --short --branch
git log --oneline --decorate -10
git fetch origin
git pull --ff-only
```

開始条件:

- `main`と`origin/main`が同期
- 作業ツリーclean
- 他プロセスが同じ作業ツリーを書込み中ではない

必読:

- `AGENTS.md`
- `instructions/instruction-20260728-16.md`
- `instructions/result-20260728-16.md`
- `kobo/manuscript.py`
- `kobo/agy.py`
- `kobo/cli.py`
- `kobo/orchestrator.py`
- `kobo.json`
- `agents/*.md`
- `novels/prototype-001/CHAPTER-001.v001.md`
- `novels/prototype-001/SELECTED_CONCEPT.v001.md`
- 現行のREADME、SPEC、QandA

## 4. 新しい工程

新しいモジュールを、責務が明確な名前で追加してください。推奨:

```text
kobo/visual_publish.py
```

責務:

- 確定本文の取得
- 文字数と段落・場面境界の解析
- 挿絵計画の作成
- 画像生成の実行と検証
- HTMLの決定的レンダリング
- 中断再開
- 版管理
- 利用者承認後の確定

本文制作工程そのものへ画像生成コードを埋め込みすぎないでください。本文はMarkdown正本、HTMLは派生成果物です。

## 5. CLI

最低限、次のCLIを実装してください。名前は既存CLI規則に合わせて微調整して構いませんが、機能を減らしてはいけません。

```powershell
py -3 -m kobo.cli visual-start 1 --work <work-id>
py -3 -m kobo.cli visual-resume --work <work-id> --session <session-id>
py -3 -m kobo.cli visual-status --work <work-id> --session <session-id>
py -3 -m kobo.cli visual-show plan --work <work-id> --session <session-id>
py -3 -m kobo.cli visual-show html --work <work-id> --session <session-id>
py -3 -m kobo.cli visual-regenerate <image-id> --work <work-id> --session <session-id>
py -3 -m kobo.cli visual-approve --work <work-id> --session <session-id>
py -3 -m kobo.cli visual-finalize --work <work-id> --session <session-id>
```

`visual-show html`は、HTML本文を巨大なJSON文字列として端末へ全表示するのではなく、主にファイルパス、版、画像数、文字数、状態を返してください。必要なら内容表示用オプションを別に設けます。

## 6. 保存構造

作業中:

```text
.kobo/works/{work_id}/visual/{session_id}/
  source.md
  CHARACTER_VISUAL_BIBLE.v001.md
  ILLUSTRATION_PLAN.v001.json
  ILLUSTRATION_PLAN.v001.md
  tasks/
  images/
    cover.png
    illustration-001.png
    illustration-002.png
    ...
  preview/
    index.html
    assets/
```

確定版:

```text
.kobo/works/{work_id}/reading/
  CHAPTER-{chapter:03d}.v{version:03d}/
    index.html
    manifest.json
    assets/
      cover.png
      illustration-001.png
      illustration-002.png
      ...
```

確定済み版は上書き禁止です。

## 7. データベース

SQLiteへ、最低限次に相当する状態を保持してください。

### visual_sessions

- session_id
- work_id
- manuscript_document_id
- chapter_number
- source_path
- source_sha256
- status
- target_chars_per_image
- min_chars_between_images
- max_chars_without_image
- cover_enabled
- image_adapter
- latest_mail_id
- error
- created_at
- updated_at

### visual_images

- image_id
- session_id
- ordinal
- kind (`cover` / `body`)
- anchor_start
- anchor_end
- insert_after_paragraph
- scene_summary
- prompt_path
- output_path
- alt_text
- caption
- status
- attempt
- error
- created_at
- updated_at

### visual_documents

- session_id
- version
- path
- manifest_path
- source_sha256
- image_count
- created_at

同じ本文正本・同じ版に対する二重確定を拒否してください。

## 8. 挿絵の間隔規則

既定値を`kobo.json`へ設定可能にしてください。

推奨既定値:

```json
{
  "visual_publish": {
    "enabled": true,
    "target_chars_per_image": 1200,
    "min_chars_between_images": 700,
    "max_chars_without_image": 1800,
    "min_body_images": 3,
    "max_body_images": 8,
    "cover_enabled": true,
    "aspect_ratio": "4:3",
    "image_format": "png"
  }
}
```

原則:

- 画像位置は固定文字数で文章を機械的に切断しない
- 文字数は「画像を必要とする時期」を決める目安
- 実際の挿入位置は、場面転換、時間経過、場所移動、感情の山、問題発見、解決、章末フックを優先
- 会話の途中、同一段落の途中、文の途中へ入れない
- 見出し直後に連続して画像を置きすぎない
- 画像と画像の間は原則700文字以上
- 画像なしの本文が1800文字を超えないようにする
- 5,000〜10,000字の章なら、本文挿絵は概ね4〜8枚を目安にする
- 表紙画像は本文挿絵数へ含めない
- 同じ構図、同じ人物の立ち絵だけが連続しないよう、場所・距離・人物・物体を変える

8,269文字の第一話では、表紙1枚と本文挿絵5〜7枚程度になることを期待します。最終枚数は意味のある場面境界によって決めてください。

## 9. 挿絵計画

最初に画像を生成せず、`illustration-planner`が挿絵計画を作成してください。

計画の各項目:

- image_id
- kind
- 挿入先の段落番号
- その時点までの累積文字数
- 対象場面の開始・終了段落
- 場面要約
- 画像で見せる主題
- 登場人物
- 場所
- 時刻・天候
- 感情
- 構図
- 必須小物
- 避ける内容
- alt_text
- caption（空でも可）
- 生成プロンプト参照先

計画には、本文に存在しない人物・衣装・出来事を勝手に追加しないでください。

## 10. 人物・画風の一貫性

章ごとの画像が別作品に見えないよう、作品単位の視覚バイブルを導入してください。

```text
CHARACTER_VISUAL_BIBLE.v001.md
```

最低限:

- 主人公と主要人物の年齢、体格、髪、目、服、道具、表情傾向
- 猫などの継続登場物
- 建物、町、工房、主要設備
- 色調、画風、光、時代感
- 禁止事項
- 既存画像の参照パス

最初の表紙または人物基準画像を生成した後、以降の画像では可能ならその画像を参照資料として使用してください。

参照画像を直接渡せない生成経路の場合も、同じ視覚バイブル全文または必要部分を各プロンプトへ含め、人物特徴を省略しないでください。

画像内へ不要な文字、ロゴ、透かし、読めない看板文字を生成しないよう指定してください。

## 11. Antigravity画像生成

人狼・神託会議で使用中の`agy`を再利用します。ただし、現在の`AgyAdapter`は主に生テキストをstdoutへ返す契約です。画像生成は別責務として実機確認してください。

Googleの公式Codelabでは、Antigravity CLIへ画像生成を依頼し、生成画像をプロジェクトの指定ディレクトリへ保存する例があります。

実装前に、一時ディレクトリで非破壊スモークを行ってください。

期待する試験:

1. 一時ディレクトリを作成
2. オリジナルの簡単な風景画像を1枚生成するよう`agy`へ依頼
3. 出力先を明示
4. PNGまたはJPEGが実際に作成されたことを確認
5. 画像マジックナンバー、寸法、ファイルサイズを検証
6. 一時ディレクトリを削除

`agy --print`でツール実行・画像保存が可能かを実機で確認してください。可能なら専用`AgyImageAdapter`を実装します。

推奨:

```text
kobo/agy_image.py
```

既存`AgyAdapter`を複雑化させすぎないでください。

画像生成アダプターの契約:

- プロンプトはUTF-8
- 出力ディレクトリは作業ルート配下へ限定
- 期待するファイル名を決定的に指定
- `shell=False`
- timeout
- command not found
- authentication
- quota/session limit
- non-zero exit
- 出力ファイルなし
- 複数ファイル生成
- 不正拡張子
- 巨大ファイル
- 画像ではない偽ファイル
- Windowsのargv長
- 中断後の再開

を区別してください。

`agy --print`で安定して画像ファイルを生成できない場合、推測で成功扱いにしてはいけません。利用可能なAntigravityプラグイン、MCP、SDK経路を調査し、実機で成功した経路だけを採用してください。

画像生成不可を理由にHTML機能全体を捨てず、挿絵計画・HTMLレンダラー・状態管理まで実装してよいですが、最終受入条件は第一話で実画像が生成されることです。

## 12. HTMLレンダリング

HTMLはローカルの決定的処理で生成してください。HTML本文の生成自体を外部AIへ丸投げしないでください。

要件:

- UTF-8
- HTML5
- 外部CDN不要
- オフライン閲覧可能
- CSSを同梱
- JavaScriptなしでも全文が読める
- 相対パスの画像
- スマートフォン対応
- 本文幅は読みやすい範囲（目安680〜800px）
- 日本語本文18〜20px相当
- 行間1.8〜2.0
- 段落間の余白
- 明るいテーマを既定にする
- OS設定に応じたダークテーマ対応は可
- 文字サイズ変更ボタンは任意
- 見出し、地の文、会話を読みやすくする
- 画像は横幅100%、縦横比維持
- `loading="lazy"`
- alt属性必須
- figcaptionは任意
- 印刷時にも本文が欠けない
- ファイルを直接ダブルクリックして読める

### 本文完全性

正本Markdownから次を除いた小説本文を、順序を変えずHTMLへ変換してください。

- 確定版管理用のメタデータ
- `## 未解決事項`など読者へ見せない制作管理節（明示設定可能）

それ以外の本文は省略禁止です。

レンダリング前後で、本文の正規化済みテキストを比較してください。

- HTMLタグを除去
- HTML entityを復号
- 空白と改行を規則どおり正規化
- 元本文と一致

一致しなければ出版を失敗させてください。

要約、言い換え、段落削除、会話削除は禁止です。

## 13. セキュリティ

- HTML特殊文字を必ずescape
- Markdown中の生HTMLを既定で無効化
- `javascript:`、外部script、iframeを禁止
- 画像パスのルート外参照を禁止
- 任意ファイル読込みを禁止
- SVGはスクリプト混入を避けるため、初期対応では禁止してよい
- PNG/JPEG/WebPだけを許可
- EXIF等の個人情報メタデータを削除できるなら削除
- 利用者の本名、会社情報、秘密情報を画像プロンプトへ含めない

## 14. 中断再開

画像生成は時間がかかり、途中で利用枠切れになる可能性があります。

- 完成済み画像を再生成しない
- `visual_images.status`ごとに再開
- 画像ごとのattemptとerrorを記録
- 一枚失敗しても完成画像を削除しない
- 同じimage_idと同じ出力パスを再利用
- 不完全画像は`.partial`またはstagingへ置き、検証後に原子的rename
- HTMLは全必要画像の検証後に生成
- HTML生成後に停止しても、同じ版で確定を再開

## 15. 承認

画像とHTMLは自動確定しないでください。

プレビュー生成後:

- HTMLパス
- 本文文字数
- 画像枚数
- 各画像のパス
- 各画像の挿入位置
- 警告

を利用者へ提示し、承認後にだけ`visual-finalize`を許可します。

個別画像だけを`visual-regenerate`できるようにしてください。本文生成をやり直してはいけません。

## 16. 第一話での実地受入試験

機能実装だけで完了としてはいけません。

既存の第一話:

```text
novels/prototype-001/CHAPTER-001.v001.md
```

を入力として、実際に挿絵付きHTMLを作成してください。

リポジトリへレビュー用成果物を保存してください。

```text
novels/prototype-001/illustrated-html-v001/
  index.html
  manifest.json
  CHARACTER_VISUAL_BIBLE.v001.md
  ILLUSTRATION_PLAN.v001.md
  assets/
    cover.png
    illustration-001.png
    ...
```

このレビュー用成果物は、機能が実際に動いた証拠です。

条件:

- 元本文8,269文字を要約しない
- 本文の主要部分がすべてHTMLに入る
- 表紙1枚
- 本文挿絵5〜7枚を目安
- 少なくとも次の場面を含む
  1. 灰炉工房とリオ・ミナ・コゲ
  2. 食堂または診療所の調査
  3. ミナの帳面から異音記録を見つける場面
  4. 吹雪の圧力調整槽での修理
  5. 温かいスープとミナの任命
  6. 禁止刻印の発見
- 同一人物の外見が極端に変わらない
- 画像の間隔が極端に偏らない
- ブラウザで画像がすべて表示される
- PCとスマートフォン幅で読める

## 17. テスト

最低限:

```powershell
py -3 -m unittest tests.test_visual_publish -v
py -3 -m unittest tests.test_agy_image -v
py -3 -m unittest discover -v
py -3 -m compileall -q kobo mail tests
git diff --check
```

テスト項目:

- 文字数から候補位置を算出
- 場面境界を優先
- 文・段落途中へ入れない
- min/max間隔
- 最小・最大画像数
- 短い章
- 長い章
- 日本語文字数
- 本文完全性比較
- HTML escape
- パストラバーサル拒否
- 画像欠落
- 画像偽装
- 中断再開
- 一枚だけ再生成
- 二重確定拒否
- dummyは画像を生成したと偽らない
- 実外部AIを通常ユニットテストで呼ばない

実画像スモークは明示コマンドだけで実行してください。

## 18. 現行コードの古い命名

`kobo/manuscript.py`には、Antigravity移行後も`_execute_gemini`や`adapter="gemini"`等の古い命名が残っている可能性があります。

今回の変更に直接関係する範囲では、実態に合うprovider-neutralな名前へ修正してください。ただし、広範な無関係リファクタリングは行わないでください。

## 19. 文書更新

次を更新してください。

- README
- SPEC
- QandA（必要な判断があれば）
- サンプル設定

READMEへ、最低限次を記載してください。

- 確定本文はMarkdown正本
- 挿絵付きHTMLは派生出版物
- 既定の挿絵間隔
- Antigravity画像生成のdoctor/smoke
- プレビュー、個別再生成、承認、確定
- オフライン閲覧方法

## 20. 禁止事項

- 第一話を要約してHTMLへ載せる
- 表紙1枚だけで完了扱い
- 画像生成機能を手作業で代替して完了扱い
- ChatGPT上で作った画像を手動コピーして実装成功とする
- 本文を画像生成プロンプトの都合で書き換える
- 画像が失敗したのに空の`img`タグを残す
- 画像なしHTMLを挿絵付き完成版と呼ぶ
- 著作権のある既存作品・キャラクターの画像を無断利用
- 外部サイトの画像をスクレイピングして使用
- 利用者確認なしの自動確定
- 新しいdevloop改造

## 21. 完了報告

次を作成してください。

```text
instructions/result-20260728-17.md
```

記載:

- 実装した工程
- 画像生成の実機契約
- 使用したAntigravityモデル・画像機能
- 本文文字数
- 表紙枚数
- 本文挿絵枚数
- 平均・最小・最大の画像間文字数
- 本文完全性検査結果
- HTMLパス
- 各画像パス
- テスト件数
- doctor/smoke結果
- コミットSHA
- push結果
- 未解決事項

意味のある単位でコミットし、通常テストと第一話実地試験が成功したらpushしてください。

利用枠が25%以下になった場合は、新しい工程へ進まず、現在の成果物を検証・コミット・pushし、未完了を正直に記録してください。
