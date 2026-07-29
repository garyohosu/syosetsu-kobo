# 次にやる作業

## 題目

C01「温かいスープと黒猫の薬草小屋」の改訂（`READER_PROFILE.v003.md`を正本として）から、CONCEPT確定・ストーリーバイブル草案・独立整合性監査までの継続。

前回の`hikitsugi.md`（devloop実行層の不具合修正、`instruction-20260727-12.md`）は解消済みのため、本ファイルは今回の題目へ差し替えた。旧内容が必要な場合は、このコミットの1つ前の`hikitsugi.md`をGit履歴から参照すること。

## 直前までの状況（重要な前提）

このセッションの前半で、`novels/prototype-001/editorial-board-v002/`が「実Antigravity生成」と主張していたにもかかわらず、プロジェクトの実DB（`.kobo/state.db`）にそのセッションの記録が一切ないという重大な不整合を発見した。旧内容は`novels/prototype-001/editorial-board-v002-unverified/`へ改名保存し、`READER_PROFILE.v002.md`を入力に実Antigravityで**検証可能な**新しいセッションを実行し直して、`editorial-board-v002/`を正式出版した（コミット`2664047`、セッションID`concept-169e90deba774338951b29d14b076073`）。詳細は`instructions/result-20260728-21.md`を参照。

**教訓（次のエージェントも厳守）**: 「実AI生成」を主張する成果物は、`.kobo/state.db`の`concept_sessions`／`concept_candidates`／`concept_evaluations`と突き合わせて裏付けを取ってから信頼すること。PROVENANCE.jsonの記述やファイル内容の完成度だけでは真正性を保証できない。

## 今回の経緯

利用者が`editorial-board-v002`のC01「温かいスープと黒猫の薬草小屋」を改訂対象に選び、物語内容への確定フィードバックを直接チャットで提示した。定時のため、この確定フィードバックを正本として保存するところまでで作業を打ち切り、実際の改訂生成（実Antigravity呼び出し）は次回セッションへ引き継ぐ。

## 今回やったこと（このセッションで完了）

1. `novels/prototype-001/READER_PROFILE.v003.md`を新規作成した。利用者が確定したC01改訂指示の全文（作品の方向性、主人公、武官、敵、黒猫、各話の基本構造、第一話の中心、設定と情報開示の8節）を正本として保存している。`v001`・`v002`は削除・上書きしていない。
2. `latest_reader_profile()`はファイル名の数値版を機械的に比較するため、`prototype-001`は次回セッション以降、自動的にv003を最新版として参照する（コード変更は不要、確認のみ推奨）。
3. 文字品質の簡易スキャン（ハングル・U+FFFD・制御文字・日本語間の孤立英語機能語）を実施し、`READER_PROFILE.v003.md`に混入がないことを確認した。
4. 実Antigravity呼び出し、`concept-select`、`CONCEPT.vNNN.md`確定、ストーリーバイブル草案生成、独立整合性監査は**一切実行していない**。

## 次にやること（利用者が明示した工程の順序）

利用者の指示原文（要約せず、そのままの順序で実行すること）。

1. **実AntigravityでC01を改訂する。** `novels/prototype-001/READER_PROFILE.v003.md`を固定入力とし、`novels/prototype-001/editorial-board-v002/candidates/candidate-c01.md`（セッション`concept-169e90deba774338951b29d14b076073`）を改訂対象として、`--dummy`なし・adapter=`agy`で実行する。既存候補・既存セッションは上書きしない（`kobo/concept.py`の`concept-revise`アクション、または新規セッションでの再生成が候補。どちらが適切かは`kobo/concept.py`の`action(...,"revise",...)`の仕様を確認してから判断すること）。
2. **改訂版を利用者が確認できる形で提示する。** 新しい編集会議版を**非上書き**で出版する（`concept-publish`は既存の`editorial-board-vNNN`を自動でスキップして次の版番号を使うため、通常操作で問題ない）。
3. **利用者の確認待ちで停止する。** 改訂版を提示したら、そこで止まり、利用者の明示的なフィードバック（承認 or 再改訂指示）を待つこと。無断で次工程（正式選択）へ進まない。
4. 問題がなければ、利用者の承認を受けてから`concept-select`でC01を正式選択し、`concept-finalize`で`CONCEPT.vNNN.md`を確定する。
5. その後、ストーリーバイブル**草案**を生成する（`agents/`配下に該当する担当がいるか要確認。`works.next_agent='story-architect'`が`concept-finalize`後に設定される想定だが、担当エージェント定義の有無をまず確認すること）。
6. ストーリーバイブル草案について、**生成担当とは別の担当**による独立整合性監査を行う（`concept-reviewer`のような分離設計を踏襲し、同一AI・同一プロンプトでの自己レビューにしないこと）。
7. **ここで再び利用者の確認待ちで停止する。**

## 禁止事項（今回・次回とも厳守）

利用者から明示の承認を得るまで、次へ進んではならない。

- ストーリーバイブルの**正式確定**（草案の提示・監査までは可、確定は不可）
- 全体プロット
- 第一話本文
- 表紙
- 挿絵
- 公開用HTML

## 参考: v003の位置づけ

`READER_PROFILE.v003.md`は、`v002`が持つ一般的な読者嗜好（強く優先すること・避けること等）を置き換えるものではなく、C01改訂に特化した上乗せの指示として書いた。改訂プロンプトを構成する際は、v002の一般方針とv003のC01固有指示の両方を参照すること（`kobo/concept.py`の`_candidate_prompt`／リビジョン用プロンプトの実装を確認し、両ファイルの内容が矛盾なく渡るようにする）。

## Git状態

- 現在のブランチ: `main`
- 直前のコミット: `2664047`（editorial-board-v002の真正性修正、origin/mainへpush済み）
- 本ファイルと`READER_PROFILE.v003.md`をコミット・push予定（このセッションの最後の操作）

## 次のエージェントが最初に確認すべきこと

```powershell
git status --short --branch
git log --oneline --decorate -5
git fetch origin
git pull --ff-only
```

その後、`AGENTS.md`、本ファイル、`instructions/result-20260728-21.md`、`novels/prototype-001/READER_PROFILE.v003.md`を読んでから、上記「次にやること」の1から着手する。
