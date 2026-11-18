"""ComfyBridge 启动入口：一个进程、一个端口。

Usage:
    .venv\\Scripts\\python.exe run.py
浏览器打开 http://127.0.0.1:8190
"""

from __future__ import annotations

import asyncio

import uvicorn

from app.config import ConfigError, config


class _SelectorConfig(uvicorn.Config):
    """强制用 selector 事件循环的 uvicorn 配置。

    为什么：uvicorn 0.36+ 在 Windows 上固定用 ProactorEventLoop（见
    uvicorn/loops/asyncio.py），而它在浏览器一次拉几百张缩略图、连接被中途掐断时，
    accept 会抛 WinError 64（指定的网络名不再可用）。这个异常会把接受连接的协程整个
    带走——进程还在，端口却不再监听，页面直接「连不上」，只能重启服务。
    selector 循环不会这样；本服务全是同步 I/O，用不到 proactor 的管道/子进程能力。
    """

    def get_loop_factory(self):
        return asyncio.SelectorEventLoop


def main() -> None:
    """读取配置后启动单进程服务。"""
    try:
        config.load()
    except ConfigError as exc:
        # 不直接退出：让页面发出来，用顶部红条指出问题并允许在页面上修改配置
        print(f"[ComfyBridge] 配置有问题，页面会提示：{exc}")
    host = "0.0.0.0" if config.service.bindLan else "127.0.0.1"
    print(f"ComfyBridge 启动： http://127.0.0.1:{config.service.port}")
    uv_config = _SelectorConfig("app.main:app", host=host, port=config.service.port,
                                log_level="info", loop="asyncio")
    uvicorn.Server(uv_config).run()


if __name__ == "__main__":
    main()
