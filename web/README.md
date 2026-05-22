# claw-eval-web

React + Vite + Tailwind 现代前端,替代之前的 Streamlit UI。

## 跑起来

需要 **Node.js ≥ 18**(WSL 自带的 v12 不够,用 Windows 端的 Node 24)。

### 第一次

```bash
cd web
npm install                  # 装依赖,~1-2 分钟
```

### 启动开发服

**两个进程**:

**后端**(Python,负责业务逻辑 + API):
```bash
# 在仓库根目录
pip install -e '.[web]'      # 一次性,装 fastapi + uvicorn
PYTHONPATH=src python3 -m claw_eval.cli web --port 8000
```

**前端**(React,负责 UI):
```bash
cd web
npm run dev                  # 起在 :5173,自动 proxy /api/* 到 :8000
```

打开浏览器 `http://localhost:5173/`。

后端的 Swagger:`http://localhost:8000/docs`。

### 生产构建(可选)

```bash
cd web
npm run build                # 产 web/dist/
```

然后只跑后端,FastAPI 同端口托管前端静态文件:
```bash
PYTHONPATH=src python3 -m claw_eval.cli web --port 8000
```

打开 `http://localhost:8000/`。

## 技术栈

- React 18 + Vite + TypeScript
- Tailwind CSS(shadcn/ui 风格,组件代码在 src/components/ui)
- React Router v6
- TanStack Query + axios(API client)
- lucide-react(图标)

## 当前完成度

✅ Phase 1: 后端 API(25 个 endpoints,Swagger 完整)
✅ Phase 2: 前端框架 + 任务列表页 + 新建任务表单

下轮(Phase 3):任务概览页 + 测试详情页(报告嵌入 / 建议自动应用 / 回归对比)
