"""赛博客栈 backend: seekdb stores players, events and full-text lore."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pymysql

DB_CONFIG = {
    "host": os.getenv("SEEKDB_HOST", "127.0.0.1"),
    "port": int(os.getenv("SEEKDB_PORT", "2881")),
    "user": os.getenv("SEEKDB_USER", "root"),
    "password": os.getenv("SEEKDB_PASSWORD", ""),
    "database": os.getenv("SEEKDB_DATABASE", "cyber_inn"),
    "charset": "utf8mb4",
    "autocommit": True,
}
HTTP_PORT = int(os.getenv("INN_HTTP_PORT", "8765"))

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS players ("
    " player_id VARCHAR(64) PRIMARY KEY,"
    " gold INT NOT NULL DEFAULT 100,"
    " reputation INT NOT NULL DEFAULT 0,"
    " rounds INT NOT NULL DEFAULT 0,"
    " last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
    ") DEFAULT CHARSET = utf8mb4",
    "CREATE TABLE IF NOT EXISTS events ("
    " id BIGINT AUTO_INCREMENT PRIMARY KEY,"
    " player_id VARCHAR(64) NOT NULL,"
    " action TEXT NOT NULL,"
    " tag VARCHAR(64) NOT NULL DEFAULT '',"
    " gold_delta INT NOT NULL DEFAULT 0,"
    " rep_delta INT NOT NULL DEFAULT 0,"
    " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
    " KEY idx_player (player_id, id)"
    ") DEFAULT CHARSET = utf8mb4",
    "CREATE TABLE IF NOT EXISTS lore ("
    " id INT AUTO_INCREMENT PRIMARY KEY,"
    " title VARCHAR(128) NOT NULL,"
    " content TEXT NOT NULL,"
    " tags VARCHAR(255) NOT NULL DEFAULT '',"
    " FULLTEXT KEY ft_lore (content) WITH PARSER ngram"
    ") DEFAULT CHARSET = utf8mb4",
)

LORE = (
    ("断魂酒坛", "后院的断魂酒坛是开栈祖师留下的，坛底刻着一行小字：摔坛者，三年之内不许再点桂花酿。", "酒坛,规矩"),
    ("不打烊的账本", "老周的账本从不合上，欠账的人名会自己浮到封面。欠账还清的那一夜，名字才会淡下去。", "账本,欠账"),
    ("招牌上的雨", "客栈招牌‘江湖夜雨’四个字，只有下雨的夜晚才会显出第四道笔画，据说那是老周年轻时留下的剑痕。", "招牌,剑痕"),
    ("说书人的空椅", "角落的说书人椅子空了很多年。谁要是坐上去讲一个没人听过的故事，第二天桌上就会多一壶温好的酒。", "说书,故事"),
    ("后厨的猫", "后厨那只黑猫只亲近还过账的客人。它若绕着你走，说明老周还在心里给你记着一笔。", "猫,欠账"),
    ("三更的马蹄", "三更天若有马蹄停在门口却不进屋，老周会多摆一副碗筷，说是给赶不及投宿的人留的。", "三更,规矩"),
    ("不收现银的规矩", "客栈有一条怪规矩：大额现银不收，得换成故事、手艺或者一次跑腿。老周说银子会咬人。", "规矩,银子"),
    ("酒杯里的月亮", "第二坛桂花酿斟满时，杯底会浮起一枚小月亮。喝到的人第二天说话会格外老实。", "桂花酿,真话"),
    ("墙上的钉子", "西墙第七颗钉子上挂过十七把刀，每一把都是来砸场子的人留下的。钉子从来没换过。", "砸场子,钉子"),
    ("老周的旧伤", "老周左肩有一道旧伤，阴天会疼。他只对信得过的客人说过一次：那是替人挡的。", "旧伤,信任"),
)


def connect(with_db: bool = True):
    cfg = dict(DB_CONFIG)
    if not with_db:
        cfg.pop("database")
    return pymysql.connect(**cfg)


def init_db() -> None:
    with connect(with_db=False) as conn, conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS {DB_CONFIG['database']} DEFAULT CHARSET utf8mb4")
    with connect() as conn, conn.cursor() as cur:
        for ddl in SCHEMA:
            cur.execute(ddl)
        cur.execute("SELECT COUNT(*) FROM lore")
        if cur.fetchone()[0] == 0:
            cur.executemany("INSERT INTO lore (title, content, tags) VALUES (%s, %s, %s)", LORE)


CONSEQUENCES = (
    (("偷", "抢", "砸", "烧", "踢", "掀", "撕", "毁"), -15, -12, "砸场子"),
    (("请", "买单", "送", "夸", "敬"), -20, 10, ""),
    (("还", "赔", "补"), -30, 15, ""),
    (("骗", "吹", "撒谎", "赖"), 0, -8, "满嘴跑火车"),
    (("帮", "救", "挡", "扶"), -5, 12, "欠你一条命"),
    (("打听", "问", "想起", "上次", "记得"), 0, 2, ""),
)


def judge(action: str) -> dict:
    for words, gold, rep, tag in CONSEQUENCES:
        if any(word in action for word in words):
            return {"gold_delta": gold, "rep_delta": rep, "tag": tag}
    return {"gold_delta": -2, "rep_delta": 1, "tag": ""}


def ensure_player(cur, player_id: str) -> None:
    cur.execute("INSERT IGNORE INTO players (player_id) VALUES (%s)", (player_id,))


def load_state(cur, player_id: str) -> dict:
    cur.execute("SELECT player_id, gold, reputation, rounds FROM players WHERE player_id = %s", (player_id,))
    row = cur.fetchone()
    cur.execute(
        "SELECT tag, COUNT(*) AS n FROM events WHERE player_id = %s AND tag <> '' GROUP BY tag ORDER BY n DESC, tag LIMIT 3",
        (player_id,),
    )
    grudges = [{"tag": tag, "count": int(count)} for tag, count in cur.fetchall()]
    cur.execute(
        "SELECT action, tag, gold_delta, rep_delta, created_at FROM events WHERE player_id = %s ORDER BY id DESC LIMIT 5",
        (player_id,),
    )
    recent = [
        {"action": action[:80], "tag": tag, "gold_delta": int(g), "rep_delta": int(r), "at": str(at)}
        for action, tag, g, r, at in cur.fetchall()
    ]
    memory = "新客第一次来，没有旧事。"
    if grudges:
        memory = "老周的记仇墙：" + "、".join(f"{item['tag']}×{item['count']}" for item in grudges)
    return {
        "player": {"id": row[0], "gold": int(row[1]), "reputation": int(row[2]), "rounds": int(row[3])},
        "grudges": grudges,
        "recent": recent,
        "memory_hint": memory,
    }


def search_lore(cur, query: str, limit: int) -> list[dict]:
    cur.execute(
        "SELECT title, content, MATCH(content) AGAINST (%s IN NATURAL LANGUAGE MODE) AS score "
        "FROM lore WHERE MATCH(content) AGAINST (%s IN NATURAL LANGUAGE MODE) > 0 "
        "ORDER BY score DESC LIMIT %s",
        (query, query, limit),
    )
    rows = cur.fetchall()
    if not rows:
        like = f"%{query[:48]}%"
        cur.execute("SELECT title, content, 0 FROM lore WHERE content LIKE %s OR tags LIKE %s LIMIT %s", (like, like, limit))
        rows = cur.fetchall()
    return [{"title": title, "content": content, "score": float(score)} for title, content, score in rows]


class Handler(BaseHTTPRequestHandler):
    server_version = "CyberInn/1.0"

    def log_message(self, fmt, *args):
        print("[http]", fmt % args, flush=True)

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        if self.path == "/health":
            with connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT VERSION()")
                version = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM lore")
                lore_count = cur.fetchone()[0]
            self._send({"ok": True, "engine": version, "lore": lore_count})
            return
        self._send({"error": "not found"}, 404)

    def do_POST(self):
        payload = self._read_json()
        player_id = str(payload.get("player_id") or "guest")[:64]
        try:
            with connect() as conn, conn.cursor() as cur:
                if self.path == "/api/state":
                    ensure_player(cur, player_id)
                    self._send({"state": load_state(cur, player_id)})
                elif self.path == "/api/act":
                    action = str(payload.get("action") or "")[:500]
                    if not action:
                        self._send({"error": "action required"}, 400)
                        return
                    ensure_player(cur, player_id)
                    verdict = judge(action)
                    cur.execute(
                        "INSERT INTO events (player_id, action, tag, gold_delta, rep_delta) VALUES (%s, %s, %s, %s, %s)",
                        (player_id, action, verdict["tag"], verdict["gold_delta"], verdict["rep_delta"]),
                    )
                    cur.execute(
                        "UPDATE players SET gold = gold + %s, reputation = reputation + %s, rounds = rounds + 1 "
                        "WHERE player_id = %s",
                        (verdict["gold_delta"], verdict["rep_delta"], player_id),
                    )
                    self._send({"state": load_state(cur, player_id), "consequence": verdict})
                elif self.path == "/api/lore/search":
                    query = str(payload.get("query") or "").strip()
                    limit = max(1, min(int(payload.get("k") or 3), 10))
                    self._send({"query": query, "results": search_lore(cur, query, limit)})
                elif self.path == "/api/reset":
                    cur.execute("DELETE FROM events WHERE player_id = %s", (player_id,))
                    cur.execute("DELETE FROM players WHERE player_id = %s", (player_id,))
                    self._send({"ok": True, "player_id": player_id})
                else:
                    self._send({"error": "not found"}, 404)
        except pymysql.MySQLError as exc:
            self._send({"error": "seekdb unavailable", "detail": str(exc)}, 503)


def main() -> None:
    init_db()
    print(f"赛博客栈 backend on :{HTTP_PORT} -> seekdb {DB_CONFIG['host']}:{DB_CONFIG['port']}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
