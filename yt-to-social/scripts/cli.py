"""CLI: ターミナルから YouTube URL を渡して投稿案を生成 (画像なし、確認用).

使い方:
  uv run python scripts/cli.py "https://youtube.com/watch?v=..."
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.generate import generate_posts  # noqa: E402
from server.youtube import get_video  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: cli.py <youtube_url>")
        sys.exit(1)
    url = sys.argv[1]

    print(f"[*] 字幕取得中: {url}", file=sys.stderr)
    v = get_video(url)
    print(f"[*] タイトル: {v.title}", file=sys.stderr)
    print(f"[*] 字幕言語: {v.language} ({len(v.transcript)}字)", file=sys.stderr)

    print("[*] 投稿文生成中…", file=sys.stderr)
    g = generate_posts(v.title, v.url, v.transcript)

    print(json.dumps(g.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
