"""DuckDuckGo News で就活ニュースを検索し、記事本文をスクレイピングする.

以前は Google Custom Search API を使っていたが、2026年5月時点で
新規 Programmable Search Engine では「Search the entire web」が選べず、
全Web対象の就活ニュース検索ができなくなったため DDG に乗り換えた.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS

from ..config import settings

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# JOBNEWS_DATE_RESTRICT (Google CSE 由来) を DDG の timelimit に変換する.
# DDG は d/w/m/y のみ受け付けるので、複数週指定 (w4) などは一番近い粒度に丸める.
_DATE_RESTRICT_TO_DDG_TIMELIMIT = {
    "d": "d", "d1": "d",
    "w": "w", "w1": "w",
    "w2": "m", "w3": "m", "w4": "m",
    "m": "m", "m1": "m",
    "y": "y", "y1": "y",
}


def _log(msg: str) -> None:
    print(f"[jobnews.news] {msg}", file=sys.stderr, flush=True)


@dataclass
class NewsItem:
    title: str
    url: str
    snippet: str
    published: str | None = None
    content: str = ""


def _timelimit() -> str:
    raw = (settings.jobnews_date_restrict or "").lower().strip()
    return _DATE_RESTRICT_TO_DDG_TIMELIMIT.get(raw, "m")


def search_job_news(
    queries: tuple[str, ...] | None = None,
    *,
    per_query: int = 5,
) -> list[NewsItem]:
    """設定された検索クエリでニュースを集める. URLで重複排除."""
    queries = queries or settings.jobnews_search_queries
    if not queries:
        raise RuntimeError("JOBNEWS_SEARCH_QUERIES が空です。検索キーワードを設定してください。")

    timelimit = _timelimit()
    seen: set[str] = set()
    results: list[NewsItem] = []
    errors: list[str] = []
    with DDGS() as ddgs:
        for q in queries:
            try:
                hits = list(
                    ddgs.news(
                        q,
                        region="jp-jp",
                        safesearch="off",
                        timelimit=timelimit,
                        max_results=per_query,
                    )
                )
            except Exception as e:
                _log(f"DDG検索失敗 q={q!r}: {e}")
                errors.append(f"{type(e).__name__}: {e}")
                continue

            for item in hits:
                url = item.get("url", "")
                if not url or url in seen:
                    continue
                seen.add(url)
                results.append(
                    NewsItem(
                        title=(item.get("title") or "").strip(),
                        url=url,
                        snippet=(item.get("body") or "").strip(),
                        published=item.get("date") or None,
                    )
                )
    _log(f"queries={len(queries)} 取得記事={len(results)} 失敗クエリ={len(errors)}")

    if not results and errors:
        uniq = list(dict.fromkeys(errors))
        raise RuntimeError(
            f"DuckDuckGoニュース検索が全クエリ({len(queries)}件)で失敗しました: "
            f"{' / '.join(uniq)}"
        )
    return results


def fetch_article_text(url: str, *, max_chars: int = 6000) -> str:
    """記事URLから本文テキストを抽出する (簡易. <p> を結合)."""
    try:
        with httpx.Client(
            timeout=20, headers={"User-Agent": _UA}, follow_redirects=True
        ) as cli:
            r = cli.get(url)
            r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
            tag.decompose()
        root = soup.find("article") or soup.body or soup
        paragraphs = [p.get_text(" ", strip=True) for p in root.find_all("p")]
        text = "\n".join(p for p in paragraphs if len(p) > 20)
        if not text:
            text = root.get_text(" ", strip=True)
        return text[:max_chars]
    except Exception as e:
        _log(f"記事取得失敗 {url}: {e}")
        return ""
