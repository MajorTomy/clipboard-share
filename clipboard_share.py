#!/usr/bin/env python3
"""手机 ⇄ 电脑 剪切板共享（局域网，Windows / macOS / Linux）

核心功能只用 Python 标准库；装了 pystray + Pillow 时会显示系统托盘图标。
用法：python clipboard_share.py [--port 端口] [--reset] [--no-tray] [--autostart on|off]
"""
import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:  # 让 Windows 控制台能正常显示中文（pythonw 下 stdout 为 None，会被忽略）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

APP_NAME = "ClipboardShare"
DEFAULT_PORT = 8765
SCRIPT = os.path.abspath(__file__)
COOKIE_MAX_AGE = 400 * 24 * 3600  # 浏览器允许的最长期限；每次打开页面都会续期


# ---------------------------------------------------------------- 配置（配对信息持久化）
def config_dir():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_NAME)


CONFIG_FILE = os.path.join(config_dir(), "config.json")


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    os.makedirs(config_dir(), exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


class State:
    cfg = {}

    @property
    def token(self):
        return self.cfg["token"]

    @property
    def port(self):
        return self.cfg["port"]

    def new_token(self):
        self.cfg["token"] = secrets.token_hex(8)
        save_config(self.cfg)


STATE = State()


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


# ---------------------------------------------------------------- 开机自启动
def _launch_cmd():
    exe = sys.executable
    if sys.platform == "win32":  # 用 pythonw 启动，不弹黑色命令行窗口
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(w):
            exe = w
    return [exe, SCRIPT, "--background"]


if sys.platform == "win32":
    import winreg

    _RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def autostart_enabled():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
                winreg.QueryValueEx(k, APP_NAME)
            return True
        except OSError:
            return False

    def set_autostart(on):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(_launch_cmd()))
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass

elif sys.platform == "darwin":
    import plistlib

    _PLIST = os.path.expanduser("~/Library/LaunchAgents/com.clipboardshare.agent.plist")

    def autostart_enabled():
        return os.path.exists(_PLIST)

    def set_autostart(on):
        if on:
            os.makedirs(os.path.dirname(_PLIST), exist_ok=True)
            with open(_PLIST, "wb") as f:
                plistlib.dump({
                    "Label": "com.clipboardshare.agent",
                    "ProgramArguments": _launch_cmd(),
                    "RunAtLoad": True,
                    "ProcessType": "Interactive",
                }, f)
        elif os.path.exists(_PLIST):
            os.remove(_PLIST)

else:
    _DESKTOP = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                            "autostart", "clipboard-share.desktop")

    def autostart_enabled():
        return os.path.exists(_DESKTOP)

    def set_autostart(on):
        if on:
            def q(a):
                return '"' + a.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"'
            os.makedirs(os.path.dirname(_DESKTOP), exist_ok=True)
            with open(_DESKTOP, "w", encoding="utf-8") as f:
                f.write("[Desktop Entry]\nType=Application\nName=Clipboard Share\n"
                        f"Exec={' '.join(q(a) for a in _launch_cmd())}\nX-GNOME-Autostart-enabled=true\n")
        elif os.path.exists(_DESKTOP):
            os.remove(_DESKTOP)


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


def pairing_url():
    return f"http://{lan_ip()}:{STATE.port}/?k={STATE.token}"


def qr_url():
    return f"http://localhost:{STATE.port}/qr"


PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
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
  .tip{font-size:13px;color:var(--mute);text-align:center}
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
<p class="tip">提示：把本页加入书签或“添加到主屏幕”，以后直接打开即可，无需再扫码。</p>
<script>
  const $=id=>document.getElementById(id), say=t=>{$('msg').textContent=t;setTimeout(()=>$('msg').textContent='',2000)};
  async function load(){const r=await fetch('/api/clip');if(r.status==403)return say('配对已失效，请在电脑上重新扫码');
    $('in').value=await r.text();}
  async function send(){const r=await fetch('/api/clip',{method:'POST',body:$('out').value});
    say(r.ok?'✅ 已复制到电脑剪切板':r.status==403?'配对已失效，请在电脑上重新扫码':'❌ 发送失败');load();}
  async function pasteHere(){try{$('out').value=await navigator.clipboard.readText()}
    catch(e){$('out').focus();say('请长按输入框选择"粘贴"')}}
  async function copy(){const t=$('in');
    try{await navigator.clipboard.writeText(t.value);return say('✅ 已复制')}catch(e){}
    t.removeAttribute('readonly');t.focus();t.setSelectionRange(0,t.value.length);
    const ok=document.execCommand('copy');t.setAttribute('readonly','');t.blur();
    say(ok?'✅ 已复制':'请长按文本手动复制');}
  load();document.addEventListener('visibilitychange',()=>!document.hidden&&load());
