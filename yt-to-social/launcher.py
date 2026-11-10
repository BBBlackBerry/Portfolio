#!/usr/bin/env python3
"""アプリ起動ランチャー

ターミナルを使わずにアプリを起動できます。
  1. yt-to-social … YouTube動画 → X / Threads 投稿生成 (ポート 8000)

※ 「就活ニュース下書き生成」(旧アプリ②) は使用しなくなったため、
   下記 APPS から外して GUI 上にも表示しないようにしてあります。
   再び使いたい場合はコメントアウトを解除してください。
"""

import os
import re
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox
except ImportError:
    print("tkinter が利用できません。Python の標準 GUI ライブラリがインストールされているか確認してください。")
    sys.exit(1)


SCRIPT_DIR = Path(__file__).resolve().parent

# ── 生成画像の上限 ─────────────────────────────────────────────
# output/ 配下に貯まった生成画像が MAX_KEEP_IMAGES 枚を超えていたら
# 起動時に古い順から削除する (画像ファイル名は image.py の
# f"{int(time.time())}_{uuid.uuid4().hex[:8]}.png" 形式を判定する)。
MAX_KEEP_IMAGES = 100
OUTPUT_DIR = SCRIPT_DIR / "output"
_IMAGE_NAME_RE = re.compile(r"^\d+_[0-9a-f]{8}\.png$")

# ── カラーパレット ──────────────────────────────────────────────
BG = "#eef1f5"          # ウィンドウ背景
CARD_BG = "#ffffff"     # カード背景
BORDER = "#e2e8f0"      # カードの枠線
TEXT = "#0f172a"        # 主要テキスト
MUTED = "#64748b"       # 補助テキスト
ACCENT = "#2563eb"      # アクセント (ボタン)
ACCENT_HOVER = "#1d4ed8"
DISABLED = "#cbd5e1"
BADGE_BG = "#eff6ff"
BADGE_FG = "#2563eb"

# ステータス色
DOT_IDLE = "#cbd5e1"
DOT_BUSY = "#f59e0b"
DOT_OK = "#22c55e"
DOT_ERR = "#ef4444"

FONT_FAMILY = "Hiragino Sans"

# (キー, 表示名, 説明, 起動スクリプト, ブラウザURL, アクセント色)
APPS = [
    (
        "yt2social",
        "yt-to-social",
        "YouTube動画の字幕から X / Threads 向けの投稿文＋画像を生成します。",
        SCRIPT_DIR / "scripts" / "run.sh",
        "http://localhost:8000",
        "#2563eb",
    ),
    # ── 旧アプリ② (就活ニュース下書き生成) は使用しなくなったため非表示 ─────
    # 再度有効化するには下のタプルのコメントアウトを解除してください。
    # (
    #     "jobnews",
    #     "就活ニュース下書き生成",
    #     "固定YouTubeチャンネルの動画に関連する就活ニュースを自動収集し、\nすぐ投稿できる下書き (X / Threads) を生成します。",
    #     SCRIPT_DIR / "scripts" / "run_jobnews.sh",
    #     "http://localhost:8001",
    #     "#0d9488",
    # ),
]


def _cleanup_old_images(output_dir: Path = OUTPUT_DIR,
                        keep: int = MAX_KEEP_IMAGES) -> int:
    """生成画像が keep 枚を超えていたら古い順に削除し、削除枚数を返す。

    ファイル名先頭の UNIX タイムスタンプで古い順を判定するため、
    後から mtime が更新されても並び順は崩れない。
    """
    if not output_dir.is_dir():
        return 0
    images = [
        p for p in output_dir.iterdir()
        if p.is_file() and _IMAGE_NAME_RE.match(p.name)
    ]
    if len(images) <= keep:
        return 0
    images.sort(key=lambda p: int(p.name.split("_", 1)[0]))
    to_delete = images[: len(images) - keep]
    deleted = 0
    for p in to_delete:
        try:
            p.unlink()
            deleted += 1
        except OSError as e:
            print(f"古い画像の削除に失敗: {p.name} ({e})", file=sys.stderr)
    return deleted


