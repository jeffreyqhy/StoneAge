# StoneAge Script Studio Windows 版

## 第一次使用

1. 解压 `StoneAge-Script-Studio-Windows.zip`。
2. 双击 `install_and_run.bat`。
3. 脚本会自动下载 Android platform-tools，并创建桌面快捷方式。
4. 以后可以双击桌面快捷方式，或者双击 `run.bat`。

这个包已经包含 Python、PySide6、OpenCV、RapidOCR 等桌面程序依赖。电脑上不需要单独安装 Python。

## 还需要你自己准备

- MuMu 模拟器或其它能打开 ADB 的安卓模拟器。
- 游戏已经安装并登录。
- 模拟器开启 ADB 调试。MuMu 默认端口通常是 `127.0.0.1:16384`。

## 如果 Windows 拦截

第一次运行未签名程序时，Windows 可能显示安全提醒。点击“更多信息”后选择“仍要运行”。

## 数据位置

脚本、素材和数据库默认会生成在程序目录下的 `data/`。更新新版程序前，先备份这个目录。
