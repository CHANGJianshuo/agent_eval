"""FastAPI 后端层 —— 把现有 Python 业务逻辑暴露成 HTTP API。

挂载点:
  GET  /api/tasks                       任务列表
  POST /api/tasks                       新建任务(异步触发 generate-task)
  DELETE /api/tasks/{id}                删除任务
  GET  /api/tasks/{id}                  任务详情
  GET  /api/tasks/{id}/tests            该任务的测试历史
  POST /api/tasks/{id}/tests            启动新测试
  GET  /api/tests/{id}                  单测试详情
  GET  /api/tasks/{id}/recommendations  改进建议
  POST /api/tasks/{id}/apply            自动应用建议
  GET  /api/persona-library             5 维度属性字典
  GET  /api/config/models               模型配置
  POST /api/config/models               改模型配置
  POST /api/config/api-key              保存 API key
  POST /api/config/test-connection      测试 LLM 连接

启动:claw-eval web --port 8000
Swagger:http://localhost:8000/docs
"""
