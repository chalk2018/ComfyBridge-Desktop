# WorkBuddyProject

工作区仓库，包含 ComfyBridge-Desktop 项目本体与各版本需求/功能文档。

## 目录结构

| 路径 | 内容 |
| --- | --- |
| `ComfyBridge-Desktop/` | 应用本体（启动方式见 [ComfyBridge-Desktop/README.md](ComfyBridge-Desktop/README.md)） |
| `ComfyBridge-需求与功能文档-1.08.html` | 当前版本需求与功能文档 |
| `ComfyBridge-需求与功能文档-1.0x.html` | 历史版本文档 |
| `ComfyBridge-交互原型-单文件.html` | 交互原型 |
| `ComfyBridge-差异确认结论.md` | 差异确认结论 |

## 快速开始

```powershell
cd ComfyBridge-Desktop
.\runtime\python\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

然后双击 `启动服务.bat`，浏览器打开 <http://127.0.0.1:8190>。

完整说明见 [ComfyBridge-Desktop/README.md](ComfyBridge-Desktop/README.md)。
