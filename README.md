# 智能采集调度平台

面向多源网页采集、增量抓取、自动化流程编排、采集结果校验与失败重试的智能数据采集调度平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 采集引擎 | `collector` | 采集任务 | 任务编号、目标网址、HTTP方法 |
| 页面抽取规则 | `crawlrule` | 抽取规则 | 规则编号、入口地址、列表选择器 |
| 内容解析器 | `parser` | 解析模板 | 模板编号、目标字段名、XPath表达式 |
| 增量采集 | `increment` | 增量游标 | 游标编号、来源标识、增量字段 |
| 限速调度 | `ratelimit` | 限速策略 | 策略编号、目标域名、并发上限 |
| 失败重试 | `retry` | 重试策略 | 策略编号、失败类型、最大重试次数 |
| 自动化流程 | `rpaflow` | 流程编排 | 流程编号、入口URL、页面定位器 |
| 验证码识别 | `captcha` | 识别方案 | 方案编号、验证码类型、识别服务地址 |
| 代理池管理 | `proxy` | 代理节点 | 节点编号、代理地址、代理协议 |
| 会话状态 | `session` | 会话配置 | 配置编号、登录网址、Cookie策略 |
| 链路分发 | `router` | 分发规则 | 规则编号、来源URL模式、目标引擎 |
| 采集校验 | `validate` | 校验规则 | 规则编号、校验目标字段、校验表达式 |
| 去重过滤 | `dedup` | 去重策略 | 策略编号、业务键列、相似度算法 |
| 格式归一 | `format` | 转换规则 | 规则编号、输入字段、输出字段 |
| 采集流水线 | `pipeline` | 流水线编排 | 流水线编号、阶段顺序、阶段名称 |
| 采集存储 | `storage` | 存储配置 | 配置编号、存储类型、连接地址 |
| 采集监控 | `monitor` | 监控指标 | 指标编号、监控目标、采集频率 |
| 定时调度 | `scheduler` | 调度计划 | 计划编号、Cron表达式、关联任务 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 解析模板与格式归一的扩展字段治理

内容解析器（parser）与格式归一（format）的登记不再只复制必填字段，页面声明的
扩展字段会完整写入并在登记响应、列表、详情、导出中完整读回。

### 字段口径

| 模块 | 必填字段（页面可编辑） | 扩展字段（页面可编辑） | 服务端维护字段（禁止写入） |
| --- | --- | --- | --- |
| 内容解析器 | 模板编号、目标字段名、XPath表达式 | 正则模式、默认取值、字段类型、必填标识、清洗函数 | id、status、pending、abnormal、version、confirmed_version、revisions、legacy |
| 格式归一 | 规则编号、输入字段、输出字段 | 转换函数、默认取值、异常策略、格式化模板、校验正则、解析模板编号、引用解析版本 | 同上 |

- 未在字段声明内的 payload 键一律忽略；提交 id、status、version 等服务端字段不会覆盖原值。
- `0`、`False`、空串是合法的显式取值，会原样保留；只有字段**缺失**（旧记录）才按
  `app/services/versioning.py` 里的 `FIELD_DEFAULTS` 在视图层补齐，并以 `legacy: true`
  标记，仓库原始记录不被改写。

### 接口（在原登记接口基础上新增，POST `/api/parser`、`/api/format` 保持不变）

- `PUT /api/parser/{id}`、`PUT /api/format/{id}`：编辑可修改的扩展字段，
  body 的 `values` 必须带 `expected_version`；版本过期返回 `409` 且不留半份修改，
  缺少版本号返回 `400`。
- `GET /api/parser/{id}/revisions`、`GET /api/format/{id}/revisions`：版本历史，
  每次编辑生成一份含完整扩展字段快照的新版本，可追溯。
- 动作接口 `POST /api/{module}/{id}/actions` 支持携带 `expected_version` 做乐观锁校验。

### 主配置裁决（解析模板为主，格式归一只能引用）

- 解析模板执行「启用模板」后，当前版本成为已确认版本（`confirmed_version`）。
- 归一规则只能引用解析模板**已确认**的字段版本（`解析模板编号 + 引用解析版本`），
  引用未确认/不存在的版本在登记或编辑时返回 `409`。
- 归一规则「提交校验/启用规则」前做确定性冲突裁决：同一目标字段的默认取值、
  清洗/转换函数口径、必填约束不一致时阻断确认，`409` 响应的 `detail.conflict`
  同时给出 `parser_source` 与 `format_source` 双方来源及逐条原因。
- 解析模板后续出新版本不影响已登记的归一规则：归一丝毫钉在引用时的已确认版本上，
  两个模块不会相互覆盖。
- 旧记录（无版本链）已启用的解析模板视同 v1 已确认；旧记录首次编辑会补一份
  v1 历史快照后再升版本。

### 前端页面

`/parser` 与 `/format` 两页提供登记弹窗、扩展字段编辑弹窗（只读展示 id/状态/版本，
服务端字段不可改）、详情抽屉（含版本历史）；格式归一页面的引用下拉只列出已确认的
解析模板版本，冲突阻断时在页内展示双方来源面板。

### 验证

```bash
cd backend
python3 -m pytest tests/ -q          # 20 个用例：丢字段复现/往返、旧数据兼容、零值与空串、并发拒绝、冲突裁决
bash tests/e2e_smoke.sh             # 真实 HTTP 冒烟（需先启动 uvicorn，默认 http://127.0.0.1:8000）
```

领域：数据工程
子方向：数据采集、爬虫与RPA