</script></body></html>"""


def qr_page():
    url = pairing_url()
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>扫码连接</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<style>body{{font:16px -apple-system,system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;padding:40px;color:#1d1d1f}}
#q{{padding:16px;background:#fff}} p{{color:#6e6e73;max-width:460px;text-align:center}}</style></head><body>
<h2>用手机扫码（手机和电脑需在同一 Wi-Fi）</h2><div id="q"></div><p><code>{url}</code></p>
<p>只需扫一次：配对信息会一直保存。建议在手机上把打开的页面加入书签或“添加到主屏幕”。</p>
<script>new QRCode(document.getElementById('q'),{{text:{json.dumps(url)},width:260,height:260}})</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 不打印每条访问日志
        pass

    def _authed(self):
        q = parse_qs(urlparse(self.path).query)
        if secrets.compare_digest(q.get("k", [""])[0], STATE.token):
            return True
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return "k" in c and secrets.compare_digest(c["k"].value, STATE.token)

    def _send(self, code, body="", ctype="text/plain; charset=utf-8", cookie=False):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", f"k={STATE.token}; Path=/; Max-Age={COOKIE_MAX_AGE}; SameSite=Strict")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/ping":  # 用于检测是否已有实例在运行
            return self._send(200, APP_NAME)
        if path == "/qr":
            if self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
                return self._send(200, qr_page(), "text/html; charset=utf-8")
            return self._send(403, "forbidden")
        if not self._authed():
            return self._send(403, "配对已失效，请在电脑上打开二维码重新扫码")
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


class Server(ThreadingHTTPServer):
    # Windows 上 SO_REUSEADDR 会允许两个进程绑定同一端口，这里关掉以便检测重复启动
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True


def already_running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=1) as r:
            return r.read().decode() == APP_NAME
    except Exception:
        return False


# ---------------------------------------------------------------- 系统托盘
def load_tray():
    try:
        import pystray
        from PIL import Image, ImageDraw
        return pystray, Image, ImageDraw
    except Exception:
        return None


def make_icon(Image, ImageDraw):
    blue, white = (10, 132, 255, 255), (255, 255, 255, 255)
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, 56, 62), radius=8, fill=blue)          # 剪切板
    d.rectangle((16, 18, 48, 54), fill=white)                         # 纸
    d.rounded_rectangle((22, 2, 42, 16), radius=4, fill=blue, outline=white, width=3)  # 夹子
    for y in (26, 34, 42):                                            # 文字行
        d.rectangle((21, y, 43, y + 3), fill=blue)
    return img


def run_tray(tray, server):
    pystray, Image, ImageDraw = tray

    if sys.platform == "darwin":  # 只在菜单栏显示，不在 Dock 里出现图标
        try:
            import AppKit
            AppKit.NSApplication.sharedApplication().setActivationPolicy_(
                AppKit.NSApplicationActivationPolicyAccessory)
        except Exception:
            pass

    def show_qr():
        webbrowser.open(qr_url())

    def toggle_autostart():
        try:
            set_autostart(not autostart_enabled())
        except OSError as e:
            print(f"设置开机自启动失败：{e}")

    def reset_pairing():
        STATE.new_token()
        show_qr()

    def quit_app(icon):
        icon.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem("显示配对二维码", show_qr, default=True),
        pystray.MenuItem("开机自启动", toggle_autostart, checked=lambda item: autostart_enabled()),
        pystray.MenuItem("重新配对（已配对的手机需重新扫码）", reset_pairing),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", quit_app),
    )
    pystray.Icon(APP_NAME, make_icon(Image, ImageDraw), "剪切板共享", menu).run()


# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="手机 ⇄ 电脑 局域网剪切板共享")
    ap.add_argument("--port", type=int, help=f"端口（默认 {DEFAULT_PORT}，设置后会记住）")
    ap.add_argument("--reset", action="store_true", help="重新配对：生成新访问码，已配对的手机需重新扫码")
    ap.add_argument("--no-tray", action="store_true", help="不显示托盘图标，在命令行中运行")
    ap.add_argument("--background", action="store_true", help="后台启动（开机自启动时使用），不打开浏览器")
    ap.add_argument("--autostart", choices=["on", "off"], help="开启 / 关闭开机自启动")
    args = ap.parse_args()

    if args.autostart:
        set_autostart(args.autostart == "on")
        print("已开启开机自启动" if args.autostart == "on" else "已关闭开机自启动")
        return

    cfg = load_config()
    first_run = "token" not in cfg
    if first_run or args.reset:
        cfg["token"] = secrets.token_hex(8)
    if args.port:
        cfg["port"] = args.port
    cfg.setdefault("port", DEFAULT_PORT)
    save_config(cfg)
    STATE.cfg = cfg

    if autostart_enabled():  # 程序文件被移动过时，更新自启动里的路径
        try:
            set_autostart(True)
        except OSError:
            pass

    try:
        server = Server(("0.0.0.0", STATE.port), Handler)
    except OSError:
        if already_running(STATE.port):
            print("剪切板共享已经在运行。")
            if not args.background:
                webbrowser.open(qr_url())
            return
        print(f"端口 {STATE.port} 被其它程序占用，请用 --port 换一个端口。")
        sys.exit(1)

    tray = None if args.no_tray else load_tray()
    print("剪切板共享已启动")
    print(f"  手机打开：{pairing_url()}")
    print(f"  扫码页面：{qr_url()}")
    print(f"  配对信息保存在：{CONFIG_FILE}")
    if tray:
        print("  已显示托盘图标，可在托盘菜单中退出")
    else:
        print("  按 Ctrl+C 退出（安装 pystray 和 Pillow 后可使用托盘图标）")

    if not args.background and (first_run or args.reset or not tray):
        threading.Timer(0.5, webbrowser.open, [qr_url()]).start()

    if tray:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        run_tray(tray, server)
    else:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n已退出")


if __name__ == "__main__":
    main()
