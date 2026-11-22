import {applyUIPrimitives} from './ui-primitives.js';
import {humanAvatar} from './human-avatar.js';

export {humanAvatar};

let nextFieldId = 0;

// Native DOM equivalents of shadcn Field, FieldLabel and FieldDescription inside Radix Dialog.
// The workspace adapter supplies the shared NativeSelect trigger and indicator.
function slot(node, name) {
  node.dataset.slot = name;
  return node;
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function field(title, input, description) {
  const group = slot(element('div'), 'field');
  group.setAttribute('role', 'group');
  group.dataset.orientation = 'vertical';
  const label = slot(element('label', title), 'field-label');
  input.id = 'dataset-field-' + ++nextFieldId;
  label.htmlFor = input.id;
  group.append(label, input);
  if (description) {
    description.id = input.id + '-hint';
    input.setAttribute('aria-describedby', description.id);
    group.append(description);
  }
  return group;
}

function roleSelector(current = 'reader') {
  const select = element('select');
  for (const [value, label] of [['reader', 'Читатель'], ['editor', 'Редактор']]) {
    const option = element('option', label);
    option.value = value;
    select.append(option);
  }
  select.value = current;
  return select;
}

function command(label, type = 'button', className = '') {
  const button = element('button', label, className);
  button.type = type;
  button.dataset.size = 'sm';
  button.dataset.variant = className === 'primary' ? 'default' : className === 'dataset-revoke' ? 'ghost' : 'outline';
  applyUIPrimitives(button);
  return button;
}

function statusLine() {
  const live = element('p', '', 'dataset-access-status');
  live.setAttribute('role', 'status');
  live.setAttribute('aria-live', 'polite');
  return live;
}

export function mountDatasetSettings(root, dataset, api, {onRename, onVisibility} = {}) {
  root.replaceChildren();
  const canEdit = dataset.can_edit === true;
  const canManageAccess = dataset.can_manage_access === true;
  if (!canEdit && !canManageAccess) return;

  const panel = element('section', undefined, 'dataset-access');
  panel.setAttribute('aria-label', 'Настройки набора ' + dataset.name);
  root.append(panel);
  const endpoint = '/api/datasets/' + encodeURIComponent(dataset.id);

  if (canEdit) {
    const form = slot(element('form', undefined, 'dataset-rename'), 'field-group');
    const name = element('input');
    applyUIPrimitives(name);
    name.required = true;
    name.maxLength = 160;
    name.value = dataset.name;
    const save = command('Сохранить название', 'submit');
    const status = statusLine();
    const fieldError = slot(element('p'), 'field-error');
    fieldError.setAttribute('role', 'alert');
    const nameField = field('Название набора', name, fieldError);
    function validateName() {
      const valid = Boolean(name.value.trim());
      nameField.dataset.invalid = String(!valid);
      name.setAttribute('aria-invalid', String(!valid));
      fieldError.textContent = valid ? '' : 'Введите название набора.';
      return valid;
    }
    name.addEventListener('invalid', validateName);
    name.addEventListener('input', () => {
      if (nameField.dataset.invalid === 'true') validateName();
    });
    form.append(nameField, save, status);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const nextName = name.value.trim();
      if (!validateName()) {
        name.focus();
        return;
      }
      if (save.disabled) return;
      save.disabled = name.disabled = true;
      status.textContent = 'Сохранение…';
      try {
        const updated = await api(endpoint, {method: 'PATCH', json: {name: nextName}});
        name.value = updated.name;
        if (root.firstChild === panel) onRename?.(updated.name);
        status.textContent = 'Название сохранено.';
      } catch (error) {
        status.textContent = error.message;
      } finally {
        save.disabled = name.disabled = false;
      }
    });
    panel.append(form);
  }

  if (!canManageAccess) return;
  const membersEndpoint = endpoint + '/members';
  const controls = slot(element('fieldset', undefined, 'dataset-members'), 'field-set');
  const heading = slot(element('legend', 'Доступ к набору'), 'field-legend');
  heading.dataset.variant = 'legend';
  const scope = element('select');
  for (const [value, label] of [['private', 'Только выбранные участники'], ['shared', 'Чтение всем в кабинете']]) {
    const option = element('option', label);
    option.value = value;
    scope.append(option);
  }
  const visibility = slot(element('p', '', 'dataset-access-note'), 'field-description');
  function renderScope() {
    scope.value = dataset.visibility;
    visibility.textContent = dataset.visibility === 'shared'
      ? 'Набор доступен всем пользователям для чтения. Редактор может менять название и фотографии станций.'
      : 'Доступ есть у владельца и выбранных участников. Редактор может менять название и фотографии станций.';
  }
  scope.addEventListener('change', () => {
    const shared = scope.value === 'shared';
    renderScope();
    update(async () => {
      const result = await api(endpoint + '/visibility?shared=' + shared, {method: 'PUT'});
      dataset.visibility = result.visibility;
      renderScope();
      if (root.firstChild === panel) onVisibility?.(result.visibility);
    }, 'Режим чтения обновлён.', scope);
  });
  renderScope();
  const list = slot(element('div', undefined, 'dataset-member-list'), 'item-group');
  list.setAttribute('role', 'list');
  list.setAttribute('aria-label', 'Участники набора');
  const invite = element('div');
  const status = statusLine();
  controls.append(heading, field('Кому доступно чтение', scope, visibility), list, invite);
  panel.append(controls, status);

  let busy = false;
  async function refresh(message = '') {
    const data = await api(membersEndpoint);
    if (root.firstChild !== panel) return;
    list.replaceChildren(personRow(data.owner, 'owner'));
    for (const {username, role} of data.members) list.append(personRow(username, role));
    const assigned = new Set([data.owner, ...data.members.map(member => member.username)]);
    const available = data.candidates.filter(username => !assigned.has(username));
    invite.replaceChildren();
    if (available.length) invite.append(invitation(available));
    status.textContent = message;
  }

  async function update(action, success, source, focusUser) {
    if (busy) return;
    const restoreFocus = source === document.activeElement;
    busy = controls.disabled = true;
    status.textContent = 'Сохранение доступа…';
    try {
      await action();
    } catch (error) {
      status.textContent = error.message;
      busy = controls.disabled = false;
      return;
    }
    await load(success);
    if (restoreFocus && panel.isConnected && (!document.activeElement || document.activeElement === document.body || document.activeElement === source)) {
      const next = Array.from(list.querySelectorAll('[data-member-role]'))
        .find(control => control.dataset.memberRole === focusUser);
      (next || (source.isConnected && source) || invite.querySelector('select,button') || scope).focus();
    }
  }

  async function load(message = '') {
    busy = controls.disabled = true;
    try {
      await refresh(message);
    } catch (error) {
      list.replaceChildren();
      const retry = command('Обновить список');
      retry.addEventListener('click', () => load());
      invite.replaceChildren(retry);
      status.textContent = error.message;
    } finally {
      busy = controls.disabled = false;
    }
  }

  function personRow(username, role) {
    const row = slot(element('div', undefined, 'dataset-member-row'), 'item');
    row.setAttribute('role', 'listitem');
    row.dataset.size = 'sm';
    row.dataset.variant = 'default';
    const media = slot(element('span'), 'item-media');
    media.dataset.variant = 'image';
    media.append(humanAvatar(username));
    const person = slot(element('span', undefined, 'dataset-person'), 'item-content');
    person.append(slot(element('span', username, 'dataset-person-name'), 'item-title'));
    row.append(media, person);
    if (role === 'owner') {
      const badge = element('span', 'Владелец', 'dataset-owner-role');
      badge.dataset.variant = 'secondary';
      applyUIPrimitives(badge);
      row.append(badge);
      return row;
    }
    const actions = slot(element('div', undefined, 'dataset-member-actions'), 'item-actions');
    const select = roleSelector(role);
    select.setAttribute('aria-label', 'Роль участника ' + username);
    select.dataset.memberRole = username;
    const memberUrl = membersEndpoint + '/' + encodeURIComponent(username);
    select.addEventListener('change', () => {
      const nextRole = select.value;
      select.value = role;
      update(() => api(memberUrl, {method: 'PUT', json: {role: nextRole}}), 'Роль обновлена.', select, username);
    });
    const remove = command('Отозвать', 'button', 'dataset-revoke');
    remove.setAttribute('aria-label', 'Отозвать доступ у ' + username);
    remove.addEventListener('click', () => update(() => api(memberUrl, {method: 'DELETE'}),
      dataset.visibility === 'shared' ? 'Роль отозвана. Общее чтение сохранено.' : 'Доступ отозван.', remove));
    actions.append(select, remove);
    row.append(actions);
    return row;
  }

  function invitation(candidates) {
    const form = slot(element('form', undefined, 'dataset-invite'), 'field-group');
    const people = element('select');
    for (const username of candidates) {
      const option = element('option', username);
      option.value = username;
      people.append(option);
    }
    const role = roleSelector();
    const add = command('Дать доступ', 'submit', 'primary');
    form.append(field('Участник', people), field('Роль', role), add);
    form.addEventListener('submit', event => {
      event.preventDefault();
      const url = membersEndpoint + '/' + encodeURIComponent(people.value);
      update(() => api(url, {method: 'PUT', json: {role: role.value}}), 'Доступ предоставлен.', add, people.value);
    });
    return form;
  }

  status.textContent = 'Загрузка участников…';
  load();
}
