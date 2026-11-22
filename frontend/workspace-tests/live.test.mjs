import {test} from 'node:test';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {createInterface} from 'node:readline';
import assert from 'node:assert/strict';
import {workspace,local,root} from './dom.mjs';
import {journey} from './journey.mjs';

function apiServer() {
  const worker = spawn(process.env.PYTHON || 'python3', ['-u','frontend/workspace-tests/api_bridge.py'], {
    cwd: root, env: {...process.env, PYTHONPATH: root, METEO_FEED_OWNER: 'research',
      DEEPSEEK_API_KEY: '', YANDEX_API_KEY: '', YANDEX_FOLDER_ID: ''}, stdio: ['pipe','pipe','inherit'],
  });
  let sequence = 0;
  const pending = new Map();
  const exited = once(worker, 'exit');
  worker.on('exit', () => {
    for (const {reject} of pending.values()) reject(new Error('FastAPI bridge stopped'));
    pending.clear();
  });
  createInterface({input:worker.stdout}).on('line', line => {
    const reply = JSON.parse(line);
    const handler = pending.get(reply.id);
    pending.delete(reply.id);
    if (reply.error) handler.reject(new Error(reply.error)); else handler.resolve(reply);
  });
  async function fetch(path, options = {}) {
    const id = ++sequence;
    const body = options.body;
    const bytes = body == null ? Buffer.alloc(0) : typeof body === 'string' ?
      Buffer.from(body) : Buffer.from(await body.arrayBuffer());
    const packet = {id, path, method: options.method || 'GET', headers: Object.fromEntries(new Headers(options.headers)),
      body: bytes.toString('base64')};
    const done = new Promise((resolve, reject) => pending.set(id, {resolve,reject}));
    worker.stdin.write(JSON.stringify(packet) + '\n');
    const data = await done;
    return new Response(data.status === 204 ? null : Buffer.from(data.body,'base64'), {status:data.status, headers:data.headers});
  }
  return {fetch, async close() {worker.stdin.end(); await exited;}};
}

test('mobile live workspace drives FastAPI, permissions and real archive processing', {timeout:90_000}, async () => {
  const api = apiServer();
  const ui = await workspace('meteo/web/index.html', 'https://workspace.test/', {mobile:true});
  const {w, $, fire, wait, go} = ui;
  try {
    w.fetch = globalThis.fetch = api.fetch;
    // App-owned refresh interval belongs to the DOM window and is released on close.
    const nativeInterval = globalThis.setInterval;
    globalThis.setInterval = (...args) => nativeInterval(...args).unref();
    w.sessionStorage.setItem('meteo-token','editor-test');
    const headers = {Authorization: 'Bearer editor-test'};
    let originalId;
    ui.archiveBytes = async () => {
      originalId = (await (await api.fetch('/api/datasets',{headers})).json())[0].id;
      return (await api.fetch('/api/datasets/' + originalId + '/raw',{headers})).arrayBuffer();
    };
    await import(local('meteo/web/app.js').href);
    await journey(ui, {offline:false, mobile:true});
    const reports = await (await api.fetch('/api/reports', {headers})).json();
    const pdf = await api.fetch('/api/reports/' + reports[0].id + '/pdf', {headers});
    assert.equal(pdf.headers.get('Content-Type'), 'application/pdf');
    assert.equal(Buffer.from(await pdf.arrayBuffer()).subarray(0, 5).toString(), '%PDF-');
    const activeCard = $('[data-settings]').dataset.settings;
    $('#logout').click();
    $('#login-form').elements.token.value = 'reader-test';
    fire($('#login-form'),'submit');
    await wait(() => $('#user-name').textContent === 'business' && $('[role=tab]'),'Reader session');
    assert.ok($('#nav [data-page=import]').hidden);
    await go('catalog','[data-open]');
    assert.ok($('[data-open="' + activeCard + '"]'));
    assert.equal($('[data-settings="' + activeCard + '"]'), null);
    const reader = {Authorization:'Bearer reader-test'};
    assert.equal((await api.fetch('/api/datasets/' + activeCard + '/members',{headers:reader})).status,403);
    const upload = await api.fetch('/api/uploads?filename=unauthorized.csv',{headers:reader, method:'POST', body:'datetime,t_air'});
    assert.equal(upload.status,403);
    assert.deepEqual(ui.errors, []);
  } finally {
    ui.w.close();
    await api.close();
  }
});
