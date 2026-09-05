import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { createServer } from 'vite'

async function loadAPI(mode) {
  const server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    configFile: false,
    mode,
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true, watch: null },
  })
  try {
    return await server.ssrLoadModule('/src/lib/api.ts')
  } finally {
    await server.close()
  }
}

test('a backend outage stays an error, and the next request can recover', async () => {
  const { api, TasksAPI, isDemo } = await loadAPI('production')
  assert.equal(isDemo, false)
  api.defaults.adapter = async () => { throw new Error('Backend unavailable') }
  await assert.rejects(TasksAPI.list(), /Backend unavailable/)

  const tasks = [{ task_id: 'real_task' }]
  api.defaults.adapter = async config => {
    assert.equal(config.url, '/tasks')
    return { data: tasks, status: 200, statusText: 'OK', headers: {}, config }
  }
  assert.deepEqual(await TasksAPI.list(), tasks)

  api.defaults.adapter = async () => { throw new Error('Invalid rubric') }
  await assert.rejects(TasksAPI.updateRubrics('real_task', []), /Invalid rubric/)
})

test('the explicit demo serves samples and rejects writes without network requests', async () => {
  const { api, TasksAPI, TestsAPI, ConfigAPI, PersonaLibAPI, isDemo } = await loadAPI('gh-pages')
  assert.equal(isDemo, true)
  let requests = 0
  api.defaults.adapter = async () => { requests++; throw new Error('Unexpected request') }
  assert.ok((await TasksAPI.list()).length > 0)

  const writes = [
    () => TasksAPI.create({ task_id: 'demo', prompt: 'Prompt' }),
    () => TasksAPI.remove('demo'),
    () => TasksAPI.updatePrompt('demo', 'Changed'),
    () => TasksAPI.updateRubrics('demo', []),
    () => TasksAPI.approve('demo', true, []),
    () => TestsAPI.start('demo', { total: 1 }),
    () => PersonaLibAPI.save([]),
    () => ConfigAPI.updateModels({}),
    () => ConfigAPI.saveKey('provider', 'test-key'),
    () => ConfigAPI.testConnection('provider'),
    () => api.post('/tasks/demo/agent-chat', { messages: [] }),
  ]
  for (const write of writes) await assert.rejects(write(), /Demo 模式/)
  assert.equal(requests, 0)
})

test('test artifacts and preview seed are scoped to the selected run', async () => {
  const { api, TestsAPI, JobsAPI } = await loadAPI('production')
  const requests = []
  api.defaults.adapter = async config => {
    requests.push(config)
    return { data: {}, status: 200, statusText: 'OK', headers: {}, config }
  }
  await TestsAPI.recommendations('run_one')
  await TestsAPI.flow('run_one')
  await TestsAPI.previewPersonas('task', { attitude: { cooperative: 1 } }, 12, 42)
  await TestsAPI.start('task', { test_id: 'run_one', total: 12, seed: 42 })
  await JobsAPI.cancel('test_run_one')
  assert.deepEqual(requests.map(r => r.url), [
    '/tests/run_one/recommendations', '/tests/run_one/flow', '/tasks/task/preview-personas',
    '/tasks/task/tests', '/jobs/test_run_one/cancel',
  ])
  assert.equal(JSON.parse(requests[2].data).seed, 42)
  assert.equal(JSON.parse(requests[3].data).seed, 42)
})
