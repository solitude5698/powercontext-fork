const GATEWAY = import.meta.env.VITE_GATEWAY ?? 'http://127.0.0.1:8790';
const DEFAULT_COLLECTION = '我的笔记';
const params = new URLSearchParams(location.search);
const BATCH_SIZE = Number(params.get('batch')) > 0 ? Number(params.get('batch')) : 8;

const el = (id) => document.getElementById(id);
const state = { collection: null, worker: null, ready: null, backend: null, dtype: null };

function setStatus(text) {
  el('status').textContent = text;
}

async function api(path, options) {
  const response = await fetch(GATEWAY + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error ?? response.statusText);
  return payload;
}

function detectDevice() {
  const forced = params.get('device');
  if (forced === 'webgpu' || forced === 'wasm') return forced;
  return 'gpu' in navigator ? 'webgpu' : 'wasm';
}

function startWorker() {
  if (state.worker) return state.ready;
  state.worker = new Worker(new URL('./worker.js', import.meta.url), { type: 'module' });
  state.ready = new Promise((resolve, reject) => {
    state.worker.onmessage = (event) => {
      const message = event.data;
      if (message.type === 'status') {
        setStatus(message.phase === 'fallback' ? 'WebGPU 不可用，正在降级到 WASM…' : '正在加载模型…');
      } else if (message.type === 'progress') {
        const { status, loaded, total, file } = message.event ?? {};
        if (status === 'progress' && total) {
          const percent = Math.round((loaded / total) * 100);
          el('progress-fill').style.width = `${percent}%`;
          el('progress-text').textContent = `模型 ${file ?? ''} ${percent}%`;
        }
      } else if (message.type === 'ready') {
        state.backend = message.device;
        state.dtype = message.dtype;
        window.__readyInfo = { device: message.device, dtype: message.dtype, loadMs: message.loadMs };
        el('badge-backend').textContent = `推理后端：${message.device === 'webgpu' ? 'WebGPU' : 'WASM'}`;
        el('badge-dtype').textContent = `精度：${message.dtype === 'q8' ? 'INT8' : message.dtype.toUpperCase()}`;
        el('progress-fill').style.width = '100%';
        el('progress-text').textContent = message.loadMs
          ? `模型已就绪（加载 ${(message.loadMs / 1000).toFixed(1)} s）`
          : '模型已就绪';
        setStatus('模型加载完成');
        resolve(message);
      } else if (message.type === 'result') {
        pending.get(message.id)?.(message);
      } else if (message.type === 'batch') {
        const percent = Math.round((message.done / message.total) * 100);
        el('progress-fill').style.width = `${percent}%`;
        el('progress-text').textContent = `向量化 ${message.done}/${message.total}`;
      } else if (message.type === 'error') {
        if (message.id && pending.has(message.id)) {
          pending.get(message.id)({ error: message.message });
        } else {
          reject(new Error(message.message));
        }
        setStatus(`错误：${message.message}`);
      }
    };
    state.worker.postMessage({ type: 'init', device: detectDevice() });
  });
  return state.ready;
}

const pending = new Map();
let sequence = 0;

function embed(texts, kind = 'document') {
  const id = ++sequence;
  return new Promise((resolve) => {
    pending.set(id, (message) => {
      pending.delete(id);
      resolve(message);
    });
    state.worker.postMessage({ type: 'embed', id, texts, kind, batchSize: BATCH_SIZE });
  });
}

async function refreshCollections(selectName) {
  const { collections } = await api('/api/collections');
  const select = el('collection-select');
  select.innerHTML = '';
  for (const item of collections) {
    const option = document.createElement('option');
    option.value = item.name;
    option.textContent = `${item.name}（${item.count}）`;
    select.appendChild(option);
  }
  state.collection = selectName ?? collections[0]?.name ?? null;
  if (state.collection) select.value = state.collection;
  return collections;
}

async function ensureCollection() {
  const { collections } = await api('/api/collections');
  if (collections.length === 0) {
    await api('/api/collections', {
      method: 'POST',
      body: JSON.stringify({ name: DEFAULT_COLLECTION }),
    });
  }
  await refreshCollections(state.collection);
  state.collection = el('collection-select').value;
}

function splitNotes(text) {
  return text
    .split(/\n\s*\n|\n/)
    .map((line) => line.trim())
    .filter((line) => line.length >= 6);
}

function hashCode(text) {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16);
}

async function importNotes() {
  const notes = splitNotes(el('notes').value);
  if (notes.length === 0) {
    setStatus('没有可导入的笔记');
    return;
  }
  const button = el('import');
  button.disabled = true;
  const started = performance.now();
  try {
    setStatus(`正在向量化 ${notes.length} 条笔记…`);
    await startWorker();
    const embedded = await embed(notes, 'document');
    if (embedded.error) throw new Error(embedded.error);
    const embedMs = performance.now() - started;
    setStatus('写入 seekdb…');
    const docs = notes.map((text, index) => ({
      id: hashCode(`${state.collection}:${index}:${text}`),
      text,
      meta: { source: 'paste', importedAt: new Date().toISOString() },
    }));
    const result = await api('/api/documents', {
      method: 'POST',
      body: JSON.stringify({ collection: state.collection, docs, embeddings: embedded.embeddings }),
    });
    await refreshCollections(state.collection);
    el('timing').textContent = `向量化 ${Math.round(embedMs)} ms · 写入 ${result.inserted} 条`;
    setStatus(`已写入 ${result.inserted} 条，当前共 ${result.total} 条`);
  } catch (error) {
    setStatus(`导入失败：${error.message}`);
  } finally {
    button.disabled = false;
  }
}

