---
name: generate-social-posts
description: YouTube動画の字幕からX (旧Twitter) と Threads 向けの投稿文を、戦略ドキュメントに準拠した形式で生成する。n8n / 業務自動化 / AI活用 ブランドの発信に最適化。
---

# generate-social-posts

このSkillは、YouTube動画の字幕（または要約）を入力として受け取り、**X** と **Threads** 向けの投稿文を生成します。生成は下記の「戦略ドキュメント」に厳密に従ってください。

---

## 入力

呼び出し元から以下の形式で渡されます:

```json
{
  "video_title": "動画タイトル",
  "video_url": "https://youtube.com/watch?v=xxxx",
  "transcript": "字幕本文 (長文可)"
}
```

各プラットフォーム (X / Threads) につき **必ず 1 投稿のみ** 返してください (`x_posts` / `threads_posts` はそれぞれ要素数 1 の配列)。

## 出力

以下のJSON Schemaに**厳密に**従って出力してください。説明文や前置きは一切不要、JSONのみ。

```json
{
  "x_posts": [
    {
      "pattern": "断言型 | 問題提起型 | 数字訴求型 | 実務イメージ型 | 保存誘導型",
      "psychological_trigger": "FOMO | 簡便性 | 権威性",
      "text": "投稿本文 (140字以内推奨, 280字上限)",
      "image_prompt": "画像生成AIに渡す英語プロンプト (詳細仕様は §7 を参照)",
      "image_badge_jp": "画像左上ピル: 動画のジャンル/カテゴリ (例: n8n × AI / 業務自動化 / 2026最新)。6〜10字。",
      "image_headline_jp": "画像の大見出し: 動画の最大の主張を1フレーズで (例: 転記作業を全部自動化)。10〜14字。改行は1回まで。",
      "image_subhead_jp": "小見出し: 誰に何が起きるかの補足 (例: 非エンジニアでも30分で構築可能)。15〜22字。",
      "image_bullets_jp": ["箇条書き1 (具体ポイント / 数字 / ツール名)", "箇条書き2", "箇条書き3"],
      "image_footer_jp": "(未使用 / サーバー側で固定文字列に上書きされるため空文字でOK)",
      "image_caption_jp": ""
    }
  ],
  "threads_posts": [
    {
      "pattern": "会話調 | 気づき共有 | 安心感訴求 | 失敗談風 | 共感CTA",
      "psychological_trigger": "FOMO | 簡便性 | 権威性",
      "text": "投稿本文 (500字程度, 改行多め, 柔らかい口調)",
      "image_prompt": "画像生成AIに渡す英語プロンプト (詳細仕様は §7 を参照)",
      "image_badge_jp": "...",
      "image_headline_jp": "...",
      "image_subhead_jp": "...",
      "image_bullets_jp": ["...", "...", "..."],
      "image_footer_jp": "...",
      "image_caption_jp": ""
    }
  ]
}
```

**重要:**
- `image_badge_jp` `image_headline_jp` `image_subhead_jp` `image_bullets_jp` の4フィールドは **必ず動画の固有情報** (ツール名、登場した数字、登場した手法、出てきた具体的なシーン) を反映させてください。汎用的な文言 (例:「業務自動化のヒント」のような誰にでも当てはまる言葉) は禁止です。
- 画像に **焼かれる** 日本語テキストは **`image_badge_jp` と `image_headline_jp` の2つだけ** です。`image_subhead_jp` と `image_bullets_jp` は画像にテキストとして描画されず、Gemini に渡す「システム図のヒント」として、ノード・アイコン・矢印などの図解要素に変換されます。
- `image_footer_jp` はサーバー側で「脱・属人チャンネル┃AI×DX」に固定上書きされます。`@your_handle` のような署名を書く必要はありません (書いても無視されます)。
- `image_caption_jp` は後方互換のための空文字でOK。

---

## 戦略ドキュメント (絶対遵守)

### 1. ブランドポジション
- ターゲット: **n8n初心者 / 業務改善担当 / 非エンジニア**
- ジャンル: **n8n / 業務自動化 / AI活用**
- 最終目的: BtoCコンテンツ販売（教材・ワークフロー・有料note）の発信基盤構築

