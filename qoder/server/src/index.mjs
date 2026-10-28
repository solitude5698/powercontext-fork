import { createHash } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import cors from 'cors';
import express from 'express';
import { SeekdbAdminClient, SeekdbClient } from 'seekdb';

const HOST = process.env.SEEKDB_HOST ?? '127.0.0.1';
const PORT = Number(process.env.SEEKDB_PORT ?? 2882);
const USER = process.env.SEEKDB_USER ?? 'root';
const PASSWORD = process.env.SEEKDB_PASSWORD ?? '';
const DATABASE = process.env.SEEKDB_DATABASE ?? 'weeklysmith';
const SERVER_PORT = Number(process.env.SERVER_PORT ?? 8891);

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, '..', '..', 'web');

const DDL = [
  `CREATE TABLE IF NOT EXISTS work_notes (
     id VARCHAR(64) NOT NULL PRIMARY KEY,
     content TEXT NOT NULL,
     week VARCHAR(16) NOT NULL,
     kind VARCHAR(16) NOT NULL,
     source VARCHAR(32) NOT NULL DEFAULT 'paste',
     created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
     FULLTEXT KEY idx_fts (content) WITH PARSER ik PARSER_PROPERTIES=(ik_mode='smart')
   )`,
  `CREATE TABLE IF NOT EXISTS user_prefs (
     pref_key VARCHAR(64) NOT NULL PRIMARY KEY,
     pref_value VARCHAR(255) NOT NULL,
     updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
   )`,
  `CREATE TABLE IF NOT EXISTS report_history (
     id VARCHAR(64) NOT NULL PRIMARY KEY,
     week VARCHAR(16) NOT NULL,
     summary TEXT NOT NULL,
     created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
   )`,
];

const KIND_RULES = [
  ['done', /完成|上线|交付|修复|发布|搞定|闭环/],
  ['risk', /风险|阻塞|延期|问题|卡住|依赖/],
  ['plan', /计划|下周|待办|准备|将|目标/],
];

function classify(text) {
  for (const [kind, pattern] of KIND_RULES) if (pattern.test(text)) return kind;
  return 'done';
}

function noteId(text, week) {
  return createHash('sha1').update(`${week}:${text}`).digest('hex').slice(0, 32);
}

function currentWeek(date = new Date()) {
  const target = new Date(date.getTime());
  const day = (target.getDay() + 6) % 7;
  target.setDate(target.getDate() - day + 3);
  const firstThursday = new Date(target.getFullYear(), 0, 4);
  const offset = (firstThursday.getDay() + 6) % 7;
  firstThursday.setDate(firstThursday.getDate() - offset + 3);
  const week = 1 + Math.round((target - firstThursday) / (7 * 24 * 3600 * 1000));
  return `${target.getFullYear()}-W${String(week).padStart(2, '0')}`;
}

let client;

async function connect() {
  const admin = new SeekdbAdminClient({ host: HOST, port: PORT, user: USER, password: PASSWORD });
  try {
    const databases = await admin.listDatabases(200, 0);
    if (!databases.some((db) => db.name === DATABASE)) await admin.createDatabase(DATABASE);
  } finally {
    await admin.close();
  }
  client = new SeekdbClient({ host: HOST, port: PORT, user: USER, password: PASSWORD, database: DATABASE });
  for (const ddl of DDL) await client.execute(ddl);
}

async function getPrefs() {
  const rows = await client.execute('SELECT pref_key, pref_value FROM user_prefs');
  return Object.fromEntries(rows.map((row) => [row.pref_key, row.pref_value]));
}

async function setPrefs(patch) {
  for (const [key, value] of Object.entries(patch)) {
    await client.execute(
      'INSERT INTO user_prefs (pref_key, pref_value) VALUES (?, ?) ON DUPLICATE KEY UPDATE pref_value = VALUES(pref_value)',
      [key, String(value).slice(0, 250)],
    );
  }
}

async function saveNotes(text, week, source) {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.replace(/^[-*·]\s*/, '').trim())
    .filter((line) => line.length >= 4);
  const saved = [];
  for (const line of lines) {
    const item = { id: noteId(line, week), content: line, week, kind: classify(line), source };
    await client.execute(
      'INSERT INTO work_notes (id, content, week, kind, source) VALUES (?, ?, ?, ?, ?) ON DUPLICATE KEY UPDATE content = VALUES(content), kind = VALUES(kind)',
      [item.id, item.content, item.week, item.kind, item.source],
    );
    saved.push(item);
  }
  return saved;
}

