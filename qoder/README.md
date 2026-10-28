# 周报匠 WeeklySmith

把一周里零散的工作笔记、会议纪要，整理成一份可以直接发出去的周报草稿；同时把「署名、语气」这类偏好写进 seekdb，下次生成自动带上。

- 解决的问题：每周五从聊天记录、待办、会议纪要里手工拼周报，重复、耗时、容易漏掉风险项
- 核心能力：一行一条随手记 → 自动归类为「完成 / 风险 / 计划」→ seekdb 全文检索（ik 中文分词）快速找回某条记录 → 一键生成本周周报
- 技术栈：Node.js + Express + [seekdb-js](https://github.com/oceanbase/seekdb-js) 1.4.0 + seekdb（兼容 MySQL 协议）；前端为原生 HTML/CSS/ESM，无需构建
- 在线展示页（Qoder Sites 发布，公开可访问）：https://weeklysmith-f6der1wjfj6.qoder.zone

## 目录

```
qoder/
├── README.md              # 本文件：运行说明与验证样例
├── docs/DEVLOG.md         # Qoder 开发记录（需求拆解、实现、验证、迭代）
├── server/
│   ├── src/index.mjs      # API：写入、检索、偏好记忆、周报生成
│   ├── package.json
│   └── .env.example       # 占位配置，不包含任何真实凭据
├── web/                   # 单页应用（由 Express 直接托管）
├── samples/
│   ├── notes.json         # 两周共 12 条示例笔记
│   └── seed.mjs           # 通过 API 灌入示例数据与偏好
└── site/                  # 项目展示页（Qoder Sites 发布源）
```

## 运行说明

### 1. 准备 seekdb

任选一种部署方式，记下 host / port / user / password（默认 `127.0.0.1:2882`，`root`，空密码）。

```bash
docker run -d --name seekdb -p 2882:2882 oceanbase/seekdb:latest
```

确认可连接：

```bash
mysql -h 127.0.0.1 -P 2882 -u root -e "SELECT VERSION();"
```

### 2. 启动服务

```bash
cd qoder/server
cp .env.example .env          # 按实际 seekdb 实例修改
npm install --legacy-peer-deps # seekdb-js 的 @seekdb/default-embed 为 peer 依赖，本项目不需要
npm start                     # http://127.0.0.1:8891
```

> 首次启动会自动创建数据库 `weeklysmith` 与三张表（`work_notes` / `user_prefs` / `report_history`）。

### 3. 灌入示例数据并打开页面

```bash
cd qoder
node samples/seed.mjs http://127.0.0.1:8891
# 浏览器打开 http://127.0.0.1:8891
```

## 验证样例（可按步骤复现）

| 步骤 | 操作 | 预期结果 |
| --- | --- | --- |
| 1 | 页面顶部徽标 | `seekdb：seekdb-v1.x`、`笔记：12` |
| 2 | 周次填 `2026-W42`，点「刷新」 | 列出 7 条笔记，标签为 完成 3 / 风险 2 / 计划 2 |
| 3 | 检索框输入 `缓存` 并回车 | 命中 1 条：「缓存服务 P99 偶发 1.2s，阻塞联调，需要运维协助」，状态栏显示 seekdb 毫秒数 |
| 4 | 点「生成本周周报」 | 草稿包含「本周完成 / 风险与阻塞 / 下周计划」三节，落款为 `示例同学`（来自偏好记忆） |
| 5 | 把署名改成 `小王`、语气选「正式」，点「保存偏好」 | 底部偏好栏更新；再次生成周报，落款与语气说明随之变化 |
| 6 | 在左侧粘贴一条新笔记「把周报归档接入了日程提醒，已上线」并写入 | 徽标笔记数 +1，左侧列表立刻出现该条（标签：完成） |

指定技术发挥作用的记录：

```sql
-- 全文检索（页面步骤 3 对应的库内查询）
SELECT id, content, MATCH(content) AGAINST('缓存') AS score
FROM weeklysmith.work_notes
WHERE MATCH(content) AGAINST('缓存') AND week = '2026-W42';

-- 偏好记忆（页面步骤 4/5 复用）
SELECT pref_key, pref_value, updated_at FROM weeklysmith.user_prefs;

-- 周报生成历史（用于「上次生成于」提示）
SELECT week, created_at FROM weeklysmith.report_history ORDER BY created_at DESC LIMIT 3;
```

## 数据表

```sql
CREATE TABLE work_notes (
  id VARCHAR(64) NOT NULL PRIMARY KEY,
  content TEXT NOT NULL,
  week VARCHAR(16) NOT NULL,
  kind VARCHAR(16) NOT NULL,           -- done / risk / plan
  source VARCHAR(32) NOT NULL DEFAULT 'paste',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FULLTEXT KEY idx_fts (content) WITH PARSER ik PARSER_PROPERTIES=(ik_mode='smart')
);

CREATE TABLE user_prefs (pref_key VARCHAR(64) PRIMARY KEY, pref_value VARCHAR(255), updated_at DATETIME);
CREATE TABLE report_history (id VARCHAR(64) PRIMARY KEY, week VARCHAR(16), summary TEXT, created_at DATETIME);
```

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | seekdb 版本、当前周、笔记总数 |
| POST | `/api/notes` | 写入笔记（逐行去重，按关键词自动归类） |
| GET | `/api/notes?week=` | 列出某周笔记与三类计数 |
| POST | `/api/search` | seekdb 全文检索（`MATCH ... AGAINST` + 周过滤），返回相关度与耗时 |
| GET / PUT | `/api/prefs` | 偏好记忆读写 |
| POST | `/api/report` | 按周生成周报草稿，并写入生成历史 |

## 已知限制

- 归类用关键词规则，不做语义理解；后续可接入 embedding（seekdb 向量索引已具备）。
- 单用户本地部署，API 未做鉴权，请勿直接暴露公网。
- 配置示例均为占位值，仓库不包含任何真实凭据。
