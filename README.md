# 剪切板共享 Clipboard Share

在局域网内让手机和电脑共用剪切板。电脑端运行一个 Python 小程序，手机用浏览器扫码打开网页即可，**手机无需安装任何 App**。

- 支持 Windows / macOS / Linux
- 只用 Python 标准库，无需 `pip install`
- 支持中文等任意文字（暂不支持图片、文件）

## 使用方法

1. 电脑上安装 Python 3.8 或更新版本（Windows 可从 [python.org](https://www.python.org/downloads/) 下载，安装时勾选 “Add Python to PATH”）。
2. 启动：
   - **Windows**：双击 `start-windows.bat`，或在命令行运行 `python clipboard_share.py`
   - **macOS**：双击 `start-mac.command`，或在终端运行 `python3 clipboard_share.py`
3. 电脑浏览器会自动打开一个二维码页面，手机连接**同一个 Wi-Fi** 后扫码。
4. 手机网页上：
   - **📱 → 💻 发送到电脑**：粘贴或输入文字，点“发送到电脑”，电脑上直接 Ctrl+V / ⌘V
   - **💻 → 📱 电脑剪切板**：显示电脑当前剪切板内容，点“复制到手机”

可指定端口：`python clipboard_share.py 9000`（默认 8765）。

## 注意事项

- **Windows 防火墙**：第一次运行会弹窗询问，请勾选“专用网络”并允许，否则手机连不上。
- **安全**：每次启动生成一个随机访问码，包含在二维码链接里；没有访问码的请求会被拒绝。扫过码的手机 30 天内无需重新扫码（同一次运行期间）。只在可信的局域网中使用。
- **手机复制/粘贴按钮**：网页是普通 http，部分手机浏览器不允许网页直接读写剪切板。此时复制会自动选中文字，长按即可复制；粘贴请长按输入框选择“粘贴”。
- **Linux**：需要安装 `xclip`（X11）或 `wl-clipboard`（Wayland）。