### 2. 媒体別の役割
| 媒体 | 役割 | 文体 | 文字数目安 |
|---|---|---|---|
| X | 専門家ブランド確立・認知拡散 | 断言・即実践・テンポ重視 | 140字以内推奨 |
| Threads | エンゲージ獲得・noteへの導線 | 会話調・改行多め・柔らかい | 300〜500字 |

### 3. 3つの心理的トリガー (必ず1投稿につき1つを軸に)
1. **FOMO (置いていかれる不安)** — 「まだやってないの？」「2026年に〜したい人は」
2. **簡便性 (すぐ得したい欲)** — 「努力がいらない」「5分で」
3. **権威性 (断定)** — 「結論：〇〇です」と言い切る

### 4. X 投稿パターン (5種)
| パターン | テンプレ例 |
|---|---|
| 断言型 | 「結論：その作業、n8nで自動化できます。」 |
| 問題提起型 | 「まだChatGPTの結果をコピペしてスプシに貼ってますか？」 |
| 数字訴求型 | 「n8nで自動化してよかったこと3つ（転記ミス消滅など）」 |
| 実務イメージ型 | 「よく作っているn8n構成👇 フォーム→PDF→Sheets...」 |
| 保存誘導型 | 「n8n初心者が最初に作るべき自動化3選」 |

**X 構成テンプレ:**
- フック (1行目): 断言型 or 問題提起型
- 本文 (3〜5行): 何ができる/何が楽になるかを箇条書き
- CTA (最終行): 動画への誘導は必ず **リプ先動画への誘導文** で行う。例: 「具体的な手順はリプライ先の動画で解説してます👇」「実際の構築画面はリプの動画で👇」「続きはリプの動画で👇」。`{url}` や `youtube.com/...` は本文に絶対に書かない。保存系CTA (「あとで使う人は保存」等) は誘導文と組み合わせてOK。

### 5. Threads 投稿パターン (5種)
| パターン | テンプレ例 |
|---|---|
| 会話調 | 「業務自動化は難しそうだけど、実は身近なところからでOK。」 |
| 気づき共有 | 「自動化は『魔法』じゃなくて『段取り』なんだなという気づき。」 |
| 安心感訴求 | 「エンジニアじゃなくても、ノーコードで実務は回せます。」 |
| 失敗談風 | 「全部やろうとして失敗した経験。まずは1工程から。」 |
| 共感CTA | 「毎日同じ作業を繰り返してる人。同じ悩みならいいねを。」 |

**Threads 構成テンプレ:**
- 導入: 改行多めの柔らかな会話調
- 気づき / 主張: 本質的な「一言」
- 補足: 失敗談 / 実例で共感を得る
- CTA: 「同じ人いたらいいね」など軽いアクション。動画誘導を入れる場合は **リプ先動画への誘導文** のみ (例: 「やり方はリプ欄の動画で詳しく話してます👇」)。URL本体は本文に書かない。

### 6. NG事項 (絶対に守る)
- ❌ 宣伝色を出さない (売り込み感は忌避される)
- ❌ 専門用語を多用しない (非エンジニア向け)
- ❌ 1投稿に複数メッセージを詰め込まない (**1投稿1メッセージ厳守**)
- ❌ 完璧な解説を1投稿に詰めない (消化しやすい量に)
- ❌ ハッシュタグの乱用 (X: 0〜1個, Threads: 0個)
- ❌ 絵文字の乱用 (1〜3個まで, 視認性のためのみ)
- ❌ **投稿本文に YouTube URL / 短縮URL / `{url}` プレースホルダを入れない** (X/Threadsともリンク貼付はアルゴリズム的に不利。動画誘導は「リプ先の動画で👇」等の文言のみで行う)

### 7. 画像方針

画像は雑誌カバー風の2層構造で生成されます:

1. **背景レイヤー (Geminiが英語プロンプトから生成)**: 動画テーマを象徴する図解・実務ビジュアル
2. **情報レイヤー (PILで後段合成)**: バッジ / 大見出し / 小見出し / 番号付き箇条書き / フッター

このため `image_prompt` は **背景レイヤー専用の指示** です。日本語テキストは含めず、視覚的な「絵」を作るための仕様だけを書いてください。

**`image_prompt` 必須の構成要素 (英語・100〜180単語):**

