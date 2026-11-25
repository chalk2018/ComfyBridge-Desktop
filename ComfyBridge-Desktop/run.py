"""ComfyBridge 启动入口：一个进程、一个对外端口，HTTP / HTTPS 共用一个地址。

Usage:
    .venv\\Scripts\\python.exe run.py
浏览器打开 http://127.0.0.1:8190 或 https://127.0.0.1:8190（同一个端口）
手机「安装应用」用 https://<本机IP>:8190（需要先信任 certs/rootCA.pem）
"""

from __future__ import annotations

import asyncio
import ssl
import threading
from pathlib import Path

import uvicorn

from app.config import ConfigError, config

# 本地 HTTPS 证书（用 mkcert 生成）：存在时对外端口同时支持 http:// 与 https://。
# 生成方式：在项目根目录执行
#   mkcert -cert-file certs/cert.pem -key-file certs/cert-key.pem <本机IP> localhost 127.0.0.1
_CERT = Path(__file__).parent / "certs" / "cert.pem"
_KEY = Path(__file__).parent / "certs" / "cert-key.pem"
# 真正的 uvicorn 只监听本机内部端口，对外端口由下面的事件分流器占用
_INTERNAL_OFFSET = 2
_TLS_HANDSHAKE = 0x16  # TLS 记录类型 handshake，明文 HTTP 不会以这个字节开头


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


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """单向搬运字节，任何一端断开就一起收摊。"""
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def _flush(outbio: ssl.MemoryBIO, writer: asyncio.StreamWriter) -> None:
    """把 SSL 层待发的加密数据写回客户端。"""
    while True:
        data = outbio.read()
        if not data:
            break
        writer.write(data)
    await writer.drain()


async def _tls_client_to_upstream(obj: ssl.SSLObject, inbio: ssl.MemoryBIO,
                                  outbio: ssl.MemoryBIO, reader: asyncio.StreamReader,
                                  writer: asyncio.StreamWriter,
                                  up_writer: asyncio.StreamWriter) -> None:
    """客户端 → 上游：SSL 解密后再转发明文。"""
    while True:
        try:
            data = obj.read(65536)
        except ssl.SSLWantReadError:
            await _flush(outbio, writer)
            raw = await reader.read(65536)
            if not raw:
                return
            inbio.write(raw)
            continue
        except (ssl.SSLError, OSError):
            return
        if not data:  # 客户端正常关闭
            return
        up_writer.write(data)
        await up_writer.drain()


async def _upstream_to_tls_client(up_reader: asyncio.StreamReader, obj: ssl.SSLObject,
                                  outbio: ssl.MemoryBIO,
                                  writer: asyncio.StreamWriter) -> None:
    """上游 → 客户端：明文加密后再发出去。"""
    while True:
        data = await up_reader.read(65536)
        if not data:
            try:
                obj.unwrap()
            except Exception:  # noqa: BLE001
                pass
            try:
                await _flush(outbio, writer)
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            obj.write(data)
        except (ssl.SSLError, OSError):
            return
        await _flush(outbio, writer)


async def _serve_tls(first: bytes, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                     up_port: int, ctx: ssl.SSLContext) -> None:
    """同端口上的 HTTPS 连接：手工完成 TLS 握手，然后像普通代理一样转发。"""
    inbio, outbio = ssl.MemoryBIO(), ssl.MemoryBIO()
    obj = ctx.wrap_bio(inbio, outbio, server_side=True)
    inbio.write(first)
    while True:
        try:
            obj.do_handshake()
            break
        except ssl.SSLWantReadError:
            await _flush(outbio, writer)
            raw = await reader.read(65536)
            if not raw:
                writer.close()
                return
            inbio.write(raw)
        except (ssl.SSLError, OSError):
            writer.close()
            return
    await _flush(outbio, writer)
    try:
        up_reader, up_writer = await asyncio.open_connection("127.0.0.1", up_port)
    except Exception:  # noqa: BLE001
        writer.close()
        return

    async def c2u() -> None:
        try:
            await _tls_client_to_upstream(obj, inbio, outbio, reader, writer, up_writer)
        finally:
            try:
                up_writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def u2c() -> None:
        try:
            await _upstream_to_tls_client(up_reader, obj, outbio, writer)
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    await asyncio.gather(c2u(), u2c())


async def _serve_plain(first: bytes, reader: asyncio.StreamReader,
                       writer: asyncio.StreamWriter, up_port: int) -> None:
    """同端口上的 HTTP 连接：原样透传。"""
    try:
        up_reader, up_writer = await asyncio.open_connection("127.0.0.1", up_port)
    except Exception:  # noqa: BLE001
        writer.close()
        return
    up_writer.write(first)
    await up_writer.drain()
    await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))


async def _dispatch(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                    up_port: int, ctx: ssl.SSLContext) -> None:
    """按连接首字节判断协议，再走对应分支。"""
    try:
        first = await reader.readexactly(1)
    except Exception:  # noqa: BLE001
        writer.close()
        return
    try:
        if first[0] == _TLS_HANDSHAKE:
            await _serve_tls(first, reader, writer, up_port, ctx)
        else:
            await _serve_plain(first, reader, writer, up_port)
    except Exception:  # noqa: BLE001
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


def _start_dual_listener(host: str, port: int, up_port: int, cert: Path, key: Path) -> None:
    """对外端口：同时接受 http:// 与 https://（看首字节分流）。

    为什么不直接再跑一个 uvicorn：应用的 lifespan 会启动 ComfyUI 连接与素材索引补建，
    跑两遍等于重复初始化；所以业务只由内部 uvicorn 持有，对外端口纯做协议分流。
    """

    def run() -> None:
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        ctx.set_alpn_protocols(["http/1.1"])

        async def serve() -> None:
            server = await asyncio.start_server(
                lambda r, w: _dispatch(r, w, up_port, ctx), host, port, backlog=256)
            print(f"ComfyBridge 监听 {host}:{port}（http:// 与 https:// 同一个端口）")
            async with server:
                await server.serve_forever()

        try:
            loop.run_until_complete(serve())
        except Exception as exc:  # noqa: BLE001
            print(f"[ComfyBridge] 对外端口启动失败：{exc}")

    threading.Thread(target=run, name="dual-listener", daemon=True).start()


def main() -> None:
    """读取配置后启动单进程服务。"""
    try:
        config.load()
    except ConfigError as exc:
        # 不直接退出：让页面发出来，用顶部红条指出问题并允许在页面上修改配置
        print(f"[ComfyBridge] 配置有问题，页面会提示：{exc}")
    host = "0.0.0.0" if config.service.bindLan else "127.0.0.1"
    port = config.service.port
    use_tls = _CERT.exists() and _KEY.exists()
    if use_tls:
        up_port = port + _INTERNAL_OFFSET
        print(f"ComfyBridge 启动： http:// 或 https:// <本机IP>:{port}")
        _start_dual_listener(host, port, up_port, _CERT, _KEY)
        listen_host, listen_port = "127.0.0.1", up_port
    else:
        print(f"ComfyBridge 启动： http://127.0.0.1:{port}")
        listen_host, listen_port = host, port
    uv_config = _SelectorConfig("app.main:app", host=listen_host, port=listen_port,
                                log_level="info", loop="asyncio")
    uvicorn.Server(uv_config).run()


if __name__ == "__main__":
    main()
