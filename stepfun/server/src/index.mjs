import cors from 'cors';
import express from 'express';
import { FulltextIndexConfig, Schema, SeekdbAdminClient, SeekdbClient, VectorIndexConfig } from 'seekdb';

const HOST = process.env.SEEKDB_HOST ?? '127.0.0.1';
const PORT = Number(process.env.SEEKDB_PORT ?? 2881);
const USER = process.env.SEEKDB_USER ?? 'root';
const PASSWORD = process.env.SEEKDB_PASSWORD ?? '';
const DATABASE = process.env.SEEKDB_DATABASE ?? 'step_notes';
const GATEWAY_PORT = Number(process.env.GATEWAY_PORT ?? 8790);
const DIMENSION = Number(process.env.EMBEDDING_DIMENSION ?? 512);
const ANALYZER = process.env.FULLTEXT_ANALYZER ?? 'ik';

const connectArgs = { host: HOST, port: PORT, user: USER, password: PASSWORD, database: DATABASE };
let client;

async function ensureDatabase() {
  const admin = new SeekdbAdminClient({ host: HOST, port: PORT, user: USER, password: PASSWORD });
  try {
    const databases = await admin.listDatabases(100, 0);
    if (!databases.some((db) => db.name === DATABASE)) {
      await admin.createDatabase(DATABASE);
      console.log(`[seekdb] created database ${DATABASE}`);
    }
  } finally {
    await admin.close();
  }
  client = new SeekdbClient(connectArgs);
  await client.execute('SELECT 1');
}

function schema() {
  return new Schema({
    vectorIndex: new VectorIndexConfig({ hnsw: { dimension: DIMENSION, distance: 'cosine' }, embeddingFunction: null }),
    fulltextIndex: new FulltextIndexConfig(ANALYZER, ANALYZER === 'ik' ? { ik_mode: 'smart' } : undefined),
  });
}

async function collectionHandle(name) {
  if (!/^[A-Za-z0-9_]{1,48}$/.test(name)) {
    throw Object.assign(new Error('collection name must match [A-Za-z0-9_]{1,48}'), { status: 400 });
  }
  if (await client.hasCollection(name)) {
    return client.getCollection({ name, embeddingFunction: null });
  }
  return client.createCollection({ name, schema: schema(), embeddingFunction: null });
}

const app = express();
app.use(cors({ origin: process.env.CORS_ORIGIN ?? '*' }));
app.use(express.json({ limit: '32mb' }));

const wrap = (handler) => (req, res) => {
  handler(req, res).catch((error) => {
    console.error('[gateway]', error);
    res.status(error.status ?? 500).json({ error: error.message });
  });
};

app.get('/api/health', wrap(async (_req, res) => {
  const [{ version }] = await client.execute('SELECT VERSION() AS version');
  res.json({ ok: true, engine: version, database: DATABASE, analyzer: ANALYZER, dimension: DIMENSION });
}));

app.get('/api/collections', wrap(async (_req, res) => {
  const names = (await client.listCollections({ withEmbeddingFunction: false })).map((item) => item.name);
  const collections = [];
  for (const name of names) {
    const handle = await client.getCollection({ name, embeddingFunction: null });
    collections.push({ name, count: await handle.count() });
  }
  res.json({ collections });
}));

app.post('/api/collections', wrap(async (req, res) => {
  const { name, reset = false } = req.body ?? {};
  if (reset && (await client.hasCollection(name))) {
    await client.deleteCollection(name);
  }
  const handle = await collectionHandle(name);
  res.json({ name: handle.name, count: await handle.count() });
}));

app.delete('/api/collections/:name', wrap(async (req, res) => {
  const { name } = req.params;
  if (await client.hasCollection(name)) {
    await client.deleteCollection(name);
  }
  res.json({ deleted: name });
}));

app.post('/api/documents', wrap(async (req, res) => {
  const { collection, docs, embeddings } = req.body ?? {};
  if (!Array.isArray(docs) || !Array.isArray(embeddings) || docs.length !== embeddings.length) {
    res.status(400).json({ error: 'docs and embeddings must be arrays of equal length' });
    return;
  }
  const handle = await collectionHandle(collection);
  const batchSize = 64;
  for (let start = 0; start < docs.length; start += batchSize) {
    const slice = docs.slice(start, start + batchSize);
    await handle.add({
      ids: slice.map((doc) => doc.id),
      documents: slice.map((doc) => doc.text),
      metadatas: slice.map((doc) => doc.meta ?? {}),
      embeddings: embeddings.slice(start, start + batchSize),
    });
  }
  res.json({ collection: handle.name, inserted: docs.length, total: await handle.count() });
}));

app.post('/api/search', wrap(async (req, res) => {
  const { collection, query, embedding, k = 8 } = req.body ?? {};
  const handle = await collectionHandle(collection);
  const nResults = Math.min(Math.max(Number(k) || 8, 1), 50);
  const trimmed = String(query ?? '').trim();
  const started = performance.now();

  let mode = 'hybrid';
  let result;
  if (trimmed) {
    result = await handle.hybridSearch({
      query: { whereDocument: { $contains: trimmed } },
      knn: { queryEmbeddings: [embedding] },
      rank: { rrf: { rankWindowSize: 40, rankConstant: 60 } },
      nResults,
      include: ['documents', 'metadatas', 'distances'],
    });
  } else {
    mode = 'vector';
    result = await handle.query({
      queryEmbeddings: [embedding],
      nResults,
      include: ['documents', 'metadatas', 'distances'],
    });
  }
  const ms = performance.now() - started;
  const ids = result.ids?.[0] ?? [];
  if (mode === 'hybrid' && ids.length === 0) {
    const fallback = await handle.query({
      queryEmbeddings: [embedding],
      nResults,
      include: ['documents', 'metadatas', 'distances'],
    });
    return res.json({
      mode: 'vector-fallback',
      elapsedMs: ms,
      hits: hydrate(fallback),
    });
  }
  res.json({ mode, elapsedMs: ms, hits: hydrate(result) });
}));

function hydrate(result) {
  const ids = result.ids?.[0] ?? [];
  const documents = result.documents?.[0] ?? [];
  const metadatas = result.metadatas?.[0] ?? [];
  const distances = result.distances?.[0] ?? [];
  return ids.map((id, index) => ({
    id,
    text: documents[index] ?? '',
    meta: metadatas[index] ?? {},
    distance: distances[index] ?? null,
    similarity: distances[index] == null ? null : 1 - distances[index],
  }));
}

app.use((_req, res) => res.status(404).json({ error: 'not found' }));

ensureDatabase()
  .then(() => {
    app.listen(GATEWAY_PORT, () => {
      console.log(`[gateway] http://127.0.0.1:${GATEWAY_PORT} -> seekdb ${HOST}:${PORT}/${DATABASE}`);
    });
  })
  .catch((error) => {
    console.error('failed to start gateway', error);
    process.exit(1);
  });
