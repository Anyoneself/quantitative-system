#!/usr/bin/env sh
set -eu

# 启动服务前执行幂等迁移，保证挂载的新数据卷可以直接使用
python -c "from storage.migrations import initialize_database; initialize_database()"

exec python -m web --host 0.0.0.0 --port 8000
