// Native DOM adaptations of the shadcn/ui new-york-v4 registry (MIT).
// Sources: https://ui.shadcn.com/r/styles/new-york-v4/{button,input,textarea,label,badge,card,table,collapsible,alert}.json
// Copyright (c) 2023 shadcn. See frontend/ui/LICENSE.shadcn.
const mounts = new WeakMap();
const tableSlots = {
  TABLE: 'table', THEAD: 'table-header', TBODY: 'table-body', TFOOT: 'table-footer',
  TR: 'table-row', TH: 'table-head', TD: 'table-cell', CAPTION: 'table-caption',
};
const targets = [
  'button', 'a.button', 'input', 'textarea', 'label', '.tag', '.dataset-owner-role',
  '.panel', '.panel > :is(h2,h3)', '.table-wrap', 'table', 'thead', 'tbody',
  'tfoot', 'tr', 'th', 'td', 'caption', 'details', 'details > summary', '#notice', '#login-error',
].join(',');
const inputTypes = new Set(['text', 'search', 'tel', 'url', 'email', 'password', 'date',
  'month', 'week', 'time', 'datetime-local', 'number', 'file']);
const badgeTones = new Set(['neutral', 'success', 'warning', 'danger']);
const badgeVariants = new Set(['default', 'secondary', 'outline', 'destructive', 'ghost', 'link']);
const entities = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};

export function badgeHTML(label, {tone = 'neutral', variant = 'outline'} = {}) {
  const safeTone = badgeTones.has(tone) ? tone : 'neutral';
  const safeVariant = badgeVariants.has(variant) ? variant : 'outline';
  const safeLabel = String(label ?? '').replace(/[&<>"']/g, char => entities[char]);
  return `<span data-ui-native data-slot="badge" data-tone="${safeTone}" data-variant="${safeVariant}">${safeLabel}</span>`;
}

function buttonVariant(node) {
  if (node.classList.contains('dataset-revoke')) return 'destructive';
  if (node.classList.contains('text')) return 'link';
  if (node.classList.contains('primary') || node.classList.contains('workspace-create')) return 'default';
  if (node.classList.contains('icon-button') || node.closest('nav')) return 'ghost';
  return 'outline';
}

function reflectDisclosure(details) {
  const state = details.open ? 'open' : 'closed';
  details.dataset.state = state;
  const trigger = details.querySelector(':scope > [data-slot=collapsible-trigger]');
  if (trigger) trigger.dataset.state = state;
}

function reflectAlert(node) {
  if (node.dataset.slot && !node.hasAttribute('data-ui-native')) return;
  if (node.textContent.trim()) {
    node.dataset.slot = 'alert';
    node.dataset.variant = node.id === 'login-error' ? 'destructive' : 'default';
    node.setAttribute('data-ui-native', '');
  } else if (node.dataset.slot === 'alert') {
    delete node.dataset.slot;
    delete node.dataset.variant;
    node.removeAttribute('data-ui-native');
  }
}

// Enhance the original control in place so form values, native listeners, shortcut
// handling and custom Atlas/Map presentation continue to belong to their owner.
function attachPrimitive(node) {
  if (node.id === 'notice' || node.id === 'login-error') {
    reflectAlert(node);
    return;
  }
  if (node.hasAttribute('data-slot')) return;
  let slot = tableSlots[node.tagName];
  switch (node.tagName) {
    case 'BUTTON':
    case 'A':
      slot = 'button';
      node.dataset.variant ||= buttonVariant(node);
      break;
    case 'INPUT':
      if (!inputTypes.has(node.type)) return;
      slot = 'input';
      break;
    case 'TEXTAREA': slot = 'textarea'; break;
    case 'LABEL': slot = 'label'; break;
    case 'DETAILS':
      slot = 'collapsible';
      reflectDisclosure(node);
      break;
    case 'SUMMARY':
      slot = 'collapsible-trigger';
      node.dataset.state = node.parentElement.open ? 'open' : 'closed';
      break;
    case 'TH':
      if (!node.hasAttribute('scope') && node.closest('thead')) node.scope = 'col';
      break;
    default:
      if (slot) break;
      if (node.matches('.tag,.dataset-owner-role')) {
        slot = 'badge';
        node.dataset.tone ||= node.classList.contains('warn') ? 'warning' : 'neutral';
        node.dataset.variant ||= 'outline';
      } else if (node.classList.contains('panel')) {
        slot = 'card';
      } else if (node.classList.contains('table-wrap')) {
        slot = 'table-container';
      } else if (node.matches('h2,h3')) {
        slot = 'card-title';
      }
  }
  if (slot) {
    node.dataset.slot = slot;
    node.setAttribute('data-ui-native', '');
  }
}

export function applyUIPrimitives(root = document) {
  if (root.matches?.(targets)) attachPrimitive(root);
  root.querySelectorAll(targets).forEach(attachPrimitive);
}

export function mountUIPrimitives(root = document) {
  if (mounts.has(root)) return mounts.get(root);
  applyUIPrimitives(root);
  const onToggle = event => {
    if (event.target.dataset?.slot === 'collapsible') reflectDisclosure(event.target);
  };
  root.addEventListener('toggle', onToggle, true);
  const observer = new MutationObserver(records => {
    for (const record of records) {
      const parent = record.type === 'characterData' ? record.target.parentElement : record.target;
      if (parent?.id === 'notice' || parent?.id === 'login-error') reflectAlert(parent);
      for (const node of record.addedNodes) {
        if (node.nodeType === 1 && root.contains(node)) applyUIPrimitives(node);
      }
    }
  });
  observer.observe(root, {childList: true, characterData: true, subtree: true});
  const dispose = () => {
    observer.disconnect();
    root.removeEventListener('toggle', onToggle, true);
    if (mounts.get(root) === dispose) mounts.delete(root);
  };
  mounts.set(root, dispose);
  return dispose;
}
