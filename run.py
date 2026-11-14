"""ComfyBridge 启动入口：一个进程、一个端口。

Usage:
    .venv\\Scripts\\python.exe run.py
浏览器打开 http://127.0.0.1:8190
"""

from __future__ import annotations

import uvicorn

from app.config import ConfigError, config


def main() -> None:
    """读取配置后启动单进程服务。"""
    try:
        config.load()
    except ConfigError as exc:
        # 不直接退出：让页面发出来，用顶部红条指出问题并允许在页面上修改配置
        print(f"[ComfyBridge] 配置有问题，页面会提示：{exc}")
    host = "0.0.0.0" if config.service.bindLan else "127.0.0.1"
    print(f"ComfyBridge 启动： http://127.0.0.1:{config.service.port}")
    uvicorn.run("app.main:app", host=host, port=config.service.port, log_level="info")


if __name__ == "__main__":
    main()
