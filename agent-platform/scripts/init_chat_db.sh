#!/usr/bin/env bash
# 建会话库与专用账号（一次性，幂等）。
#
# 为什么单独一个库：平台只写自己的 chat 库，adm（权限）与 biz（业务）归产物；
# 账号只授权 chat.*，越权写权限库会被 MySQL 直接拒绝。
#
# 用法：./scripts/init_chat_db.sh
# 前置：~/.adaptive/db.env 里有 MYSQL_ROOT_PWD。
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="${HOME}/.adaptive/db.env"
[ -f "$ENV_FILE" ] || { echo "缺少 $ENV_FILE"; exit 1; }
set -a; . "$ENV_FILE"; set +a
: "${MYSQL_ROOT_PWD:?需要 MYSQL_ROOT_PWD（见 db.env）}"

DB="${CHAT_DB_NAME:-chat}"
TEST_DB="${DB}_test"
DB_USER="${CHAT_DB_USER:-chat}"

if [ -n "${CHAT_DB_PASSWORD:-}" ]; then
  echo "CHAT_DB_* 已配置，跳过（要重置：先删 db.env 里 5 行 CHAT_DB_*，再重跑）"
  exit 0
fi

PWD_CHAT="$(openssl rand -hex 16)"
mysql -h 127.0.0.1 -u root -p"$MYSQL_ROOT_PWD" 2>/dev/null <<SQL
CREATE DATABASE IF NOT EXISTS ${DB} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS ${TEST_DB} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${PWD_CHAT}';
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${PWD_CHAT}';
ALTER USER '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${PWD_CHAT}';
ALTER USER '${DB_USER}'@'localhost' IDENTIFIED BY '${PWD_CHAT}';
GRANT ALL PRIVILEGES ON ${DB}.* TO '${DB_USER}'@'127.0.0.1';
GRANT ALL PRIVILEGES ON ${DB}.* TO '${DB_USER}'@'localhost';
GRANT ALL PRIVILEGES ON ${TEST_DB}.* TO '${DB_USER}'@'127.0.0.1';
GRANT ALL PRIVILEGES ON ${TEST_DB}.* TO '${DB_USER}'@'localhost';
FLUSH PRIVILEGES;
SQL

{
  printf '\n# 平台会话存储（Python 侧专用，仅 %s / %s 两库权限）\n' "$DB" "$TEST_DB"
  printf 'CHAT_DB_HOST=127.0.0.1\nCHAT_DB_PORT=3306\nCHAT_DB_NAME=%s\n' "$DB"
  printf 'CHAT_DB_USER=%s\nCHAT_DB_PASSWORD=%s\n' "$DB_USER" "$PWD_CHAT"
} >> "$ENV_FILE"

chmod 600 "$ENV_FILE"
echo "已创建 ${DB} / ${TEST_DB}，并写入 ${ENV_FILE}（600）"
