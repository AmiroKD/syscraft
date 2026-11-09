"""性能验收（运行时管理设计：万条数据分页 P95）。

流程：mcp 账号直灌 10,000 行到 biz.trade_order → 以超管令牌打 /query
（size 20 与 100 各 N 次，带过滤与不带过滤）→ 报 P50/P95/P99 与吞吐。
数字供论文 6.x 性能一节引用；数据用完即清。
"""

from __future__ import annotations

import argparse
import os
import statistics
import time

import httpx
import pymysql

BASE = "http://127.0.0.1:8080"


def seed(rows: int) -> None:
    connection = pymysql.connect(
        host="127.0.0.1", port=3306,
        user=os.environ["MCP_USER"], password=os.environ["MCP_PWD"],
        database="biz", charset="utf8mb4",
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM trade_order WHERE order_no LIKE 'PERF-%'")
            batch = []
            for i in range(rows):
                batch.append(
                    f"('PERF-{i:06d}', '压测客户{i % 97}', {i % 9 + 1}, {i % 500 + 1}.00, "
                    f"{(i % 9 + 1) * (i % 500 + 1)}.00, '{'CREATED' if i % 2 else 'PAID'}', "
                    f"'2026-11-09 12:00:00')")
            # 500 行一批的多值插入：1 万行 20 条语句，秒级完成
            for start in range(0, rows, 500):
                chunk = batch[start:start + 500]
                cursor.execute(
                    "INSERT INTO trade_order (order_no, customer, qty, price, amount, status, created_at) "
                    "VALUES " + ",".join(chunk))
        connection.commit()
    finally:
        connection.close()


def measure(token: str, label: str, payload: dict, rounds: int) -> dict:
    latencies = []
    for _ in range(rounds):
        start = time.perf_counter()
        response = httpx.post(f"{BASE}/api/biz/trade_order/query",
                              headers={"Authorization": f"Bearer {token}"},
                              json=payload, timeout=10)
        elapsed = (time.perf_counter() - start) * 1000
        assert response.status_code == 200, response.text[:200]
        latencies.append(elapsed)
    latencies.sort()
    return {
        "label": label,
        "rounds": rounds,
        "p50": round(statistics.median(latencies), 1),
        "p95": round(latencies[int(len(latencies) * 0.95)], 1),
        "p99": round(latencies[int(len(latencies) * 0.99)], 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--rounds", type=int, default=60)
    args = parser.parse_args()

    print(f"灌入 {args.rows} 行…")
    seed(args.rows)

    token = httpx.post(f"{BASE}/api/auth/login", json={
        "username": os.environ["ADMIN_USER"], "password": os.environ["ADMIN_INIT_PWD"],
    }, timeout=5).json()["data"]["token"]

    results = [
        measure(token, "无条件分页 size=20", {"page": 1, "size": 20}, args.rounds),
        measure(token, "无条件分页 size=100", {"page": 1, "size": 100}, args.rounds),
        measure(token, "EQ 过滤 size=20",
                {"page": 1, "size": 20,
                 "filter": {"op": "AND",
                            "conditions": [{"field": "status", "operator": "EQ", "value": "PAID"}]}},
                args.rounds),
        measure(token, "LIKE 过滤 size=20",
                {"page": 1, "size": 20,
                 "filter": {"op": "AND",
                            "conditions": [{"field": "customer", "operator": "LIKE", "value": "压测客户1%"}]}},
                args.rounds),
    ]

    print(f"\n数据量 {args.rows} 行，每项 {args.rounds} 次（毫秒）：")
    for row in results:
        print(f"  {row['label']:<18} P50={row['p50']:>7}  P95={row['p95']:>7}  P99={row['p99']:>7}")

    # 数据用完即清：性能数据不留在演示环境
    seed(0)
    print("压测数据已清理")

    target = results[0]["p95"]
    ok = target < 500
    print(f"\n结论：{'达标' if ok else '未达标'}（P95 {target}ms，目标 < 500ms）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