def _port_in_use(port: int) -> bool:
    """指定ポートが既にバインドされているか判定する。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def _free_port(port: int, timeout: float = 3.0) -> bool:
    """指定ポートを掴んでいるプロセスを終了してポートを解放する。

    解放できた、または最初から空いていた場合に True を返す。
    """
    if not _port_in_use(port):
        return True

    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return False

    pids = {int(pid) for pid in result.stdout.split() if pid.strip().isdigit()}
    if not pids:
        return not _port_in_use(port)

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _port_in_use(port):
            return True
        time.sleep(0.2)

    # まだ掴んだままなら SIGKILL
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _port_in_use(port):
            return True
        time.sleep(0.2)
    return False


class RoundedButton(tk.Canvas):
    """角丸のカスタムボタン (macOS でも色が反映される)。"""

    def __init__(self, parent, text, command, width=200, height=42, radius=12,
                 bg=ACCENT, hover_bg=ACCENT_HOVER, fg="white",
                 disabled_bg=DISABLED, parent_bg=CARD_BG, font=None):
        super().__init__(parent, width=width, height=height,
                         highlightthickness=0, bd=0, bg=parent_bg)
        self.command = command
        self.radius = radius
        self.btn_w = width
        self.btn_h = height
        self.bg = bg
        self.hover_bg = hover_bg
        self.fg = fg
        self.disabled_bg = disabled_bg
        self.font = font or (FONT_FAMILY, 12, "bold")
        self._enabled = True
        self._text = text
        self._draw(bg)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _round_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def _draw(self, color):
        self.delete("all")
        self._round_rect(2, 2, self.btn_w - 2, self.btn_h - 2, self.radius,
                         fill=color, outline=color)
        self.create_text(self.btn_w // 2, self.btn_h // 2, text=self._text,
                         fill=self.fg, font=self.font)

    def _on_enter(self, _):
        if self._enabled:
            self._draw(self.hover_bg)
            self.config(cursor="hand2")

    def _on_leave(self, _):
        if self._enabled:
            self._draw(self.bg)
        self.config(cursor="")

    def _on_click(self, _):
        if self._enabled and self.command:
            self.command()

    def set_state(self, text=None, enabled=None):
        if text is not None:
            self._text = text
        if enabled is not None:
            self._enabled = enabled
        self._draw(self.bg if self._enabled else self.disabled_bg)


class AppLauncher:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("アプリ起動ランチャー")
        self.root.geometry("560x380")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        self.processes = {}  # key -> Popen
        self.cards = {}      # key -> {"button", "dot", "status"}

        # ── ヘッダー ───────────────────────────────────────────
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill=tk.X, padx=28, pady=(26, 6))

        tk.Label(
            header,
            text="🚀  アプリ起動ランチャー",
            font=(FONT_FAMILY, 19, "bold"),
            bg=BG,
            fg=TEXT,
        ).pack(anchor=tk.W)

        tk.Label(
            header,
            text="ボタンをクリックするとサーバーが起動し、ブラウザが自動で開きます。",
            font=(FONT_FAMILY, 11),
            bg=BG,
            fg=MUTED,
        ).pack(anchor=tk.W, pady=(4, 0))

        # ── アプリカード ───────────────────────────────────────
        for key, name, desc, script, url, accent in APPS:
            self._build_app_card(key, name, desc, script, url, accent)

        # ── フッター ───────────────────────────────────────────
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill=tk.X, side=tk.BOTTOM, padx=28, pady=(0, 18))

        self.status_label = tk.Label(
            footer,
            text="起動するアプリを選んでください。",
            font=(FONT_FAMILY, 10),
            bg=BG,
            fg=MUTED,
        )
        self.status_label.pack(anchor=tk.W)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_app_card(self, key, name, desc, script, url, accent):
        # 外枠 (枠線つき)
        outer = tk.Frame(
            self.root, bg=CARD_BG,
            highlightbackground=BORDER, highlightthickness=1, bd=0,
        )
        outer.pack(fill=tk.X, padx=28, pady=9)

        # 左側のアクセントバー
        tk.Frame(outer, bg=accent, width=5).pack(side=tk.LEFT, fill=tk.Y)

        body = tk.Frame(outer, bg=CARD_BG)
        body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=18, pady=16)

        # 1行目: アプリ名 + ポートバッジ
        top = tk.Frame(body, bg=CARD_BG)
        top.pack(fill=tk.X)

        tk.Label(
            top, text=name, font=(FONT_FAMILY, 14, "bold"),
            bg=CARD_BG, fg=TEXT,
        ).pack(side=tk.LEFT)

        port = url.rsplit(":", 1)[-1]
        badge = tk.Label(
            top, text=f"  PORT {port}  ", font=(FONT_FAMILY, 9, "bold"),
            bg=BADGE_BG, fg=BADGE_FG,
        )
        badge.pack(side=tk.RIGHT)

        # 2行目: ステータス表示
        status_row = tk.Frame(body, bg=CARD_BG)
        status_row.pack(fill=tk.X, pady=(6, 0))

        dot = tk.Label(
            status_row, text="●", font=(FONT_FAMILY, 11),
            bg=CARD_BG, fg=DOT_IDLE,
        )
        dot.pack(side=tk.LEFT)

        status = tk.Label(
            status_row, text="停止中", font=(FONT_FAMILY, 10),
            bg=CARD_BG, fg=MUTED,
        )
        status.pack(side=tk.LEFT, padx=(4, 0))

        # 3行目: 説明
        tk.Label(
            body, text=desc, justify=tk.LEFT, anchor=tk.W,
            font=(FONT_FAMILY, 11), bg=CARD_BG, fg=MUTED,
        ).pack(fill=tk.X, pady=(8, 12))

        # 4行目: 起動ボタン
        btn = RoundedButton(
            body, text="このアプリを起動", parent_bg=CARD_BG,
            bg=accent, hover_bg=_darken(accent),
            command=lambda: self.start_app(key, name, script, url),
        )
        btn.pack(anchor=tk.W)

        self.cards[key] = {"button": btn, "dot": dot, "status": status}

    def _set_card_status(self, key, dot_color, text, text_color=MUTED):
        card = self.cards[key]
        card["dot"].config(fg=dot_color)
        card["status"].config(text=text, fg=text_color)

    def start_app(self, key, name, script, url):
        btn = self.cards[key]["button"]
        port = int(url.rsplit(":", 1)[-1])

        # 既に起動済みなら、ブラウザを開き直すだけ
        existing = self.processes.get(key)
        if existing is not None and existing.poll() is None:
            self.status_label.config(
                text=f"{name} は起動済みです。ブラウザを開きます。", fg="#16a34a")
            webbrowser.open(url)
            return

        try:
            btn.set_state(text="起動中…", enabled=False)
            self._set_card_status(key, DOT_BUSY, "起動中…", DOT_BUSY)
            self.status_label.config(
                text=f"{name} を起動しています…", fg=ACCENT)

            if not script.exists():
                raise FileNotFoundError(f"スクリプトが見つかりません: {script}")

            # 前回の uvicorn が掴んだままならポートを解放してから起動する
            if _port_in_use(port):
                self.status_label.config(
                    text=f"ポート {port} を解放しています…", fg=ACCENT)
                self.root.update_idletasks()
                if not _free_port(port):
                    raise RuntimeError(
                        f"ポート {port} を解放できませんでした。"
                        f"他のプロセスを手動で終了してください。"
                    )

            # 子プロセス(uv → python)もまとめて止められるよう、新しいプロセスグループで起動
            process = subprocess.Popen(
                [str(script)],
                cwd=SCRIPT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            self.processes[key] = process
            self.root.after(
                2500, lambda: self._check_status(key, name, url, process)
            )
        except Exception as e:
            self._set_card_status(key, DOT_ERR, "失敗", DOT_ERR)
            self.status_label.config(text=f"エラー: {e}", fg=DOT_ERR)
            btn.set_state(text="このアプリを起動", enabled=True)
            messagebox.showerror("エラー", f"{name} の起動に失敗しました:\n{e}")

    def _check_status(self, key, name, url, process):
        btn = self.cards[key]["button"]
        if process.poll() is None:
            # 実行中 = 起動成功とみなす
            self._set_card_status(key, DOT_OK, "実行中", DOT_OK)
            self.status_label.config(
                text=f"{name} が起動しました！ブラウザを開きます。", fg="#16a34a")
            btn.set_state(text="ブラウザを開く", enabled=True)
            webbrowser.open(url)
        else:
            stdout, stderr = process.communicate()
            error_msg = (stderr or stdout or "").strip() or "不明なエラー"
            self._set_card_status(key, DOT_ERR, "失敗", DOT_ERR)
            self.status_label.config(
                text=f"{name} の起動に失敗しました", fg=DOT_ERR)
            btn.set_state(text="このアプリを起動", enabled=True)
            messagebox.showerror(
                "起動失敗", f"{name} の起動に失敗しました:\n{error_msg}"
            )

    def _on_close(self):
        running = [
            app for app in APPS
            if self.processes.get(app[0]) is not None
            and self.processes[app[0]].poll() is None
        ]
        if running:
            names = [a[1] for a in running]
            if not messagebox.askokcancel(
                "確認",
                "起動中のサーバーがあります:\n  " + "\n  ".join(names) +
                "\n\nランチャーを閉じるとサーバーも停止します。閉じますか？",
            ):
                return
            for app in running:
                self._stop_process(app[0])
        self.root.destroy()

    def _stop_process(self, key):
        """指定アプリの子プロセスツリーを終了する。"""
        proc = self.processes.get(key)
        if proc is None:
            return
        if proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

    def run(self):
        self.root.mainloop()


def _darken(hex_color, factor=0.82):
    """アクセント色を少し暗くしてホバー色を作る。"""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (max(0, int(c * factor)) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


if __name__ == "__main__":
    removed = _cleanup_old_images()
    if removed:
        print(f"古い生成画像を {removed} 枚削除しました "
              f"(上限 {MAX_KEEP_IMAGES} 枚を維持)")
    AppLauncher().run()
