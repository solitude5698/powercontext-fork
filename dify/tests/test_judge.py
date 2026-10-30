"""判定规则的回归测试：python -m pytest dify/tests 或 python dify/tests/test_judge.py"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from server import judge  # noqa: E402

CASES = (
    ("你还记得我上次在你店里干了什么吗？", 0, 0, True),
    ("老周，还记得我上次砸了酒坛吗？", 0, 0, True),
    ("上次那笔账，你记着吗", 0, 0, True),
    ("我上次喝多了，把后院的酒坛砸了", -15, -12, False),
    ("我趁老周转身，把账本撕了", -15, -12, False),
    ("我帮你把门口的雪扫了", -5, 12, False),
    ("我还你一坛酒", -30, 15, False),
    ("我想用一壶桂花酿抵了旧账", -2, 1, False),
)


def main() -> int:
    failed = 0
    for text, gold, rep, recall in CASES:
        got = judge(text)
        ok = got["gold_delta"] == gold and got["rep_delta"] == rep and bool(got.get("recall")) == recall
        print(("PASS" if ok else "FAIL"), text, got)
        failed += 0 if ok else 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
