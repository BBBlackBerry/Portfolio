"""Threads 長期アクセストークンを明示的にリフレッシュする (cron / launchd 用).

使い方:
  uv run python scripts/refresh_threads_token.py            # 強制リフレッシュ
  uv run python scripts/refresh_threads_token.py --if-stale # 50日経過時のみ

cron 例 (毎日4:00に if-stale モードで回す — 50日経過時だけ実際にAPIを叩く):
  0 4 * * *  cd /path/to/yt-to-social && /usr/local/bin/uv run python scripts/refresh_threads_token.py --if-stale >> output/threads_token.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.threads_token import REFRESH_AFTER_SECONDS, current_access_token, refresh_now  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Threads 長期トークンを再発行する")
    p.add_argument(
        "--if-stale",
        action="store_true",
        help="50日以上経過している場合のみリフレッシュ (cron推奨)",
    )
    args = p.parse_args()

    if args.if_stale:
        # current_access_token() が中で必要に応じて自動リフレッシュする
        token = current_access_token()
        print(f"[{time.strftime('%Y-%m-%dT%H:%M:%S%z')}] if-stale check done (token tail: …{token[-8:]})")
        return 0

    state = refresh_now(force=True)
    print(
        json.dumps(
            {
                "access_token_tail": f"…{state.access_token[-12:]}",
                "issued_at": state.issued_at,
                "expires_in": state.expires_in,
                "expires_in_days": round(state.expires_in / 86400, 1),
            },
            indent=2,
        )
    )
    print(
        f"次のリフレッシュ目安: {REFRESH_AFTER_SECONDS / 86400:.0f}日後 "
        f"(失効まで {state.expires_in / 86400:.0f}日)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
