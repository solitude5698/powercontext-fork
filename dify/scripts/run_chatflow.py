"""Run the exported Dify Chatflow DSL without a Dify engine (safe mock).

The runner reproduces the Dify 1.x workflow semantics this app relies on:

* a variable pool seeded by the start node inputs,
* ``{{#node_id.variable#}}`` template resolution in http-request bodies,
  llm prompts and the answer node,
* sequential traversal of the exported graph edges,
* http-request nodes posting JSON bodies to the backend,
* an llm node calling an OpenAI compatible endpoint when ``OPENAI_BASE_URL``
  and ``OPENAI_API_KEY`` are set, otherwise producing a deterministic local
  reply so the main chain stays runnable without credentials,
* an answer node rendering the final response.

Every node transition is appended to a JSONL trace, so a run can be archived
as evidence that the Chatflow graph itself executes end to end.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import yaml

TEMPLATE = re.compile(r"\{\{#([0-9A-Za-z_]+)\.([0-9A-Za-z_]+)#\}\}")


def load_graph(dsl_path: str) -> dict:
    with open(dsl_path, "r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    return document["workflow"]["graph"]


def order_nodes(graph: dict) -> list:
    nodes = {node["id"]: node for node in graph["nodes"]}
    successors = {edge["source"]: edge["target"] for edge in graph["edges"]}
    start = next(node_id for node_id, node in nodes.items() if node["data"]["type"] == "start")
    ordered = []
    current = start
    while current is not None:
        ordered.append(nodes[current])
        current = successors.get(current)
    return ordered


def render(template: str, pool: dict) -> str:
    def replace(match: re.Match) -> str:
        value = pool.get((match.group(1), match.group(2)), "")
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    return TEMPLATE.sub(replace, template)


def post_json(url: str, payload: dict, retries: int, timeout: float) -> tuple:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.2)
    raise RuntimeError(f"POST {url} failed after {retries + 1} attempts: {last_error}")


def mock_llm_reply(pool: dict) -> str:
    state = json.loads(pool[("1000000000002", "body")])["state"]
    act = json.loads(pool[("1000000000004", "body")])
    lore = json.loads(pool[("1000000000003", "body")]).get("results") or []
    memory = (state.get("memory_hint") or "记仇墙还空着").rstrip("。")
    player = state["player"]["id"]
    consequence = act.get("consequence", {})
    gold_delta = consequence.get("gold_delta", 0)
    rep_delta = consequence.get("rep_delta", 0)
    tag = consequence.get("tag") or "新账"
    lore_title = lore[0]["title"] if lore else "客栈的老规矩"
    lore_content = lore[0]["content"] if lore else ""
    return (
        f"（老周把算盘往柜台上一放，抬眼看你）{player}，账我都记着呢，{memory}。"
        f"这一笔记作「{tag}」，金币{gold_delta:+d}、声望{rep_delta:+d}。"
        f"说到这个，倒让我想起「{lore_title}」——{lore_content}"
        f"下次再来，我可还认得你。"
    )


def call_openai(prompts: list) -> str:
    base = os.environ["OPENAI_BASE_URL"].rstrip("/")
    request_body = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "messages": prompts,
        "temperature": 0.85,
    }
    request = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"]


def run(dsl_path: str, inputs: dict, base_url: str, trace_path: str | None, force_mock: bool) -> str:
    graph = load_graph(dsl_path)
    pool: dict = {}
    trace = []
    final_answer = ""
    use_openai = not force_mock and os.getenv("OPENAI_BASE_URL") and os.getenv("OPENAI_API_KEY")

    for step, node in enumerate(order_nodes(graph), start=1):
        data = node["data"]
        node_type = data["type"]
        started = time.time()
        inputs_used: dict = {}
        outputs: dict = {}

        if node_type == "start":
            for variable in data.get("variables", []):
                value = inputs.get(variable["variable"], "")
                if variable.get("required") and not value:
                    raise SystemExit(f"missing required start variable: {variable['variable']}")
                pool[(node["id"], variable["variable"])] = value
                outputs[variable["variable"]] = value
        elif node_type == "http-request":
            body_template = data["body"]["data"][0]["value"]
            rendered = render(body_template, pool)
            payload = json.loads(rendered)
            url = data["url"].replace("host.docker.internal", "127.0.0.1")
            if base_url:
                url = base_url.rstrip("/") + urlsplit(url).path
            retries = data.get("retry_config", {}).get("max_retries", 0) if data.get("retry_config", {}).get("retry_enabled") else 0
            status, body = post_json(url, payload, retries, float(data["timeout"]["max_read_timeout"]))
            pool[(node["id"], "body")] = body
            pool[(node["id"], "status_code")] = str(status)
            inputs_used = {"request": payload, "url": url}
            outputs = {"status_code": status, "body": body}
        elif node_type == "llm":
            prompts = [
                {"role": item["role"], "content": render(item["text"], pool)}
                for item in data["prompt_template"]
            ]
            text = call_openai(prompts) if use_openai else mock_llm_reply(pool)
            pool[(node["id"], "text")] = text
            inputs_used = {"messages": prompts, "model": data["model"]["name"], "mode": "openai" if use_openai else "mock"}
            outputs = {"text": text}
        elif node_type == "answer":
            final_answer = render(data["answer"], pool)
            outputs = {"answer": final_answer}
        else:
            raise SystemExit(f"unsupported node type: {node_type}")

        trace.append(
            {
                "step": step,
                "node_id": node["id"],
                "title": data["title"],
                "type": node_type,
                "elapsed_ms": round((time.time() - started) * 1000, 1),
                "inputs": inputs_used,
                "outputs": outputs,
            }
        )

    if trace_path:
        with open(trace_path, "w", encoding="utf-8") as handle:
            for entry in trace:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return final_answer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsl", default=os.path.join(os.path.dirname(__file__), "..", "dsl", "cyber-inn.yml"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--player-id", default="阿飞")
    parser.add_argument("--action", default="我把后院的酒坛砸了")
    parser.add_argument("--trace", default=None, help="write a JSONL node trace to this path")
    parser.add_argument("--mock-llm", action="store_true", help="always use the local deterministic reply")
    args = parser.parse_args()

    answer = run(
        args.dsl,
        {"player_id": args.player_id, "action": args.action},
        args.base_url,
        args.trace,
        args.mock_llm,
    )
    print(answer)


if __name__ == "__main__":
    sys.exit(main())
