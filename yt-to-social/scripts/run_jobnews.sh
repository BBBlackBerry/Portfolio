#!/usr/bin/env bash
# 就活ニュース自動収集アプリ 起動スクリプト
# 使い方: ./scripts/run_jobnews.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "[!] .env が存在しません。.env.example をコピーして編集してください:"
  echo "    cp .env.example .env"
  exit 1
fi

# 依存解決 (uvが.venvを自動生成)
uv sync

# サーバー起動 (既定ポート 8001)
uv run python -m server.jobnews.app
