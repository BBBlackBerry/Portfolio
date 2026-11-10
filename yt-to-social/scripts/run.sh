#!/usr/bin/env bash
# yt-to-social 起動スクリプト
# 使い方: ./scripts/run.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "[!] .env が存在しません。.env.example をコピーして編集してください:"
  echo "    cp .env.example .env"
  exit 1
fi

# 依存解決 (uvが.venvを自動生成)
uv sync

# サーバー起動
uv run python -m server.app
