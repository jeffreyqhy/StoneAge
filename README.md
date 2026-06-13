# StoneAge Script Studio

石器时代专用脚本制作器 MVP。

第一版目标不是复杂 AI，而是把制作脚本的动作放回游戏画面里：

- ADB 实时截图映射
- 录制点击并生成步骤
- 在画面中框选区域并自动保存 raw/crop/annotated
- NPC/目标、按钮框选后默认生成“识别并点击中心点”步骤
- 自动创建图片识别、文字 OCR、数字 OCR 等步骤
- 题库录入：题目区域、四个选项、可选确定按钮，不自动加入流程
- 答题步骤：RapidOCR 识别当前题目文字、查题库、点击正确选项；默认只答 1 题，重复答题用 LoopStep
- 大图框选：弹出原始截图整图窗口，实时显示坐标；可先粗框一块点“放大所选”，再精确框选，结果仍保存为原始 1920x1080 坐标
- 步骤列表拖拽排序、复制、禁用、单步测试、全流程运行
- 制作模式和运行模式分离：运行模式不会持续刷新中间游戏映射画面，运行脚本更轻
- 图片识别默认按“等待出现”处理，找到后再进入下一步；适合判断上一个点击是否完成跳转
- 点击、识别、答题动作自带后置等待；可以用“合并等待到上一步”减少独立 WaitStep
- 游戏坐标区域可设为全局坐标读数，录制点击会记录当时游戏坐标，运行时可先校验坐标再点击，避免偏移误点
- 素材 SQLite 索引、基础素材管理、pending review
- `flow.json` 保存和加载

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

默认 OCR 使用 RapidOCR。其他 OCR 后端是可选能力；如果全部不可用，软件仍会保存裁剪图并进入 `pending_review`，之后可以集中人工确认。

## 启动

macOS 可以直接双击：

```text
StoneAge Script Studio.app
```

这个启动器不会打开 Terminal。备用启动器：

```text
launch_studio.command
```

或者在终端启动：

```bash
source .venv/bin/activate
python -m stoneage_studio
```

## Windows PC 一键包

GitHub Actions 会自动生成 Windows 版：

```text
StoneAge-Script-Studio-Windows.zip
```

PC 上使用：

1. 下载并解压 zip。
2. 双击 `install_and_run.bat`。
3. 它会自动下载 Android platform-tools，创建桌面快捷方式，然后启动软件。
4. 以后直接双击桌面快捷方式，或者运行包里的 `run.bat`。

Windows 包已经包含 Python、PySide6、OpenCV、NumPy、Pillow、RapidOCR 等程序依赖；电脑上不需要自己装 Python。MuMu 模拟器和游戏仍需要提前安装并登录，模拟器 ADB 需要开启。

如果要手动在 Windows 上重新打包：

```powershell
py -3.12 -m venv .venv-build
.\.venv-build\Scripts\Activate.ps1
.\packaging\windows\build.ps1
```

## 材料库网页版 / 外置公开页

桌面工具顶部工具栏有 `网页版材料库` 按钮。点击后会启动本地维护网页，默认地址：

```text
http://127.0.0.1:8765/
```

这个本地维护网页和桌面版共用同一个数据库：

```text
data/stoneage_materials.db
```

所以你可以继续用当前桌面材料库修改、更新资料；网页版刷新后会直接读取最新数据。网页版也支持材料、价格、出处、配方、升级步骤的编辑，以及 JSON 备份、价格 CSV 和 Excel 出处表的导入导出。

也可以从终端单独启动：

```bash
source .venv/bin/activate
python -m stoneage_studio.material_web --open
```

如果要做成给别人看的外置网页，在材料库窗口点击 `导出外置网页`，选择一个导出文件夹。软件会生成：

```text
index.html
styles.css
app.js
material-data.json
```

把整个文件夹上传到 GitHub Pages、Netlify、自己的服务器静态目录或对象存储，即可公开给别人查看。公开页是只读的，不包含本地编辑接口；以后你在桌面材料库更新资料后，再点一次 `导出外置网页` 并上传覆盖这些文件即可。

