"""
디스코드 자동 알림 (공유 이미지 + 글, 사이트 링크 없음)

하는 일
  1. docs 폴더를 잠깐 내 컴퓨터 안 웹사이트로 띄운다.
  2. 브라우저(크롬)로 사이트를 몰래 열어서, '공유 이미지 만들기'와 똑같은 그림을 PNG로 받는다.
     - 평일: 오늘 기록 이미지 1장
     - 금요일 기록: 오늘 기록 + 주간 리포트 2장
  3. 글과 사진을 디스코드 웹후크로 보낸다.
  사진을 못 만들면 글만이라도 보낸다.

GitHub Actions 에서 aggregate.py 로 기록을 저장한 다음에 실행된다.
"""

import base64
import functools
import http.server
import json
import os
import shutil
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aggregate import SITE_DATA_FILE, send_discord  # noqa: E402

DOCS = Path(__file__).resolve().parent.parent / "docs"


def find_chrome():
    """GitHub 컴퓨터(ubuntu)에 미리 깔려 있는 크롬을 찾는다. 없으면 None (Playwright 기본 브라우저 사용)"""
    for path in (os.environ.get("CHROME_PATH"), "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
                 shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser")):
        if path and Path(path).exists():
            return path
    return None


def render_images(site):
    """[(파일이름, PNG 바이트, 제목)] 을 돌려준다. 실패하면 빈 목록."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("⚠️  playwright 가 없어서 사진 없이 글만 보낼게요.")
        return []

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):   # 실행 기록이 지저분해지지 않게
            pass
    handler = functools.partial(Quiet, directory=str(DOCS))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/index.html"

    addr = (site.get("site_url") or "").replace("https://", "").rstrip("/")
    wk = site["groups"][0].get("weekly") if site.get("groups") else None
    wanted = [("day", "share-today.png", "📊 오늘 기록")]
    if wk and wk.get("complete"):   # 금요일 기록이면 주간 리포트도 같이
        wanted.append(("week", "share-week.png", "📅 주간 리포트"))

    images = []
    try:
        with sync_playwright() as p:
            chrome = find_chrome()
            browser = p.chromium.launch(executable_path=chrome) if chrome else p.chromium.launch()
            page = browser.new_page(viewport={"width": 1000, "height": 900}, reduced_motion="reduce")
            page.goto(base, wait_until="networkidle")
            for mode, fname, title in wanted:
                data_url = page.evaluate("([m, a]) => window.__renderShareImage(m, a)", [mode, addr])
                if data_url:
                    images.append((fname, base64.b64decode(data_url.split(",", 1)[1]), title))
                    print(f"🖼️  {title} 이미지를 만들었어요.")
            browser.close()
    except Exception as e:
        print(f"⚠️  공유 이미지를 만들지 못했어요 (글만 보낼게요): {e}")
        images = []
    finally:
        server.shutdown()
    return images


def main():
    if not os.environ.get("DISCORD_WEBHOOK_URL", "").strip():
        print("ℹ️  디스코드 웹후크 주소가 없어서 알림은 건너뛰었어요.")
        return
    if not SITE_DATA_FILE.exists():
        print("ℹ️  사이트 데이터가 없어서 알림을 건너뛰었어요.")
        return
    site = json.loads(SITE_DATA_FILE.read_text(encoding="utf-8"))
    send_discord(site, render_images(site))


if __name__ == "__main__":
    main()
