"""Sign in interactively with jAccount and retain only the Canvas site's session."""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import pywintypes
import win32crypt
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


ORIGIN = "https://oc.sjtu.edu.cn"
HOST = "oc.sjtu.edu.cn"
DATA = Path(os.environ["LOCALAPPDATA"]) / "sjtu-canvas-ddl"
SESSION = DATA / "canvas-session.dpapi"
HEADERS = {"Accept": "application/json"}


class NeedsLogin(Exception):
    pass


class CheckError(Exception):
    pass


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
        except BaseException:
            stream.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def canvas_state(storage):
    return {
        "cookies": [
            cookie for cookie in storage["cookies"]
            if cookie["domain"].lstrip(".").lower() == HOST
        ],
        "origins": [],
    }


def save_session(storage):
    raw = json.dumps(canvas_state(storage)).encode()
    encrypted = win32crypt.CryptProtectData(raw, "SJTU Canvas", None, None, None, 1)
    atomic_write(SESSION, encrypted)


def load_session():
    if not SESSION.exists():
        raise NeedsLogin("请先在本机运行登录脚本，并在交大页面完成登录。")
    encrypted = SESSION.read_bytes()
    try:
        raw = win32crypt.CryptUnprotectData(encrypted, None, None, None, 1)[1]
        return json.loads(raw)
    except (pywintypes.error, ValueError) as exc:
        raise NeedsLogin("当前 Windows 用户无法读取此会话，请重新登录。") from exc


def get_json(context, url):
    if urlsplit(url).netloc != HOST or urlsplit(url).scheme != "https":
        raise CheckError("Canvas 返回了站外 API 链接。")
    response = context.get(url, headers=HEADERS, max_redirects=0, timeout=25000)
    try:
        if response.status == 401 or 300 <= response.status < 400:
            raise NeedsLogin("Canvas 登录已失效，请在本机重新登录。")
        if not response.ok:
            raise CheckError(f"Canvas API 返回 HTTP {response.status}。")
        if "json" not in response.headers.get("content-type", "").lower():
            raise CheckError("Canvas API 返回了非 JSON 页面。")
        body = response.text().lstrip().removeprefix("while(1);")
        try:
            return json.loads(body), response.headers.get("link", "")
        except ValueError as exc:
            raise CheckError("Canvas 返回的 JSON 无法解析。") from exc
    finally:
        response.dispose()


def account_id(context):
    user, _ = get_json(context, ORIGIN + "/api/v1/users/self/profile")
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        raise CheckError("Canvas 未返回有效的当前用户。")
    return user["id"]


def login():
    with sync_playwright() as runner:
        browser = runner.chromium.launch(channel="msedge", headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(ORIGIN + "/login/openid_connect", wait_until="commit")
        print("请在 Edge 中完成交大统一身份认证。", flush=True)
        deadline = time.monotonic() + 600
        last_issue = None
        while time.monotonic() < deadline and browser.is_connected() and context.pages:
            for tab in context.pages:
                if urlsplit(tab.url).netloc != HOST:
                    continue
                state = canvas_state({"cookies": context.cookies(ORIGIN)})
                if not state["cookies"]:
                    continue
                probe = runner.request.new_context(storage_state=state)
                try:
                    account_id(probe)
                    save_session(probe.storage_state())
                except (NeedsLogin, CheckError, PlaywrightError) as exc:
                    issue = str(exc) if isinstance(exc, (NeedsLogin, CheckError)) else "API 连接失败。"
                    if issue != last_issue:
                        print(issue, flush=True)
                        last_issue = issue
                    continue
                finally:
                    probe.dispose()
                print("Canvas 登录已验证。", flush=True)
                return 0
            # A Playwright call dispatches navigation events from the interactive window.
            context.pages[0].wait_for_timeout(2000)
        print("登录窗口已关闭或已超时。")
        return 2


if __name__ == "__main__":
    try:
        sys.exit(login())
    except (PlaywrightError, OSError):
        sys.exit("无法完成登录，请重新运行脚本并检查 Edge 与网络。")
