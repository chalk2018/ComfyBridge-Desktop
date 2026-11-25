"""开发用：在 8192 起一份隔离的 ComfyBridge，专供 UI 检查。

为什么不直接用 run.py：本地 8190 可能被另一个会话（宫格分镜故事板）占着，
这个脚本只改端口、不跑任何生成任务，避免互相干扰。
"""

from __future__ import annotations

import uvicorn

from app.config import config
from run import _SelectorConfig

PORT = 8192

if __name__ == "__main__":
    config.load()
    config.service.port = PORT
    uv = _SelectorConfig("app.main:app", host="127.0.0.1", port=PORT,
                         log_level="warning", loop="asyncio")
    uvicorn.Server(uv).run()
