"""ModelEval 后端便捷启动入口。

用法：
    uv run app.py                            # 默认 127.0.0.1:8660
    uv run app.py --port 9000               # 指定端口
    uv run app.py --host 0.0.0.0 --reload   # 监听所有网卡 + 热重载（开发模式）
    PORT=8660 HOST=127.0.0.1 uv run app.py  # 环境变量覆盖

等价于 `uvicorn app.main:app`。使用 import string 启动，避免与同目录的 app/ 包重名冲突。
"""
import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="ModelEval 后端启动器")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8660")))
    parser.add_argument("--reload", action="store_true", help="代码改动自动重启（开发模式）")
    args = parser.parse_args()

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
