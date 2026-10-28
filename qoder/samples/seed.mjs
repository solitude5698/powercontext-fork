// Seed sample notes through the HTTP API so the data flows through the same
// classify + insert path the UI uses.
// Usage: node samples/seed.mjs [baseUrl]

import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const baseUrl = process.argv[2] ?? process.env.SERVER_URL ?? 'http://127.0.0.1:8891';
const here = dirname(fileURLToPath(import.meta.url));
const samples = JSON.parse(await readFile(join(here, 'notes.json'), 'utf8'));

for (const [week, lines] of Object.entries(samples)) {
  const response = await fetch(`${baseUrl}/api/notes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: lines.join('\n'), week, source: 'seed' }),
  });
  const result = await response.json();
  console.log(`[seed] ${week}: saved ${result.saved} notes`);
}

const prefs = await fetch(`${baseUrl}/api/prefs`, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ author_name: '示例同学', tone: '简洁' }),
});
console.log('[seed] prefs', await prefs.json());