async function search() {
  const query = el('query').value.trim();
  if (!query) return;
  const button = el('search');
  button.disabled = true;
  const started = performance.now();
  try {
    await startWorker();
    const embedded = await embed([query], 'query');
    if (embedded.error) throw new Error(embedded.error);
    const embedMs = performance.now() - started;
    const result = await api('/api/search', {
      method: 'POST',
      body: JSON.stringify({
        collection: state.collection,
        query,
        embedding: embedded.embeddings[0],
        k: 8,
      }),
    });
    render(result);
    el('search-mode').textContent =
      result.mode === 'vector' ? '向量检索' : result.mode === 'vector-fallback' ? '向量回退（全文无命中）' : '混合检索（seekdb RRF）';
    el('search-timing').textContent = `向量化 ${Math.round(embedMs)} ms · 检索 ${result.elapsedMs.toFixed(1)} ms`;
    el('timing').textContent = `端到端 ${Math.round(performance.now() - started)} ms`;
  } catch (error) {
    setStatus(`检索失败：${error.message}`);
  } finally {
    button.disabled = false;
  }
}

function render(result) {
  const list = el('results');
  list.innerHTML = '';
  if (!result.hits?.length) {
    const empty = document.createElement('li');
    empty.className = 'empty';
    empty.textContent = '没有命中，换个说法试试，或先导入一些笔记。';
    list.appendChild(empty);
    return;
  }
  result.hits.forEach((hit, index) => {
    const item = document.createElement('li');
    const head = document.createElement('div');
    head.className = 'hit-head';
    head.innerHTML = `<span class="rank">#${index + 1}</span><span class="score">${hit.similarity == null ? '' : `相似度 ${hit.similarity.toFixed(3)}`}</span>`;
    const text = document.createElement('p');
    text.textContent = hit.text;
    const meta = document.createElement('small');
    meta.textContent = Object.entries(hit.meta ?? {})
      .map(([key, value]) => `${key}: ${value}`)
      .join(' · ');
    item.append(head, text, meta);
    list.appendChild(item);
  });
}

async function bootstrap() {
  try {
    const health = await api('/api/health');
    el('badge-gateway').textContent = `seekdb：${health.engine.replace('5.7.25-OceanBase ', '')}`;
  } catch (error) {
    el('badge-gateway').textContent = 'seekdb：未连接';
    setStatus(`后端未启动：${error.message}`);
  }
  try {
    await ensureCollection();
  } catch (error) {
    setStatus(`知识库初始化失败：${error.message}`);
  }
}

el('import').addEventListener('click', importNotes);
el('search').addEventListener('click', search);
el('query').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') search();
});
el('clear-notes').addEventListener('click', () => {
  el('notes').value = '';
});
el('demo').addEventListener('click', () => {
  el('notes').value = [
    'HNSW 用分层可导航小世界图组织向量，查询时从稀疏层逐层下沉，把近邻检索的复杂度降到对数级。',
    'WebGPU 允许浏览器直接调度 GPU，Transformers.js 在支持 WebGPU 的环境下会优先把 ONNX 计算放到显卡上执行。',
    'INT8 量化把权重从 32 位浮点压缩到 8 位整数，模型体积约缩小到四分之一，端侧加载更快。',
    'seekdb 兼容 MySQL 协议，同时提供向量索引和全文索引，可以在一次查询里完成混合检索。',
    'BM25 依靠词频和逆文档频率打分，擅长精确关键词；向量检索擅长语义相近但用词不同的情况。',
    'RRF（倒数排名融合）把两路检索的名次转换为分数相加，无需对齐两路分值的量纲。',
    '写周报时先列本周完成的三件事，再补风险和下周计划，最后压到一页以内。',
    '考试前复习错题优先看错因归纳，而不是重新做一遍已经会的题。',
  ].join('\n');
});
el('collection-select').addEventListener('change', (event) => {
  state.collection = event.target.value;
  el('results').innerHTML = '';
  setStatus(`已切换到知识库 ${state.collection}`);
});
el('collection-new').addEventListener('click', async () => {
  const name = prompt('新知识库名称（字母、数字、下划线）', '笔记_' + Date.now().toString().slice(-4));
  if (!name) return;
  try {
    await api('/api/collections', { method: 'POST', body: JSON.stringify({ name }) });
    await refreshCollections(name);
    setStatus(`已创建知识库 ${name}`);
  } catch (error) {
    setStatus(`创建失败：${error.message}`);
  }
});
el('collection-reset').addEventListener('click', async () => {
  if (!state.collection || !confirm(`清空知识库 ${state.collection}？`)) return;
  await api('/api/collections', {
    method: 'POST',
    body: JSON.stringify({ name: state.collection, reset: true }),
  });
  await refreshCollections(state.collection);
  el('results').innerHTML = '';
  setStatus(`已清空 ${state.collection}`);
});
el('collection-delete').addEventListener('click', async () => {
  if (!state.collection || !confirm(`删除知识库 ${state.collection}？`)) return;
  await api(`/api/collections/${state.collection}`, { method: 'DELETE' });
  await ensureCollection();
  el('results').innerHTML = '';
  setStatus(`已删除 ${state.collection}`);
});

bootstrap();
