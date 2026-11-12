#!/usr/bin/env bash
# 平台与产物的最小冒烟：健康 → 登录 → 版本清单 → 一次只读查询。
#
# 用法：
#   ./scripts/smoke.sh                 # 查「有多少条记录」（用在线版本第一个实体）
#   ./scripts/smoke.sh "查一下活动"     # 自定义一句只读问题
#
# 前置：平台 8090 与产物 8080 已启动；凭证在 ~/.adaptive/db.env。
# 退出码非 0 表示冒烟失败，可直接接进 CI/答辩前的自检。
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="${PLATFORM_BASE_URL:-http://127.0.0.1:8090}"
SPRING="${SPRING_BASE_URL:-http://127.0.0.1:8080}"
QUESTION="${1:-查一下活动有多少条}"

if [ -f "$HOME/.adaptive/db.env" ]; then
  set -a; . "$HOME/.adaptive/db.env"; set +a
fi
: "${ADMIN_USER:?缺少 ADMIN_USER（见 ~/.adaptive/db.env）}"
: "${ADMIN_INIT_PWD:?缺少 ADMIN_INIT_PWD（见 ~/.adaptive/db.env）}"

pass() { printf '  [PASS] %s\n' "$1"; }
fail() { printf '  [FAIL] %s\n' "$1"; exit 1; }

echo "== 1/4 平台健康 =="
curl -fsS -m 5 "$BASE/healthz" > /dev/null && pass "$BASE/healthz" || fail "平台不可达"

echo "== 2/4 产物健康 =="
curl -fsS -m 5 "$SPRING/actuator/health" > /dev/null && pass "$SPRING/actuator/health" || fail "产物不可达"

echo "== 3/4 登录与在线版本 =="
# 请求体交给 python3 json.dumps 生成：手工拼 JSON 会在值带引号/反斜杠时变语义
BODY=$(python3 -c 'import json,sys; print(json.dumps({"username": sys.argv[1], "password": sys.argv[2]}))' \
  "$ADMIN_USER" "$ADMIN_INIT_PWD")
TOKEN=$(curl -fsS -m 5 -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' -d "$BODY" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("data",{}).get("token",""))')
[ -n "$TOKEN" ] || fail "登录失败（口令已改？）"
pass "登录成功"

VERSIONS=$(curl -fsS -m 5 "$BASE/api/runtime/versions" -H "Authorization: Bearer $TOKEN" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin)["data"]; print(",".join(v["version"] for v in d))')
[ -n "$VERSIONS" ] || fail "版本清单为空"
pass "在线版本清单：$VERSIONS"

echo "== 4/4 只读查询：$QUESTION =="
BODY=$(python3 -c 'import json,sys; print(json.dumps({"message": sys.argv[1]}))' "$QUESTION")
ANSWER=$(curl -fsS -m 60 -X POST "$BASE/api/chat" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "$BODY" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print((d.get("data") or {}).get("reply") or d.get("msg"))')
[ -n "$ANSWER" ] || fail "查询没有返回内容"
pass "回复：$ANSWER"

echo "== 冒烟通过 =="
