# ComfyBridge 1.01

本机 ComfyUI 转接服务：一个 Python 进程、一个端口，带创作台、管理台四页、历史记录和给斜线技能用的接口。界面与交互按开发原型实现，功能口径按需求文档 1.01 落实。

## 运行（双击启动）

前提：本机 ComfyUI 已启动（默认 `http://127.0.0.1:8188`），且已加载 Qwen Image 2.1 Turbo 的两个工作流所需模型。

**双击 `启动服务.bat`**（或英文名 `start.bat`，两者完全等价）即可：

- 弹出一个黑色控制台窗口，服务日志与任何错误信息都直接打印在窗口里；
- 浏览器打开 <http://127.0.0.1:8190> 使用创作台与管理台；
- **关掉这个黑窗口，服务就停了**；
- 万一启动失败，窗口会停在 `Service stopped. Exit code: ...` 并等待按键，报错不会一闪而过；
- 启动前先做端口预检：端口被另一个 ComfyBridge 窗口占用时，窗口里直接给中文说明，不会只甩一串英文 `[Errno 10048]`。

命令行方式（等价）：

```powershell
cd D:\workspace\WorkBuddyProject\ComfyBridge-Desktop
.\.venv\Scripts\python.exe run.py
```

允许手机/局域网访问：管理台 → 服务设置 → 打开「允许本机以外访问」，保存后重启应用（监听地址改动需要重启）。

依赖已经装在应用文件夹内的 `.venv`；换机器时：

```powershell
cd D:\workspace\WorkBuddyProject\ComfyBridge-Desktop
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-cache-dir -r requirements.txt
```

## 两个真实工作流

| 母版 | 源文件（ComfyUI 里保存的） | 应用内工作流 |
| --- | --- | --- |
| 文生图 | `[图][文生图]image_qwen_image_2_1_turbo_t2i.json` | `workflows/图片生成-文生图-qwen2.1turbo.json` |
| 参考生图 | `[图][编辑]image_qwen_image_2_1_turbo_image_edit.json` | `workflows/图片生成-参考生图-qwen2.1turbo.json` |

ComfyUI 0.42 保存的是 subgraph（子图）UI 格式，`/prompt` 只吃扁平 API 格式，所以 `tools/convert_workflows.py` 在开发阶段把子图展开一次，产物是上面两个可直接提交的 JSON；`.bridge.json` 记录描述框/种子/参考图/出片节点的注入点，`schema.json` 记录参数控件类型。运行时应用只读这些文件，不拉取 ComfyUI 的节点定义，也不写回原始工作流。

配置里只挂两个工作流 tab（文生图 / 参考生图），分别对接两个真实工作流文件；按上传参考图数量自动挑工作流（`图片 = 0` → 文生图，`图片 ≥ 1` → 参考生图）。原型里复用同一文件的壳（图生图.sdxl / 风格参考.ipadapter）和重复的 turbo 壳已精简删除。

## 参数生效与删除

- **参数一定生效**：每次生成都按当前配置重建档位工作流拷贝（页面改、手改 `config.yaml`、点「重读配置」都生效），并解决「宽高比」与「固定宽高」互相覆盖导致出图仍是 1:1 的冲突。档位表每档显示实际出图尺寸，展开后逐项列出「配置值 → 实际提交值」并标记是否全部生效；创作台档位按钮也带尺寸说明，每条任务记录实际使用的参数。
- **历史记录**：新增「全选」与「删除」（也可单张删）；删除会二次确认，然后**同时删掉本地对应的成品文件**，并从主页面移除这几张图。
- **会话任务**：右栏「本会话任务」和主页面对话卡片都能删除这条对话记录，**不删成品文件**；成品仍留在历史记录里，只有历史页的删除才真正删文件。

## 目录

```
启动服务.bat / start.bat  双击启动（黑窗口，关窗即停）
run.py                  命令行启动入口
config.yaml             单配置文件（唯一当前版本）
app/                     Python 服务端（config / state / workflows / comfy / services / main）
web/                     页面（原型 CSS + app.js）
workflows/               API 格式工作流、bridge、schema、档位拷贝、源工作流备份
skills/                  斜线技能文本
data/                    上传文件、任务记录、成品索引（不落数据库）
tools/                   开发期脚本：文档解析、工作流转换、启动端口预检、冒烟测试
tests/                   自检脚本
```

## 落盘边界

- 上传文件、任务记录、档位拷贝、配置、venv、缓存与临时目录都写在 `D:\workspace\WorkBuddyProject\ComfyBridge-Desktop` 内；启动脚本把 `TEMP`/`TMP` 也指到文件夹里的 `.cache\tmp`，不往 C 盘写东西。
- 整个应用只占 `ComfyBridge-Desktop` 这一个文件夹，可以整体拷走；上级目录里只剩需求方预置的参考材料，不属于应用。
- 生成成功后，只把本次生成的文件从 ComfyUI 的 `output/` 移动到 `<成果目录>/<能力名>/`；成果目录默认在 output 内，也可在服务设置里指定任意有写权限的目录；不会动目录里原有的任何文件。
- 原始工作流只读；档位是一份份独立拷贝。

## 自检

```powershell
cd D:\workspace\WorkBuddyProject\ComfyBridge-Desktop
.\.venv\Scripts\python.exe tests\smoke_test.py
```

会检查配置/工作流/接口，并向本机 ComfyUI 真实提交一次文生图任务；需要 ComfyUI 在线、应用已启动。

逐功能点的真实浏览器点击测试（不需要额外安装浏览器，复用本机 Edge + venv 里的 websocket-client）：

```powershell
.\.venv\Scripts\python.exe tests\ui_e2e_test.py
```

覆盖创作台、管理台四页、历史、对外接口与配置损坏红条共 83 项；`tests/recovery_test.py` 单独验证重启恢复。

新增 `tests/features_e2e_test.py` 覆盖「配置里添加/保存/删除全部按钮、所有档位参数逐项生效（含真实 16:9 出图）、历史全选与删文件、会话任务删除联动」共 57 项；测试只删自己刚生成的图，开始备份并结束恢复 `config.yaml`。逐条需求核对与遗漏补齐记录见 `需求核对清单.md`。