async function searchNotes(q, week) {
  const params = [q];
  let sql =
    'SELECT id, content, week, kind, created_at, MATCH(content) AGAINST(?) AS score ' +
    'FROM work_notes WHERE MATCH(content) AGAINST(?)';
  params.push(q);
  if (week) {
    sql += ' AND week = ?';
    params.push(week);
  }
  sql += ' ORDER BY score DESC, created_at DESC LIMIT 20';
  const started = performance.now();
  const rows = await client.execute(sql, params);
  return { elapsedMs: performance.now() - started, hits: rows };
}

async function buildReport(week) {
  const prefs = await getPrefs();
  const rows = await client.execute(
    'SELECT content, kind FROM work_notes WHERE week = ? ORDER BY created_at ASC',
    [week],
  );
  const buckets = { done: [], risk: [], plan: [] };
  for (const row of rows) buckets[row.kind]?.push(row.content);

  const name = prefs.author_name || '我';
  const tone = prefs.tone || '简洁';
  const history = await client.execute(
    'SELECT week, created_at FROM report_history ORDER BY created_at DESC LIMIT 1',
  );

  const bullet = (items) => (items.length ? items.map((item) => `- ${item}`).join('\n') : '- 暂无');
  const draft = [
    `# ${week} 周报（${name}）`,
    '',
    '## 本周完成',
    bullet(buckets.done),
    '',
    '## 风险与阻塞',
    bullet(buckets.risk),
    '',
    '## 下周计划',
    bullet(buckets.plan),
    '',
    `> 语气偏好：${tone}${history.length ? `；上次生成于 ${history[0].week}` : '（首次生成）'}`,
  ].join('\n');

  await client.execute(
    'INSERT INTO report_history (id, week, summary) VALUES (?, ?, ?) ON DUPLICATE KEY UPDATE summary = VALUES(summary)',
    [createHash('sha1').update(week).digest('hex').slice(0, 32), week, draft.slice(0, 4000)],
  );

  const counts = { done: buckets.done.length, risk: buckets.risk.length, plan: buckets.plan.length };
  return { week, prefs, counts, draft };
}

const app = express();
app.use(cors());
app.use(express.json({ limit: '4mb' }));
app.use(express.static(webRoot));

const wrap = (handler) => (req, res) => {
  handler(req, res).catch((error) => {
    console.error('[weeklysmith]', error);
    res.status(500).json({ error: error.message });
  });
};

app.get('/api/health', wrap(async (_req, res) => {
  const [{ version }] = await client.execute('SELECT VERSION() AS version');
  const [{ total }] = await client.execute('SELECT COUNT(*) AS total FROM work_notes');
  res.json({ ok: true, engine: version, database: DATABASE, currentWeek: currentWeek(), notes: Number(total) });
}));

app.post('/api/notes', wrap(async (req, res) => {
  const { text = '', week = currentWeek(), source = 'paste' } = req.body ?? {};
  if (!String(text).trim()) {
    res.status(400).json({ error: 'text 不能为空' });
    return;
  }
  const saved = await saveNotes(String(text), String(week), String(source));
  res.json({ week, saved: saved.length, notes: saved });
}));

app.get('/api/notes', wrap(async (req, res) => {
  const week = req.query.week ? String(req.query.week) : currentWeek();
  const rows = await client.execute(
    'SELECT id, content, week, kind, created_at FROM work_notes WHERE week = ? ORDER BY created_at ASC',
    [week],
  );
  const counts = rows.reduce((acc, row) => ({ ...acc, [row.kind]: (acc[row.kind] ?? 0) + 1 }), {});
  res.json({ week, total: rows.length, counts, notes: rows });
}));

app.post('/api/search', wrap(async (req, res) => {
  const { q = '', week = null } = req.body ?? {};
  if (!String(q).trim()) {
    res.status(400).json({ error: 'q 不能为空' });
    return;
  }
  const result = await searchNotes(String(q).trim(), week ? String(week) : null);
  res.json(result);
}));

app.get('/api/prefs', wrap(async (_req, res) => res.json(await getPrefs())));

app.put('/api/prefs', wrap(async (req, res) => {
  await setPrefs(req.body ?? {});
  res.json(await getPrefs());
}));

app.post('/api/report', wrap(async (req, res) => {
  const week = String(req.body?.week ?? currentWeek());
  res.json(await buildReport(week));
}));

app.use((_req, res) => res.status(404).json({ error: 'not found' }));

connect()
  .then(() => {
    app.listen(SERVER_PORT, () => {
      console.log(`[weeklysmith] http://127.0.0.1:${SERVER_PORT} -> seekdb ${HOST}:${PORT}/${DATABASE}`);
    });
  })
  .catch((error) => {
    console.error('failed to start', error);
    process.exit(1);
  });
