# -*- coding: utf-8 -*-
"""appload.py —— 在进程内加载 app 的字节码，用于离线跑真实逻辑。"""
from __future__ import annotations

import marshal
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402


def load_app(patch_files=()):
    """exec .work/ref/app 的 code object，可选地覆盖若干顶层函数。

    patch_files 里的每个 .py 会被编译后在模块命名空间里 exec，从而按「同名顶层
    函数」覆盖原实现 —— 这正是 build.py 打进 exe 的那套机制。
    """
    missing = paths.missing_work()
    if missing:
        raise SystemExit("缺少参考文件：%s\n先跑  py -3.13 tools/bootstrap.py" % missing)
    paths.add_ref_to_syspath()

    code = marshal.loads((paths.REF / "app").read_bytes())
    mod = types.ModuleType("appmod")
    mod.__file__ = str(paths.REF / "app.py")
    mod.__name__ = "appmod"          # 让 `if __name__ == "__main__"` 不触发
    exec(code, mod.__dict__)
    mod._VPNINFO_PS1 = str(paths.REF / "vpninfo.ps1")

    for f in patch_files:
        src = Path(f).read_text(encoding="utf-8")
        exec(compile(src, "app.py", "exec"), mod.__dict__)
    return mod


def serve(mod):
    """起一个真实 Handler，端口随机，返回 (server, port)。"""
    import threading
    from http.server import ThreadingHTTPServer

    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def get_json(port: int, path: str, timeout: float = 40.0):
    """按前端的方式发请求（不走系统代理）。"""
    import time
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    with opener.open("http://127.0.0.1:%d%s" % (port, path), timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    return __import__("json").loads(body), time.time() - t0