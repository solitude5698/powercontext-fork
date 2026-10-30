"""End-to-end check of the Dify HTTP nodes against seekdb, no Dify required."""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
PLAYER = "阿飞"


def call(path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def show(title: str, payload: dict) -> None:
    print(f"\n### {title}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


show("1. reset 初始状态", call("/api/reset", {"player_id": PLAYER}))
show("2. 前情：砸酒坛", call("/api/act", {"player_id": PLAYER, "action": "我上次喝多了，把后院的酒坛砸了"}))
show("3. 新会话：再次进店（记忆应包含砸场子）", call("/api/state", {"player_id": PLAYER}))
show("4. 记忆回顾问句（只读，不应扣费或推进回合）", call("/api/act", {"player_id": PLAYER, "action": "你还记得我上次在你店里干了什么吗？"}))
show("5. 检索传说：酒坛规矩", call("/api/lore/search", {"query": "酒坛 规矩", "k": 3}))
show("6. 本次行为：撕账本", call("/api/act", {"player_id": PLAYER, "action": "我趁老周转身，把账本撕了"}))
show("7. 结算后的记忆", call("/api/state", {"player_id": PLAYER}))
show("8. 检索传说：欠账", call("/api/lore/search", {"query": "欠账 还清 名字", "k": 2}))
