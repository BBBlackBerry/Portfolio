"""就活ニュース下書き生成の一連の流れをまとめるパイプライン.

  1. 固定チャンネルの動画をDBコーパスに取り込む (任意)
  2. DuckDuckGo ニュース検索で直近の就活ニュースを集める
  3. 各記事の本文を取得し、Claude で動画コーパスとの関連度を判定
  4. すべての記事について X / Threads の下書きを生成し DB に保存。
     関連度が高ければ動画ブリッジ付き、低ければニュース単独投稿として扱う
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from ..config import settings
from . import channel, db, news
from .analyze import analyze_news


def _log(msg: str) -> None:
    print(f"[jobnews.pipeline] {msg}", file=sys.stderr, flush=True)


@dataclass
class PipelineResult:
    corpus: dict[str, int] = field(default_factory=dict)
    news_found: int = 0
    news_analyzed: int = 0
    drafts_created: int = 0  # 全分析記事 = 全下書き
    summaries_created: int = 0  # うち関連動画なしの単独投稿
    skipped_already_seen: int = 0
    errors: list[str] = field(default_factory=list)


def run_pipeline(*, refresh_channel: bool = False) -> PipelineResult:
    """フルパイプラインを実行する. UIの「下書き生成」ボタンから呼ばれる.

    refresh_channel=True の場合のみ YouTube チャンネルコーパスを再取得する.
    既定では DuckDuckGo ニュース検索 → Claude 分析 → 下書き生成 のみを行い、
    DB に既存の動画コーパスがあれば関連度マッチングに利用する.

    取得したニュースは全て下書き化して保存する。関連動画が見つかれば動画
    ブリッジ付きの下書き、見つからなければニュース単独投稿の下書きになる。
    """
    result = PipelineResult()
    db.init_db()

    # 1. チャンネルコーパスの更新 (任意。UIのボタンからは呼ばれない)
    if refresh_channel:
        try:
            result.corpus = channel.refresh_corpus()
        except Exception as e:
            msg = f"チャンネル取り込み失敗: {e}"
            _log(msg)
            result.errors.append(msg)

    # 動画コーパスは空でも可。空ならニュース単独投稿モードのみで動作する.
    videos = db.all_videos()

    # 2. ニュース検索
    try:
        items = news.search_job_news()
    except Exception as e:
        msg = f"ニュース検索失敗: {e}"
        _log(msg)
        result.errors.append(msg)
        return result
    result.news_found = len(items)

    # 3-4. 記事ごとに関連度判定 → 下書き生成 → 保存 (全件保存)
    rel_threshold = settings.jobnews_relevance_threshold
    for item in items:
        if db.draft_url_seen(item.url):
            result.skipped_already_seen += 1
            continue

        item.content = news.fetch_article_text(item.url)
        try:
            analysis = analyze_news(item, videos)
        except Exception as e:
            # 個別記事の分析失敗は集計から除外するだけで、UIにはエラーを出さない。
            # 詳細は stderr ログ側で追跡する。
            _log(f"分析失敗 {item.url}: {e}")
            continue
        result.news_analyzed += 1

        # 関連動画があり関連度がしきい値以上なら「動画ブリッジ付き下書き」、
        # それ以外は「ニュース単独投稿」として保存。どちらの場合も投稿可能な下書きを残す。
        is_related = (
            bool(analysis.matched_video_id) and analysis.relevance_score >= rel_threshold
        )

        db.insert_draft(
            news_title=item.title,
            news_url=item.url,
            news_published=item.published,
            news_summary=analysis.news_summary,
            matched_video_id=analysis.matched_video_id if is_related else None,
            matched_video_title=analysis.matched_video_title if is_related else None,
            matched_video_url=analysis.matched_video_url if is_related else None,
            relevance_score=analysis.relevance_score,
            relevance_reason=analysis.relevance_reason,
            newsworthiness_score=analysis.newsworthiness_score,
            draft_x=analysis.draft_x,
            draft_threads=analysis.draft_threads,
        )
        result.drafts_created += 1
        if not is_related:
            result.summaries_created += 1

    _log(
        f"news_found={result.news_found} analyzed={result.news_analyzed} "
        f"drafts={result.drafts_created} summaries={result.summaries_created} "
        f"seen={result.skipped_already_seen} errors={len(result.errors)}"
    )
    return result