如果外置网页目录本身是一个已经配置好远端的 Git 仓库，例如 GitHub Pages 仓库，可以在材料库窗口填好 `外置网页` 目录后点击 `同步更新网站`。软件会自动导出最新静态网页，然后执行 Git 提交和推送；如果目录不是 Git 仓库，则只会完成导出并提示未推送。

也可以从终端导出：

```bash
source .venv/bin/activate
python -m stoneage_studio.material_site_export --output data/public_material_site --zip data/public_material_site.zip
```

或导出后自动提交推送 Git 仓库：

```bash
python -m stoneage_studio.material_site_export --output /path/to/your/pages-repo --sync-git
```

## ADB

确保模拟器已开启 ADB，并且命令行可看到设备：

```bash
adb devices
```

MuMu 模拟器默认端口：

```bash
adb connect 127.0.0.1:16384
```

启动软件后会自动连接 MuMu，并在工具栏显示 `MuMu 127.0.0.1:16384：已连接/连接失败`。中间区域会显示实时游戏画面。点击“开始录制”后，在映射画面里的点击会自动生成点击步骤并执行 ADB 点击。

## 答题

点击“添加题库”后按顺序框选：

```text
题目区域 -> 选项 A -> 选项 B -> 选项 C -> 选项 D -> 可选确定按钮
```

软件会弹出大图窗口让你逐个框选，并显示原始截图坐标。保存时选择正确答案，软件只会写入题库。答题步骤默认只答 1 题；如果某个副本真的连续出题，可以在右侧把“需要答对”改大。要让答题步骤判断类似 `0/5` 的进度，选中答题步骤后点“大图框选”，框选进度文字，并选择“答题进度区域 0/5”。

题库只负责补充知识库，不会自动写入流程。需要流程自动答题时，点击“添加答题步骤”，它会套用最近一次题库录入保存的题目/选项/确定按钮区域。运行时遇到题库没有的新题，会弹出题库录入窗口，确认后立即继续答题。

答题默认只按题目文字和四个选项文字匹配题库，不再用题目背景截图相似度猜题。右侧属性里可以手动开启“题目视觉兜底”，但一般不建议打开。

## 循环

如果游戏每答完一题都会回到地图，需要重复“点击 NPC -> 点击答题按钮 -> 答题”：

1. 在左侧步骤列表选中这几步连续步骤。
2. 点击“循环选中”。
3. 输入循环次数，例如 5。

全流程运行时会执行 LoopStep，循环这段步骤指定次数，然后自动跳过循环体，继续后面的步骤。

## 运行模式

制作脚本时使用“制作模式”的流程步骤、截图框选和属性面板。真正跑副本时切换到顶部“运行模式”，它会暂停实时画面映射，只保留脚本列表、步骤列表、运行控制和日志，减少 ADB 截图循环带来的卡顿。

运行模式支持：

```text
加载副本脚本 -> 从头运行
加载副本脚本 -> 选中某一步 -> 从选中运行
设置循环次数 -> 循环运行（0/不设置 = 一直循环）
停止
```

## 等待和图片判断

点击、识别目标、答题步骤都有自己的“动作后等待”。如果流程里已经插入了很多 `wait_001`、`wait_002`，可以点击左侧“合并等待到上一步”，软件会把等待时间合并到前一个动作的属性里，左侧流程会更清楚。

图片识别步骤默认是“等待图片出现”：在超时秒数内轮询截图，识别到了才进入下一步。它适合用来确认“点击按钮后进入战斗”“战斗结束标志出现”“对话框出现”等状态。

## 游戏坐标

先在大图框选右上角类似 `(18,22)` 的游戏坐标文字，选择“游戏坐标区域”。这个区域会被设为全局坐标读数。

之后录制点击时，步骤会同时保存：

```text
屏幕坐标
当时游戏坐标
```

运行时如果勾选“执行前校验游戏坐标”，软件会先读取当前游戏坐标，确认角色仍在录制时的坐标附近，再执行屏幕点击。这样可以先避免因为地图移动偏差导致的误点。完整自动寻路还需要后续补方向校准和 MoveToGameCoord 的点击策略。

## 数据目录

默认项目数据保存在：

```text
data/projects/stoneage/
```

脚本默认保存在：

```text
data/projects/stoneage/scripts/副本_001/flow.json
```

素材会自动保存为 raw、crop、annotated，并写入 SQLite 索引。
