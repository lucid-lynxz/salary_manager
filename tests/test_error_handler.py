"""错误兜底与 debug 开关回归用例（无第三方测试框架依赖）。

运行方式：
    venv/bin/python tests/test_error_handler.py      # 独立运行，全部通过退出码 0，有失败则 1

（用例函数名与裸 assert 写法兼容 pytest，但本仓库未引入 pytest 依赖，无需要求安装。）

覆盖两条约定（详见 README 四、注意事项）：
1. 服务默认关闭 debug，只有 `SALARY_DEBUG=1` 才开启；
2. 未捕获异常与 4xx/5xx HTTP 异常一律返回 JSON + 中文人话，且响应体不得出现
   Traceback / 异常类型 / 局部变量值，完整堆栈改由 `error_id` 在 app.log 中定位。

用例只读取项目状态：导入 app 会执行 init_db（CREATE TABLE IF NOT EXISTS）与
创建 uploads/ 目录，不会写入业务数据。
"""
import contextlib
import io
import logging
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import app as appmod  # noqa: E402  （导入即完成 debug 开关求值与 errorhandler 注册）

# 保持用例输出干净：去掉 Flask 默认的 stderr handler 并把 werkzeug 请求日志压到
# CRITICAL，否则每次故意触发的 500 都会向终端刷一大段堆栈。需要验证日志落盘时，
# 由 _capture_logger 临时把级别调回 ERROR 并在结束后还原。
from flask.logging import default_handler  # noqa: E402

appmod.app.logger.removeHandler(default_handler)
appmod.app.logger.setLevel(logging.CRITICAL)
logging.getLogger("werkzeug").setLevel(logging.CRITICAL)

# 埋入一个"必然泄露"的敏感值：若某天有人删掉 errorhandler 或改回 debug=True，
# 这个值会出现在响应体里，下面的断言随即失败。
SECRET = "AKIA-模拟敏感授权码"

# 注意：路由必须在首次请求之前注册（Flask 2.2+ 收到首个请求后禁止再调用 setup 方法）
@appmod.app.route("/__test_boom")
def _boom():
    leaked_token = SECRET
    raise ValueError("模拟异常：解析 Excel 第 42 行失败")


def _client():
    return appmod.app.test_client()


@contextlib.contextmanager
def _capture_logger():
    """把 app.logger 的 ERROR 输出接到内存，用于校验错误号与堆栈确实落盘。"""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.ERROR)
    old_level = appmod.app.logger.level
    appmod.app.logger.addHandler(handler)
    appmod.app.logger.setLevel(logging.ERROR)
    try:
        yield buf, handler
    finally:
        appmod.app.logger.removeHandler(handler)
        appmod.app.logger.setLevel(old_level)


def test_debug_off_by_default():
    assert appmod.DEBUG is False, (
        "未设置 SALARY_DEBUG 时 debug 必须默认关闭；若你正在开发，请先 unset SALARY_DEBUG 再跑用例"
    )


def test_debug_enabled_by_env():
    """SALARY_DEBUG=1 需在子进程中验证（模块级开关在 import 时就已求值）。"""
    env = dict(os.environ, SALARY_DEBUG="1")
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); import app; print(app.DEBUG)" % str(ROOT)],
        env=env, capture_output=True, text=True, cwd=str(ROOT),
    ).stdout.strip()
    assert out == "True", f"SALARY_DEBUG=1 应开启 debug，实际 {out!r}"


def test_unhandled_error_returns_json_without_leak():
    resp = _client().get("/__test_boom")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 500, resp.status_code
    assert resp.is_json, "500 必须返回 JSON，否则前端会把 HTML 整段塞进 alert()"
    for leak in (SECRET, "leaked_token", "Traceback", "ValueError", "模拟异常"):
        assert leak not in body, f"响应体泄露了内部信息：{leak}"
    data = resp.get_json()
    assert data.get("error") and data.get("error_id"), "缺少 error / error_id 字段"
    assert len(body) < 1024, f"响应体应远小于调试页（实测 debug=True 为 14365 字节），实际 {len(body)}"


def test_error_id_locates_traceback_in_log():
    with _capture_logger() as (buf, _handler):
        data = _client().get("/__test_boom").get_json()
    log_text = buf.getvalue()
    assert f"error_id={data['error_id']}" in log_text, "日志中找不到错误号，无法定位"
    assert "模拟异常：解析 Excel 第 42 行失败" in log_text, "日志中缺少异常原文"
    assert "Traceback (most recent call last)" in log_text, "日志中缺少完整堆栈"


def test_http_exceptions_return_json_in_chinese():
    client = _client()

    missing = client.get("/no-such-page")
    assert missing.status_code == 404 and missing.is_json
    assert "请求的内容不存在" in missing.get_json()["error"], missing.get_json()

    bad_method = client.post("/api/user-config", data="{}",
                            headers={"Content-Type": "application/json"})
    assert bad_method.status_code == 405 and bad_method.is_json
    assert "请求方式" in bad_method.get_json()["error"], bad_method.get_json()

    # 非法 JSON 会在 request.get_json() 处抛 400，早于 save_user_config，不会改到真实配置
    bad_body = client.put("/api/user-config", data="{bad json",
                          headers={"Content-Type": "application/json"})
    assert bad_body.status_code == 400, bad_body.status_code
    assert bad_body.is_json, "非法 JSON 请求体也必须返回 JSON"
    assert "请求内容格式有误" in bad_body.get_json()["error"], bad_body.get_json()


def test_normal_routes_unaffected():
    assert _client().get("/").status_code == 200, "错误兜底不应影响正常页面"


if __name__ == "__main__":
    cases = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failed = 0
    for name, fn in cases:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {name}: {exc}")
    print(f"\n{len(cases) - failed}/{len(cases)} 用例通过")
    sys.exit(1 if failed else 0)
