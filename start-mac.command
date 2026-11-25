#!/bin/bash
cd "$(dirname "$0")"
echo "正在安装托盘图标支持（pystray、Pillow）…"
# macOS 自带 Python 的 pip 太旧，装不上 pystray 依赖的 pyobjc，先升级 pip
python3 -m pip install --user -q --disable-pip-version-check --upgrade pip
python3 -m pip install --user -q --disable-pip-version-check -r requirements.txt
if python3 -c "import pystray, PIL" 2>/dev/null; then
  nohup python3 clipboard_share.py >/dev/null 2>&1 &
  echo "已在后台启动，请在屏幕顶部菜单栏找到剪切板图标。这个窗口可以关闭了。"
else
  echo "托盘图标不可用，在当前窗口运行，关闭窗口即退出。"
  python3 clipboard_share.py
fi
