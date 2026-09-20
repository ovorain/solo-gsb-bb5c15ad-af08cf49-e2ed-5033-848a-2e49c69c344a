#!/bin/sh
# 容器启动初始化：
# 1. 校验关键依赖已就绪（构建时安装，这里只做快速自检）
# 2. 校验应用模块可导入
# 3. exec 转入传入的启动命令（默认 uvicorn）
set -e

echo "[entrypoint] 检查 Python 依赖..."
python -c "import fastapi, uvicorn, pydantic; print('[entrypoint] 依赖版本:', fastapi.__version__, uvicorn.__version__, pydantic.VERSION)"

echo "[entrypoint] 检查应用模块..."
python -c "from app.main import app; print('[entrypoint] 应用模块加载成功，路由数:', len(app.routes))"

echo "[entrypoint] 启动: $*"
exec "$@"
