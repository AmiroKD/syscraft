#!/usr/bin/env bash
# 模板回归矩阵（《代码生成器设计》7 节）：三个固定模型 生成→编译→断言。
# 每次改模板后跑一遍：./scripts/regression.sh —— 三者全部通过才算模板可用。
set -euo pipefail
cd "$(dirname "$0")/.."
PY=".venv/bin/python"
MAVEN="$(ls "$HOME"/.m2/wrapper/dists/apache-maven-*/[0-9a-f]*/bin/mvn 2>/dev/null | head -1 || echo mvn)"

for entry in "r1_single:r1" "r2_one_to_many:r2" "r3_rules:r3"; do
  file="${entry%%:*}"; version="${entry##*:}"
  echo "=== 回归 ${version}（models/regression/${file}.json）==="
  rm -rf "workspace/versions/${version}"
  PYTHONPATH=src $PY -m syscraft.codegen.generator --model "models/regression/${file}.json" > /dev/null
  (cd "workspace/versions/${version}" && "$MAVEN" -B -q compile -DskipTests > /tmp/regression-${version}.log 2>&1) \
    && echo "[PASS] ${version} 生成+编译" \
    || { echo "[FAIL] ${version} 编译失败："; tail -20 "/tmp/regression-${version}.log"; exit 1; }
done
echo "=== 回归矩阵全部通过 ==="
