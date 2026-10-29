"""Drive a real Dify engine end to end: import the chatflow DSL, publish, then replay
three sessions that share one player's memory across separate conversations.

Env:
  DIFY_BASE_URL          default http://127.0.0.1
  DIFY_CONSOLE_EMAIL     Dify console account
  DIFY_CONSOLE_PASSWORD  Dify console password
  INN_BASE_URL           default http://127.0.0.1:8765
  DIFY_ENGINE_LOG        output JSONL trace, default logs/dify-engine-run.jsonl

Dify's console API expects base64-encoded passwords on the wire.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DIFY = os.environ.get('DIFY_BASE_URL', 'http://127.0.0.1')
EMAIL = os.environ.get('DIFY_CONSOLE_EMAIL', '')
PASSWORD = os.environ.get('DIFY_CONSOLE_PASSWORD', '')
INN = os.environ.get('INN_BASE_URL', 'http://127.0.0.1:8765')
DSL = os.environ.get('DSL_PATH', os.path.join(HERE, '..', 'dsl', 'cyber-inn.yml'))
APP_NAME = os.environ.get('DIFY_APP_NAME', '赛博客栈·老周的记性')
PLAYER = '阿飞'
TRACE_PATH = os.environ.get('DIFY_ENGINE_LOG', os.path.join(HERE, '..', 'logs', 'dify-engine-run.jsonl'))


class Http:
    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}

    def request(self, method: str, url: str, payload=None, token: str = '', raw: bool = False):
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
        if self.cookies:
            req.add_header('Cookie', '; '.join('%s=%s' % kv for kv in self.cookies.items()))
            req.add_header('X-CSRF-Token', self.cookies.get('csrf_token', ''))
        if token:
            req.add_header('Authorization', 'Bearer ' + token)
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                for header in resp.headers.get_all('Set-Cookie') or []:
                    pair = header.split(';', 1)[0]
                    if '=' in pair:
                        key, value = pair.split('=', 1)
                        self.cookies[key] = value
                body = resp.read()
                if raw:
                    return resp.status, body
                text = body.decode('utf-8', 'replace')
                return resp.status, (json.loads(text) if text.strip().startswith(('{', '[')) else text)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')
            raise RuntimeError('%s %s -> %s %s' % (method, url, exc.code, detail[:400])) from None


http = Http()


def console(method: str, path: str, payload=None):
    return http.request(method, DIFY + path, payload)[1]


def inn(path: str, payload=None):
    return http.request('POST', INN + path, payload)[1]


def login() -> None:
    if not EMAIL or not PASSWORD:
        raise SystemExit('DIFY_CONSOLE_EMAIL / DIFY_CONSOLE_PASSWORD are required')
    console('POST', '/console/api/login', {
        'email': EMAIL,
        'password': base64.b64encode(PASSWORD.encode()).decode(),
        'remember_me': True,
    })


def find_app() -> dict | None:
    for item in (console('GET', '/console/api/apps?page=1&limit=50').get('data') or []):
        if item.get('name') == APP_NAME:
            return item
    return None


def import_app() -> dict:
    yaml_text = open(DSL, encoding='utf-8').read()
    result = console('POST', '/console/api/apps/imports',
                     {'mode': 'yaml-content', 'yaml_content': yaml_text, 'name': APP_NAME})
    if result.get('status') == 'pending':
        result = console('POST', '/console/api/apps/imports/%s/confirm' % result['id'])
    if not str(result.get('status', '')).startswith('completed'):
        raise RuntimeError('import failed: %s' % json.dumps(result, ensure_ascii=False)[:500])
    return result


def publish(app_id: str) -> None:
    console('POST', '/console/api/apps/%s/workflows/publish' % app_id,
            {'marked_name': 'real-engine-run', 'marked_comment': 'run_dify_engine.py'})


def api_key(app_id: str) -> str:
    keys = console('GET', '/console/api/apps/%s/api-keys' % app_id).get('data') or []
    for item in keys:
        if item.get('type') == 'app':
            return item['token']
    return console('POST', '/console/api/apps/%s/api-keys' % app_id, {'type': 'app'})['token']


def stream_chat(token: str, query: str, conversation_id: str = '') -> tuple[dict, list[dict]]:
    payload = {'inputs': {'player_id': PLAYER, 'action': query}, 'query': query,
               'response_mode': 'streaming', 'conversation_id': conversation_id,
               'user': PLAYER, 'files': []}
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(DIFY + '/v1/chat-messages', data=data, method='POST',
                                 headers={'Content-Type': 'application/json',
                                          'Authorization': 'Bearer ' + token})
    events: list[dict] = []
    answer = ''
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw in resp:
                line = raw.decode('utf-8', 'replace').strip()
                if not line.startswith('data:'):
                    continue
                chunk = line[5:].strip()
                if chunk == '[DONE]':
                    break
                event = json.loads(chunk)
                events.append(event)
                if event.get('event') == 'message':
                    answer += event.get('answer') or ''
                elif event.get('event') in ('error', 'workflow_finished') and event.get('event') == 'error':
                    raise RuntimeError('dify error event: %s' % json.dumps(event, ensure_ascii=False)[:300])
    except urllib.error.HTTPError as exc:
        raise RuntimeError('chat-messages -> %s %s' % (exc.code, exc.read().decode('utf-8', 'replace')[:400])) from None
    final = {'answer': answer, 'events': events, 'elapsed_s': round(time.time() - started, 2)}
    return final, events


def summarize(run: dict) -> dict:
    """Keep the parts that prove the engine ran the graph, drop bulky payloads."""
    keep: list[dict] = []
    run_id = ''
    for event in run['events']:
        kind = event.get('event')
        if kind == 'workflow_started':
            run_id = event.get('workflow_run_id', '')
            keep.append({'event': 'workflow_started', 'workflow_run_id': run_id,
                         'conversation_id': event.get('conversation_id'),
                         'task_id': event.get('task_id')})
        elif kind in ('node_started', 'node_finished'):
            data = event.get('data') or {}
            record = {'event': kind, 'title': data.get('title'), 'node_type': data.get('node_type'),
                      'status': data.get('status')}
            if kind == 'node_finished':
                outputs = data.get('outputs') or {}
                body = outputs.get('body') or outputs.get('text') or outputs
                record['output'] = str(body)[:400]
                record['elapsed_s'] = data.get('elapsed_time')
            else:
                record['inputs'] = str(data.get('inputs'))[:300]
            keep.append(record)
    return {'workflow_run_id': run_id, 'events': keep}


def main() -> int:
    login()
    app = find_app()
    if app is None:
        import_app()
        app = find_app()
    if app is None:
        raise SystemExit('app not found after import')
    app_id = app['id']
    publish(app_id)
    token = api_key(app_id)

    engine = http.request('GET', INN + '/health')[1]
    inn('/api/reset', {'player_id': PLAYER})
    os.makedirs(os.path.dirname(TRACE_PATH), exist_ok=True)
    trace = open(TRACE_PATH, 'w', encoding='utf-8')

    def emit(record: dict) -> None:
        trace.write(json.dumps(record, ensure_ascii=False) + '\n')
        trace.flush()

    emit({'kind': 'meta', 'dify_base_url': DIFY, 'app_id': app_id, 'app_mode': app.get('mode'),
          'dsl': os.path.basename(DSL), 'backend': engine, 'player_id': PLAYER,
          'started_at': time.strftime('%Y-%m-%dT%H:%M:%S')})
    emit({'kind': 'state_before', 'state': inn('/api/state', {'player_id': PLAYER})})

    sessions = [
        ('session1', '我把后院的酒坛砸了'),
        ('session2', '你还记得我上次在你店里干了什么吗？'),
        ('session3', '我想用一壶桂花酿抵了旧账'),
    ]
    infos: list[tuple[str, str]] = []
    for name, query in sessions:
        run, _ = stream_chat(token, query)
        summary = summarize(run)
        conversation_id = ''
        for event in run['events']:
            if event.get('conversation_id'):
                conversation_id = event['conversation_id']
        emit({'kind': 'session', 'name': name, 'query': query, 'conversation_id': conversation_id,
              'elapsed_s': run['elapsed_s'], 'workflow_run_id': summary['workflow_run_id'],
              'nodes': summary['events'], 'answer': run['answer']})
        emit({'kind': 'state_after', 'name': name, 'state': inn('/api/state', {'player_id': PLAYER})})
        infos.append((name, conversation_id))
        print('%s conversation=%s run=%s' % (name, conversation_id, summary['workflow_run_id']))
        print('  answer:', run['answer'][:120].replace('\n', ' '))

    emit({'kind': 'conversations', 'distinct': len({c for _, c in infos}) == len(infos),
          'items': [{'name': n, 'conversation_id': c} for n, c in infos]})
    trace.close()
    print('trace ->', os.path.normpath(TRACE_PATH))
    return 0


if __name__ == '__main__':
    sys.exit(main())
