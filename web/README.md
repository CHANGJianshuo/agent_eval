# DialAgentEval-web

React + Vite + Tailwind 前端，与 FastAPI 共用评测引擎。

## 跑起来 —— 两种模式

### 模式 A:生产模式(单端口,推荐)

打包前端,FastAPI 同时托管 API + 前端 + 报告:

```bash
pip install -e '.[web]'
cd web && npm install && npm run build
cd ..
PYTHONPATH=src python3 -m claw_eval.cli web --port 8000
```

浏览器开 **http://localhost:8000/** —— 一个端口搞定所有。

### 模式 B:开发模式(双端口,可热重载)

适合改 React 代码时用,改完 Vite 自动 reload:

```bash
# 终端 1:FastAPI
PYTHONPATH=src python3 -m claw_eval.cli web --port 8000

# 终端 2:Vite dev server(改 React 代码自动重启)
cd web && npm run dev
```

浏览器开 **http://localhost:5173/**(Vite 5173 自动 proxy /api/* 到 :8000)。

需要 **Node.js ≥ 18**。

### 静态演示

```bash
cd web
npm run build:gh             # 产 web/dist-gh/，显式启用 gh-pages 模式
```

只有这个模式展示样本数据，写操作会报错。开发和生产模式始终连接真实 API；后端不可用时显示错误，不会切换到样本数据。

### 验证

```bash
cd web
npm test                    # API 错误传播、演示模式读取和写入限制
npm run build               # TypeScript 检查 + 生产构建
```

完整浏览器回归在项目根目录执行，使用真实 React/FastAPI 和临时数据，替代全部模型调用：

```bash
pip install -e '.[dev,web,browser]'
python -m playwright install chromium
EVAL_BROWSER=1 python -m pytest -q tests/test_browser_workflows.py
```

覆盖建议生成、证据对话、差异采纳、固定用例回归，以及变量/剧本/噪音编辑、版本恢复、独立评分与多标注者隔离。CI 在前端构建后执行这些测试。

## 技术栈

- React 18 + Vite + TypeScript
- Tailwind CSS(shadcn/ui 风格,组件代码在 src/components/ui)
- React Router v6
- TanStack Query + axios(API client)
- lucide-react(图标)
