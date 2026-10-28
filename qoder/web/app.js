const el = (id) => document.getElementById(id);
const state = { week: null };

function setStatus(text) {
  el('status').textContent = text;
}

async function api(path, options) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error ?? response.statusText);
  return payload;
}

const KIND_LABEL = { done: '完成', risk: '风险', plan: '计划' };

async function bootstrap() {
  try {
    const health = await api('/api/health');
    el('badge-engine').textContent = `seekdb：${health.engine.replace('5.7.25-OceanBase ', '')}`;
    el('badge-week').textContent = `本周：${health.currentWeek}`;
    state.week = health.currentWeek;
    el('week').value = health.currentWeek;
    await refreshNotes();
    await refreshPrefs();
  } catch (error) {
    el('badge-engine').textContent = 'seekdb：未连接';
    setStatus(`后端未启动：${error.message}`);
    return;
  }

  const params = new URLSearchParams(location.search);
  const week = params.get('week');
  if (week) {
    el('week').value = week;
    await refreshNotes().catch(() => {});
  }
  const q = params.get('q');
  if (q) {
    el('query').value = q;
    el('search').click();
  }
  if (params.get('report')) el('report').click();
}

async function refreshNotes() {
  const week = el('week').value.trim() || state.week;
  const data = await api(`/api/notes?week=${encodeURIComponent(week)}`);
  state.week = data.week;
  el('badge-notes').textContent = `笔记：${data.total}`;
  const list = el('note-list');
  list.innerHTML = '';
  if (data.notes.length === 0) {
    const empty = document.createElement('li');
    empty.className = 'empty';
    empty.textContent = '本周还没有笔记，先在上方写下几条。';
    list.appendChild(empty);
    return;
  }
  for (const note of data.notes) {
    const item = document.createElement('li');
    const tag = document.createElement('span');
    tag.className = `tag ${note.kind}`;
    tag.textContent = KIND_LABEL[note.kind] ?? note.kind;
    const text = document.createElement('span');
    text.textContent = note.content;
    item.append(tag, text);
    list.appendChild(item);
  }
}

async function refreshPrefs() {
  const prefs = await api('/api/prefs');
  el('author').value = prefs.author_name ?? '';
  if (prefs.tone) el('tone').value = prefs.tone;
  el('prefs').textContent = `偏好：${prefs.author_name ?? '未设置署名'} · ${prefs.tone ?? '简洁'}`;
}

el('demo').addEventListener('click', () => {
  el('notes').value = [
    '修复检索结果分页错位的问题，已回归并通过',
    '完成周报模板的可配置化改造，交付设计稿',
    '缓存服务 P99 偶发 1.2s，阻塞联调，需要运维协助',
    '灰度环境数据与线上不一致，存在风险',
    '下周补齐检索链路回归用例，目标覆盖分页与空结果',
    '计划把会议纪要导入做成批量粘贴，减少手工整理',
  ].join('\n');
});

el('clear-input').addEventListener('click', () => {
  el('notes').value = '';
});

el('save').addEventListener('click', async () => {
  const text = el('notes').value.trim();
  if (!text) {
    setStatus('没有可写入的内容');
    return;
  }
  const button = el('save');
  button.disabled = true;
  const started = performance.now();
  try {
    const week = el('week').value.trim() || state.week;
    const result = await api('/api/notes', {
      method: 'POST',
      body: JSON.stringify({ text, week, source: 'pwa' }),
    });
    el('save-status').textContent = `已写入 ${result.saved} 条（${Math.round(performance.now() - started)} ms）`;
    setStatus(`写入 ${result.saved} 条到 ${result.week}`);
    el('notes').value = '';
    await refreshNotes();
  } catch (error) {
    setStatus(`写入失败：${error.message}`);
  } finally {
    button.disabled = false;
  }
});

el('load').addEventListener('click', () => {
  refreshNotes().catch((error) => setStatus(`加载失败：${error.message}`));
});

el('search').addEventListener('click', async () => {
  const q = el('query').value.trim();
  if (!q) return;
  el('search-status').textContent = '检索中…';
  try {
    const result = await api('/api/search', {
      method: 'POST',
      body: JSON.stringify({ q, week: el('week').value.trim() || null }),
    });
    el('search-status').textContent = `命中 ${result.hits.length} 条（MATCH AGAINST + week 过滤）`;
    el('search-timing').textContent = `seekdb ${result.elapsedMs.toFixed(1)} ms`;
    const list = el('results');
    list.innerHTML = '';
    if (result.hits.length === 0) {
      const empty = document.createElement('li');
      empty.className = 'empty';
      empty.textContent = '没有命中，换个关键词试试。';
      list.appendChild(empty);
      return;
    }
    for (const [index, hit] of result.hits.entries()) {
      const item = document.createElement('li');
      const head = document.createElement('div');
      head.className = 'hit-head';
      head.innerHTML = `<span class="rank">#${index + 1}</span><span class="score">相关度 ${Number(hit.score).toFixed(3)}</span>`;
      const text = document.createElement('p');
      text.textContent = hit.content;
      const meta = document.createElement('small');
      meta.textContent = `${hit.week} · ${KIND_LABEL[hit.kind] ?? hit.kind}`;
      item.append(head, text, meta);
      list.appendChild(item);
    }
  } catch (error) {
    el('search-status').textContent = `检索失败：${error.message}`;
  }
});

el('query').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') el('search').click();
});

el('save-prefs').addEventListener('click', async () => {
  try {
    const prefs = await api('/api/prefs', {
      method: 'PUT',
      body: JSON.stringify({ author_name: el('author').value.trim(), tone: el('tone').value }),
    });
    el('prefs').textContent = `偏好：${prefs.author_name ?? '未设置署名'} · ${prefs.tone ?? '简洁'}`;
    setStatus('偏好已保存到 seekdb，下次生成周报会自动带上');
  } catch (error) {
    setStatus(`保存偏好失败：${error.message}`);
  }
});

el('report').addEventListener('click', async () => {
  try {
    const week = el('week').value.trim() || state.week;
    const result = await api('/api/report', { method: 'POST', body: JSON.stringify({ week }) });
    el('draft').textContent = result.draft;
    el('status').textContent = `周报已生成：完成 ${result.counts.done} / 风险 ${result.counts.risk} / 计划 ${result.counts.plan}`;
  } catch (error) {
    setStatus(`生成失败：${error.message}`);
  }
});

el('copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(el('draft').textContent);
    setStatus('已复制到剪贴板');
  } catch {
    setStatus('浏览器拒绝了剪贴板写入，请手动选中复制');
  }
});

bootstrap();
