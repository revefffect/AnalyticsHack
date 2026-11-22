import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {workspace,local} from './dom.mjs';
import {journey} from './journey.mjs';

test('the published standalone HTML works on file origin through all seven sections', async () => {
  const file = 'docs/demo/station-atlas.html';
  const ui = await workspace(file, local(file).href);
  try {
    assert.equal(ui.all('script[src],link[rel=stylesheet]').length, 0);
    for (const script of ui.all('script:not([type]),script[type="text/javascript"]')) {
      await new vm.Script(script.textContent, {
        importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER,
      }).runInContext(ui.dom.getInternalVMContext());
    }
    await journey(ui, {offline:true, mobile:false});
  } finally {
    ui.w.close();
  }
});
