import {readFile, writeFile, mkdir, readdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';

const directory = path.dirname(fileURLToPath(import.meta.url));
const vendor = path.resolve(directory, '../../meteo/web/vendor');
await mkdir(vendor, {recursive: true});
const {metafile} = await build({
  absWorkingDir: directory, entryPoints: ['index.jsx'], bundle: true, minify: true, format: 'esm',
  jsx: 'automatic', target: 'es2020', charset: 'utf8', metafile: true, legalComments: 'none',
  supported: {'template-literal': false},
  define: {'process.env.NODE_ENV': '"production"'},
  banner: {js: '/*! shadcn/ui · Radix UI · React. See dataset-dialog.LICENSE.txt */'},
  outfile: path.join(vendor, 'dataset-dialog.js'),
});

const packages = new Set();
for (const input of Object.keys(metafile.inputs)) {
  const match = input.match(/(?:^|\/)node_modules\/((?:@[^/]+\/)?[^/]+)\//);
  if (match) packages.add(match[1]);
}
const notices = ['shadcn/ui — https://github.com/shadcn-ui/ui\n' +
  await readFile(path.join(directory, 'LICENSE.shadcn'), 'utf8')];
for (const name of [...packages].sort()) {
  const dep = path.join(directory, 'node_modules', name);
  const manifest = JSON.parse(await readFile(path.join(dep, 'package.json'), 'utf8'));
  const files = await readdir(dep);
  const filename = files.find(file => /^licen[sc]e(?:\.\w+)?$/i.test(file));
  let notice;
  if (filename) {
    notice = await readFile(path.join(dep, filename), 'utf8');
  } else if (name === 'react-remove-scroll-bar' && manifest.license === 'MIT') {
    // This published tarball declares MIT and an author, but omits its license file.
    const mit = await readFile(path.join(directory, 'LICENSE.shadcn'), 'utf8');
    notice = mit.replace('Copyright (c) 2023 shadcn', 'Copyright (c) ' + manifest.author);
  } else {
    throw new Error('Missing license for ' + name);
  }
  if (files.includes('CopyrightNotice.txt')) notice += '\n' + await readFile(path.join(dep, 'CopyrightNotice.txt'), 'utf8');
  notices.push(name + ' ' + manifest.version + '\n' + notice);
}
await writeFile(path.join(vendor, 'dataset-dialog.LICENSE.txt'), notices.map(text => text.replaceAll('\r\n', '\n').trimEnd()).join('\n\n') + '\n');

await build({
  absWorkingDir: directory, entryPoints: ['components/form-controls.jsx'], bundle: true, format: 'esm',
  target: 'es2020', legalComments: 'none', banner: {js: '/*! Native form controls. See frontend/ui/components/form-controls.jsx for source and attribution. */'},
  outfile: path.resolve(vendor, '../form-controls.js'),
});
