"""启动前端口占用预检（被 start.bat / 启动服务.bat 调用）。

避免「另一个 ComfyBridge 窗口还开着」时，黑窗口里只出现一串
[Errno 10048] 英文报错，用户看不懂为什么服务没起来。

退出码：0 = 端口可用；1 = 已被占用。
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configured_port() -> int:
    """从唯一配置文件读端口；读不到就退回默认值。"""
    try:
        import yaml

        raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        return int(raw["service"]["port"])
    except Exception:  # noqa: BLE001 - 配置坏掉时由应用自己报错，这里只做预检
        return 8190


def main() -> int:
    """检查本机端口能否监听。"""
    port = configured_port()
    probe = socket.socket()
    probe.settimeout(0.5)
    busy = probe.connect_ex(("127.0.0.1", port)) == 0
    probe.close()
    print(f"[ComfyBridge] 端口 {port}：{'已被占用' if busy else '可用'}")
    if busy:
        # 中文说明由 Python 输出：bat 文件里的中文 echo 会被 cmd 按 GBK 解析成乱码命令
        print()
        print("  [X] 服务没有启动：这个端口已经被占用。")
        print("      多半是另一个 ComfyBridge 黑窗口还开着：先关掉那个窗口，再双击本文件重新打开。")
        print("      如果确认没有别的窗口，是其它软件占了端口，请到「管理台 - 服务设置」里改端口后重启。")
        print()
    return 1 if busy else 0


if __name__ == "__main__":
    sys.exit(main())