1. **SCENE / METAPHOR**: 動画の核心を象徴する具体シーン1つ。汎用フレーズ禁止。
   - 良い例: `an isometric n8n workflow canvas with three connected nodes — a webhook trigger node, an AI processing node depicted as a glowing brain icon card, and a Google Sheets output node — connected by orange routed edges with small data packet glyphs flowing along them`
   - 悪い例: `an infographic about automation`
2. **HERO ELEMENT**: 右上〜右側に置く主役オブジェクト (アイコンスタック、ノードグラフ、ブラウザモック、ターミナルパネル、ドーナツチャート 等)
3. **SUPPORTING ELEMENTS** (2〜4個): ファイル束 / カーソル / ステップインジケータ / 折れ線 / バッジアイコン
4. **STYLE TOKENS** (固定で含める): `premium editorial infographic`, `flat design with soft depth`, `monoline 2px stroke icons`, `subtle drop shadow`, `clean asymmetric layout`
5. **PALETTE** (固定): `off-white canvas #F8FAFC, deep navy ink #0F172A, primary accent #2563EB, secondary accents #16A34A and #F59E0B used sparingly`
6. **COMPOSITION GUARD** (固定で末尾に含める): `keep the LEFT 55% and BOTTOM 40% visually quiet for a Japanese text panel overlay; no letters, no numerals, no text glyphs of any kind`

**良い `image_prompt` の例:**

```
A premium editorial infographic illustrating an AI-powered document processing pipeline.
HERO: in the upper-right, an isometric stack of three layered cards — top card shows a
generic form mockup with placeholder rectangles, middle card shows an AI gear icon
with subtle blue glow, bottom card shows a spreadsheet grid with highlighted rows.
Connecting them: routed orange dotted lines with small circular data-packet glyphs.
SUPPORTING: a small floating cursor indicator, two abstract progress dots, a tiny
shield icon for security. STYLE: premium editorial infographic, flat design with
soft depth, monoline 2px stroke icons, subtle drop shadow, clean asymmetric layout.
PALETTE: off-white canvas #F8FAFC, deep navy ink #0F172A, primary accent #2563EB,
secondary accents #16A34A and #F59E0B used sparingly.
COMPOSITION: keep the LEFT 55% and BOTTOM 40% visually quiet for a Japanese text
panel overlay; no letters, no numerals, no text glyphs of any kind.
```

**禁止事項:**
- ❌ プロンプト内に日本語を含めない (Geminiが化けた文字を描いてしまう)
- ❌ プロンプト内にテキスト/ロゴ/UIラベルの描画指示を入れない
- ❌ 写実的・3D・絵画調・水彩調 (フラット編集デザインのみ)
- ❌ 動画と関係のない汎用イラスト (ロボット / ライトバルブ / 一般オフィス風景 等)

---

## 生成ルール

1. **パターン選定**: 5パターンの中から、動画内容に最も合う 1 パターンを X / Threads それぞれで選ぶ (各プラットフォーム 1 投稿ずつ)
2. **時事性**: 字幕に固有名詞 (n8n, Claude, GPT-5 等) があれば積極的に使う
3. **数字**: 動画内で言及された具体的数字 (時間短縮、件数等) は必ず投稿に入れる
4. **CTA**: 各投稿に1つだけ、自然な行動喚起を入れる
5. **動画URLの扱い (重要)**: **X / Threads とも、投稿本文 (`text`) に YouTube URL を絶対に貼らないでください**。Xではアルゴリズム的に外部リンクを含む投稿が嫌われ、Threadsも同じ運用に統一しています。動画への誘導は本文末尾で「具体的な手順はリプライ先の動画で解説してます👇」「詳しくはリプ欄の動画で👇」「続きはリプの動画で👇」のような **リプ先動画への誘導文** で行ってください (URL本体は運用側がリプライ/概要欄で補完します)。`{url}` プレースホルダ・短縮URL・`youtu.be/...` 等も同様に禁止です。
6. **検証**: 出力前に、各投稿が NG事項に該当しないかセルフチェック (特に本文にURLが混入していないか)

---

## 呼び出し例

```
ユーザー: この動画から投稿を作って [URL]
→ Skillが字幕取得 → X / Threads 各 1 投稿ずつ JSON で返却 → サーバーが画像生成 → UIで確認 → 投稿
```
