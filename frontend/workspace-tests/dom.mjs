import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {setTimeout as pause} from 'node:timers/promises';

const requireUI = createRequire(new URL('../ui/package.json', import.meta.url));
const {JSDOM, VirtualConsole} = requireUI('jsdom');
export const root = fileURLToPath(new URL('../../', import.meta.url));
export const local = path => new URL(path, new URL('../../', import.meta.url));

export async function workspace(file, url, {mobile = false} = {}) {
  const errors = [];
  const dom = new JSDOM(await readFile(local(file), 'utf8'), {
    url, runScripts: 'outside-only', pretendToBeVisual: true,
    virtualConsole: new VirtualConsole().on('jsdomError', error => errors.push(error.message)),
  });
  const w = dom.window;
  const d = w.document;
  // jsdom implements the DOM, but has no layout engine, canvas or viewport media queries.
  w.innerWidth = mobile ? 390 : 1280;
  w.matchMedia = query => ({matches: mobile && query.includes('max-width'), addEventListener(){}, removeEventListener(){}});
  w.HTMLElement.prototype.scrollIntoView = () => {};
  w.HTMLElement.prototype.hasPointerCapture = () => false;
  w.HTMLElement.prototype.setPointerCapture = w.HTMLElement.prototype.releasePointerCapture = () => {};
  w.ResizeObserver = class {observe(){} unobserve(){} disconnect(){}};
  w.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
    get: (_, name) => name === 'measureText' ? () => ({width: 25}) : () => {},
  });
  for (const name of ['station','start','end','step','parameter','columns','file','token','day','auto','q']) {
    Object.defineProperty(w.HTMLFormElement.prototype, name, {get(){ return this.elements.namedItem(name); }});
  }
  Object.assign(w, {Response, Headers, DecompressionStream, PointerEvent: w.PointerEvent || w.MouseEvent});
  for (const name of ['window','document','navigator','location','HTMLElement','HTMLInputElement','HTMLButtonElement',
    'HTMLSelectElement','HTMLFormElement','Element','DocumentFragment','ShadowRoot','Node','NodeFilter','MutationObserver',
    'Event','CustomEvent','KeyboardEvent','MouseEvent','PointerEvent','ResizeObserver','FormData','DOMException',
    'getComputedStyle','requestAnimationFrame','cancelAnimationFrame','matchMedia']) {
    const value = ['getComputedStyle','requestAnimationFrame','cancelAnimationFrame','matchMedia'].includes(name) ? w[name].bind(w) : w[name];
    Object.defineProperty(globalThis, name, {value, configurable:true, writable:true});
  }
  if (!url.startsWith('file:')) Object.assign(globalThis, {sessionStorage: w.sessionStorage, localStorage: w.localStorage});
  const $ = selector => d.querySelector(selector);
  const all = selector => Array.from(d.querySelectorAll(selector));
  async function wait(predicate, label) {
    for (let i = 0; i < 250; i++) {
      const found = predicate();
      if (found) return found;
      await pause(20);
    }
    assert.fail(`${label}\n${$('#notice')?.textContent}\n${$('#content')?.textContent.slice(0,600)}\n${errors.join('\n')}`);
  }
  function fire(node, type) {
    assert.ok(node, `Missing target for ${type}`);
    node.dispatchEvent(new w.Event(type, {bubbles:true, cancelable:true}));
  }
  async function go(page, selector) {
    const link = $(`#nav [data-page="${page}"]`);
    assert.ok(link && !link.hidden, `Visible navigation item '${page}'`);
    if (mobile && d.querySelector('.menu-toggle').getAttribute('aria-expanded') !== 'true')
      d.querySelector('.menu-toggle').click();
    link.click();
    if (mobile) assert.equal(d.querySelector('.menu-toggle').getAttribute('aria-expanded'), 'false');
    return wait(() => $(selector), `${page}: ${selector}`);
  }
  return {dom,w,d,errors,$,all,wait,fire,go};
}
