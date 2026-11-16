#!/usr/bin/env python3
"""手机 ⇄ 电脑 剪切板共享（局域网，Windows / macOS / Linux）

只用 Python 标准库，无需安装任何依赖。
用法：python clipboard_share.py [端口]
"""
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:  # 让 Windows 控制台能正常显示中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
TOKEN = secrets.token_hex(8)


# ---------------------------------------------------------------- 剪切板读写
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _u32.OpenClipboard.argtypes = [wintypes.HWND]
    _u32.OpenClipboard.restype = wintypes.BOOL
    _u32.CloseClipboard.restype = wintypes.BOOL
    _u32.EmptyClipboard.restype = wintypes.BOOL
    _u32.GetClipboardData.argtypes = [wintypes.UINT]
    _u32.GetClipboardData.restype = wintypes.HANDLE
    _u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    _u32.SetClipboardData.restype = wintypes.HANDLE
    _k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    _k32.GlobalAlloc.restype = wintypes.HGLOBAL
    _k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    _k32.GlobalLock.restype = ctypes.c_void_p
    _k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    _k32.GlobalUnlock.restype = wintypes.BOOL
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002

    def _open_clipboard():
        for _ in range(20):  # 剪切板可能被其它程序短暂占用
            if _u32.OpenClipboard(None):
                return
            time.sleep(0.05)
        raise OSError("无法打开剪切板")

    def clip_get():
        _open_clipboard()
        try:
            h = _u32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return ""
            p = _k32.GlobalLock(h)
            try:
                return ctypes.wstring_at(p)
            finally:
                _k32.GlobalUnlock(h)
        finally:
            _u32.CloseClipboard()

    def clip_set(text):
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
        data = text.encode("utf-16-le") + b"\x00\x00"
        h = _k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        p = _k32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        _k32.GlobalUnlock(h)
        _open_clipboard()
        try:
            _u32.EmptyClipboard()
            _u32.SetClipboardData(CF_UNICODETEXT, h)
        finally:
            _u32.CloseClipboard()

else:
    _ENV = dict(os.environ, LANG="en_US.UTF-8")
    if sys.platform == "darwin":
        _GET, _SET = ["pbpaste"], ["pbcopy"]
    elif os.environ.get("WAYLAND_DISPLAY"):
        _GET, _SET = ["wl-paste", "--no-newline"], ["wl-copy"]
    else:
        _GET, _SET = ["xclip", "-selection", "clipboard", "-o"], ["xclip", "-selection", "clipboard"]

    def clip_get():
        r = subprocess.run(_GET, capture_output=True, env=_ENV)
        return r.stdout.decode("utf-8", errors="replace")

    def clip_set(text):
        subprocess.run(_SET, input=text.encode("utf-8"), env=_ENV, check=True)


# ---------------------------------------------------------------- 网页
def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # 不会真正发包，只用来找出本机局域网 IP
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


URL = f"http://{lan_ip()}:{PORT}/?k={TOKEN}"

PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>剪切板共享</title>
<style>
  :root{--bg:#f5f5f7;--card:#fff;--fg:#1d1d1f;--mute:#6e6e73;--acc:#0a84ff}
  @media (prefers-color-scheme:dark){:root{--bg:#000;--card:#1c1c1e;--fg:#f5f5f7;--mute:#98989d}}
  body{margin:0;background:var(--bg);color:var(--fg);font:16px -apple-system,system-ui,sans-serif;padding:16px}
  .card{background:var(--card);border-radius:14px;padding:14px;margin-bottom:16px}
  h2{font-size:15px;margin:0 0 8px;color:var(--mute);font-weight:600}
  textarea{width:100%;box-sizing:border-box;min-height:120px;font:inherit;padding:10px;border-radius:10px;
    border:1px solid #8884;background:transparent;color:inherit;resize:vertical}
  .row{display:flex;gap:8px;margin-top:10px}
  button{flex:1;font:inherit;font-weight:600;padding:12px;border:0;border-radius:10px;background:var(--acc);color:#fff}
  button.sec{background:#8883;color:var(--fg)}
  #msg{text-align:center;color:var(--mute);min-height:1.4em}
</style></head><body>
<div class="card"><h2>📱 → 💻 发送到电脑</h2>
  <textarea id="out" placeholder="在这里粘贴或输入…"></textarea>
  <div class="row"><button class="sec" onclick="pasteHere()">从手机粘贴</button>
  <button onclick="send()">发送到电脑</button></div></div>
<div class="card"><h2>💻 → 📱 电脑剪切板</h2>
  <textarea id="in" readonly></textarea>
  <div class="row"><button class="sec" onclick="load()">刷新</button>
  <button onclick="copy()">复制到手机</button></div></div>
<div id="msg"></div>
<script>
  const $=id=>document.getElementById(id), say=t=>{$('msg').textContent=t;setTimeout(()=>$('msg').textContent='',2000)};
  async function load(){const r=await fetch('/api/clip');$('in').value=await r.text();}
  async function send(){const r=await fetch('/api/clip',{method:'POST',body:$('out').value});
    say(r.ok?'✅ 已复制到电脑剪切板':'❌ 发送失败');load();}
  async function pasteHere(){try{$('out').value=await navigator.clipboard.readText()}
    catch(e){$('out').focus();say('请长按输入框选择"粘贴"')}}
  async function copy(){const t=$('in');
    try{await navigator.clipboard.writeText(t.value);return say('✅ 已复制')}catch(e){}
    t.removeAttribute('readonly');t.focus();t.setSelectionRange(0,t.value.length);
    const ok=document.execCommand('copy');t.setAttribute('readonly','');t.blur();
    say(ok?'✅ 已复制':'请长按文本手动复制');}
  load();document.addEventListener('visibilitychange',()=>!document.hidden&&load());
</script></body></html>"""

QR_PAGE = f"""<!doctype html><html><head><meta charset="utf-8"><title>扫码连接</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<style>body{{font:16px -apple-system,system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;padding:40px}}
#q{{padding:16px;background:#fff}}</style></head><body>
<h2>用手机扫码（手机和电脑需在同一 Wi-Fi）</h2><div id="q"></div><p><code>{URL}</code></p>
<script>new QRCode(document.getElementById('q'),{{text:{json.dumps(URL)},width:260,height:260}})</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 不打印每条访问日志
        pass

    def _authed(self):
        q = parse_qs(urlparse(self.path).query)
        if q.get("k", [""])[0] == TOKEN:
            return True
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return "k" in c and c["k"].value == TOKEN

    def _send(self, code, body="", ctype="text/plain; charset=utf-8", cookie=False):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", f"k={TOKEN}; Path=/; Max-Age=2592000; SameSite=Strict")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/qr":
            if self.client_address[0] in ("127.0.0.1", "::1"):
                return self._send(200, QR_PAGE, "text/html; charset=utf-8")
            return self._send(403, "forbidden")
        if not self._authed():
            return self._send(403, "无效链接，请重新扫码")
        if path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8", cookie=True)
        if path == "/api/clip":
            return self._send(200, clip_get())
        self._send(404, "not found")

    def do_POST(self):
        if urlparse(self.path).path != "/api/clip" or not self._authed():
            return self._send(403, "forbidden")
        n = int(self.headers.get("Content-Length", 0))
        text = self.rfile.read(n).decode("utf-8", errors="replace")
        clip_set(text)
        print(f"[收到] 手机发来 {len(text)} 个字")
        self._send(200, "ok")


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("剪切板共享已启动")
    print(f"  手机打开：{URL}")
    print(f"  或在电脑浏览器打开 http://localhost:{PORT}/qr 扫码")
    print("  按 Ctrl+C 退出")
    if not os.environ.get("NO_OPEN"):
        threading.Timer(0.5, webbrowser.open, [f"http://localhost:{PORT}/qr"]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
