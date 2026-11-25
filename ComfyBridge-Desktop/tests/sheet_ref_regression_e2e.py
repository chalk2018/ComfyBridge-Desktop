"""参考图缓存上限 + 重启后自愈 · 真实页面操作回归。

全程只通过页面控件操作：切「图生图」→ 点「＋ 上传」→ CDP 给页面内的 file input
注入文件（后续走页面自己的 uploadFiles / /api/uploads）→ 点参考图 chip 的「移除」。
不直接调用任何后端生成接口，也不执行业务生成代码。

复现口径：页面把参考图从输入框摘掉时不删服务端副本，所以连续 30 多次上传会在
服务端攒下 30+ 个内存登记。修复前第 31 次会被 400 挡住（prune_refs 因所有登记
都被历史任务引用而清不掉任何一张）；修复后 32 次都应成功，并且随后旧任务仍能
通过「加入队列生成 / 再来一张」自动把参考图找回来。

结果：tests/_sheet_full_out/ref_regression.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sheet_full_e2e as e2e  # noqa: E402

OUT = e2e.OUT / "ref_regression.json"
REF = str(e2e.APP / "data" / "uploads" / "ref_1849.png")
ROUNDS = 32


def main() -> None:
    proc, cdp = e2e.start_browser()
    rows: list[dict] = []
    try:
        if not e2e.ensure_sheet(cdp):
            raise RuntimeError("没能进入角色设定图页面")
        e2e.click(cdp, '[data-mode="i2i"]')
        cdp.pause(500)
        e2e.log(f"--- REF-REGRESSION START 共 {ROUNDS} 次连续上传/移除")
        for i in range(1, ROUNDS + 1):
            before = cdp.js("UI.refs.length")
            ok, info = e2e.upload_ref(cdp, REF)
            after = cdp.js("UI.refs.length")
            toast = cdp.js("([...document.querySelectorAll('.toast')].map(x=>x.textContent).join(' | '))||''")
            rows.append({
                "round": i, "upload_ok": bool(ok), "info": info,
                "refs_before": before, "refs_after": after,
                "toast": str(toast or "")[:200],
            })
            e2e.log(f"--- REF-UPLOAD {i}/{ROUNDS} ok={bool(ok)} {info} toast={str(toast or '')[:80]}")
            # 通过页面自己的「移除」按钮摘掉输入框里的图（服务端副本要留着）
            if after > 0:
                e2e.click(cdp, "[data-rmref]")
                cdp.pause(160)
        all_ok = all(r["upload_ok"] for r in rows)
        summary = {
            "rounds": ROUNDS,
            "upload_ok_count": sum(1 for r in rows if r["upload_ok"]),
            "all_ok": all_ok,
            "first_fail_round": next((r["round"] for r in rows if not r["upload_ok"]), None),
            "rounds_after_cap": [r["round"] for r in rows if r["round"] > 30 and r["upload_ok"]],
            "rows": rows,
        }
        OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        e2e.log(f"--- REF-REGRESSION DONE all_ok={all_ok} ok={summary['upload_ok_count']}/{ROUNDS} -> {OUT.name}")
    finally:
        cdp.close()
        proc.terminate()


if __name__ == "__main__":
    main()
