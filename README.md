<div align="center">

<img src="assets/logo/comfybridge-icon-1024.png" width="88" alt="ComfyBridge">

# ComfyBridge

**在本机 ComfyUI 上，用一句中文，生成图片、设定图和短视频**

不用记节点、不用改工作流，画面不出这台电脑。

![version](https://img.shields.io/badge/version-1.08-2f6fed)
![platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D4?logo=windows)
![works with ComfyUI](https://img.shields.io/badge/works%20with-ComfyUI-ff6f00)

[它能做什么](#它能做什么) · [模型与插件](#需要准备哪些模型和插件) · [音频](#音频生成可选) · [开始使用](#开始使用) · [怎么用](#怎么用) · [常见问题](#常见问题)

</div>

## 它能做什么

| 你想做 | 选这个 | 你会得到 |
|---|---|---|
| 一张图 | 图片生成 | 写一句描述就出图；最多再传 3 张参考图，控制风格、构图或人物 |
| 同一个角色的多角度设定 | 角色设定图 | 四视图、四宫格或全身像，彩色稿和线稿都可以 |
| 一个场景的概念图 | 场景设定图 | 普通、全景、鸟瞰、四宫格、九宫格；有参考图还能往外扩 |
| 一件道具的多角度设定 | 道具设定图 | 多视图或单视图，出图后自动去掉重复的那几张 |
| 一段分镜 | 宫格分镜故事板 | 4、6、9 格任选，一格一句描述，留空的格子出纯白 |
| 一段短视频 | 视频生成 | 文生视频、首尾帧、参考生视频、故事板生视频；竖屏横屏都行 |
| 更快地出短片 | 视频生成扩展 | 五档速度与画质，支持首尾帧或参考素材，做 4~15 秒的视频 |

生成好的图片和视频都保存在你自己的电脑上，不会上传到任何云端。

## 需要准备哪些模型和插件

ComfyBridge 本身不带模型，所有生成能力都调用你本机的 ComfyUI。要让图片和视频能力全部可用，需要下面这些：

| 准备项 | 要求 | 用在哪 |
|---|---|---|
| ComfyUI | **0.42.1 或更高** | 全部功能 |
| 图像模型 | Qwen-Image 2.1 Turbo：主模型 + 2 个文本编码器 + 1 个多模态编码器 + VAE，约 34 GB | 图片生成、角色/场景/道具设定图、宫格故事板 |
| 视频模型 | MiniMax H3：fl2va 与 ref2va 两套主模型 + 编码器 + 视频/音频 VAE + 4 个加速 LoRA，约 69 GB | 视频生成、视频生成扩展 |
| 自定义插件 | 6 个，装在 ComfyUI 的 `custom_nodes` 里 | 视频生成扩展 |

模型合计约 **103 GB**，建议预留 130 GB 以上空闲磁盘。

放置位置：

- 模型 → ComfyUI 的 `models` 目录下对应子文件夹（`diffusion_models`、`text_encoders`、`vae`、`loras`）；
- 插件 → ComfyUI 的 `custom_nodes` 目录；6 个插件分别是 ComfyUI-Impact-Pack、ComfyUI-KJNodes、ComfyUI-MieNodes、ComfyUI_Comfyroll_CustomNodes、Derfuu_ComfyUI_ModdedNodes、TE-Speed-MiniMaxH3-OSS；
- 放完重启 ComfyUI，再启动 ComfyBridge。

每个文件的确切名称、大小、插件仓库地址，以及放完后的自查方法，都在 [模型与插件依赖清单](ComfyBridge-Desktop/模型与插件依赖清单.md) 里。

## 音频生成（可选）

除了图片和视频，ComfyBridge 也能在本机做音乐和配音，入口是「音频生成」页：

| 子功能 | 你能做什么 |
|---|---|
| 音乐生成 | 写风格和歌词，直接出一段音乐 |
| 参考生成音乐 | 传一段参考音频，按它的感觉重做一版 |
| 语音生成 | 文字转语音，可选性别、年龄、音高和语速 |
| 声音克隆 | 传一段样音，用这个音色念新台词 |

这部分需要额外准备：

- 音乐模型：MiniMax Music 3 三件套 + YuE2 + SheetSage2，约 18 GB，放到 `models` 下对应的 `diffusion_models`、`text_encoders`、`vae`、`checkpoints`、`audio_encoders`；
- 插件：comfyui-AICG3D（放在 `custom_nodes`）；
- 语音生成与声音克隆还需要 **Spark-TTS-0.5B**，放到 `models/tts/Spark-TTS-0.5B`。当前尚未安装，所以这两个子功能暂时不可用。

补齐后重启 ComfyUI 即可在「音频生成」页使用，细节见 [模型与插件依赖清单](ComfyBridge-Desktop/模型与插件依赖清单.md)。

## 开始使用

### 你需要准备

- 一台 Windows 电脑
- 已经能正常出图的 ComfyUI（0.42.1 及以上，模型与插件见「需要准备哪些模型和插件」）

> ComfyBridge 只负责帮你操作 ComfyUI，不负责安装模型。ComfyUI 能出图，并且装齐前面的模型与插件后，所有能力才完整可用。

### 三步启动

1. 启动 ComfyUI，窗口保持开着。
2. 双击 ComfyBridge 文件夹里的 **`启动服务.bat`**。
3. 浏览器打开 **http://127.0.0.1:8190**。

<details>
<summary><b>第一次使用：先初始化一次运行环境</b>（点开）</summary>

如果双击后窗口一闪而过，说明还差这一步。在 ComfyBridge 文件夹里打开 PowerShell，粘贴两行命令，等它跑完即可：

```powershell
.\runtime\python\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

只需要做一次，以后直接双击 `启动服务.bat`。项目自带运行环境，不用另外安装 Python。

</details>

### 第一次打开，先指一下成品文件夹

点右上角 **管理台 → 服务设置 → 成品存放**，选中 ComfyUI 的 `output` 文件夹，点 **保存并生效**。

以后每次生成完成，文件会自动归到 `output\ComfyBridgeOutput` 下，并按能力分好文件夹。

## 怎么用

### 生成第一张图

1. 左栏点「图片生成」。
2. 在下面的输入框里写描述，例如：`一只白猫坐在窗台上，柔和晨光，浅景深`。
3. 想更接近你想要的效果，可以选风格、尺寸，或上传参考图。
4. 点右下角的发送按钮（也可以按 `Ctrl + Enter`）。

生成时卡片上会显示进度，完成后点开就能看大图。

### 用参考图控制角色或风格

上传参考图后，缩略图右下角有一个 `@`。点它，描述里会插入「[图 1]」：

- 想让参考图里的人出现在画面里，就把它放在描述中对应的位置。
- 宫格分镜里，在某一格写「图 2」，这一格就会用第二张参考图里的角色。

### 结果在哪里

- 对话区会留下每一次的结果卡，可以「再来一张」或「基于上一条」继续改。
- 顶栏「历史」里能按能力筛选，支持批量下载。
- 文件本身在 `ComfyUI\output\ComfyBridgeOutput\能力名\` 下。

### 在手机上用

管理台 → 服务设置，打开 **允许本机以外访问**，重启服务后用手机浏览器访问 `http://电脑的局域网IP:8190`。

手机界面会自动换成窄屏布局；输入区可以收起成一行，不挡画面。

## 常见问题

**双击 `启动服务.bat` 窗口一闪而过**
还没初始化运行环境，按上面「第一次使用」那段做一次即可。

**浏览器打不开 127.0.0.1:8190**
先确认黑色命令行窗口还开着；关了窗口服务就停了。

**顶部提示没连上 ComfyUI**
ComfyUI 没启动，或它的端口不是 8188。先在浏览器打开 `http://127.0.0.1:8188` 确认。

**任务一直显示排队**
同一时间只跑一个任务，前面还有任务在跑，等它结束就好。

**找不到生成的图片**
看「历史」里的结果卡，或直接到 `output\ComfyBridgeOutput` 下按能力翻。

**改了设置或页面没变化**
保存后刷新页面；如果还是旧的，按 `Ctrl + F5` 强制刷新。

---

当前版本 **1.08**。遇到问题时，把任务卡上显示的提示文字发给技术支持，就能快速定位。
