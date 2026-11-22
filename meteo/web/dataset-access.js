import {humanAvatar} from './human-avatar.js';

export {humanAvatar};

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function field(label, input) {
  const node = element('label', label);
  node.append(input);
  return node;
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
  if (!dataset.can_edit && !dataset.can_manage_access) return;

  const panel = element('section', undefined, 'dataset-access');
  panel.setAttribute('aria-label', 'Настройки набора ' + dataset.name);
  root.append(panel);
  const endpoint = '/api/datasets/' + encodeURIComponent(dataset.id);

  if (dataset.can_edit) {
    const form = element('form', undefined, 'dataset-rename');
    const name = element('input');
    name.required = true;
    name.maxLength = 160;
    name.value = dataset.name;
    const save = command('Сохранить название', 'submit');
    const status = statusLine();
    form.append(field('Название набора', name), save, status);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const nextName = name.value.trim();
      if (!nextName) {
        status.textContent = 'Введите название набора.';
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

  if (!dataset.can_manage_access) return;
  const membersEndpoint = endpoint + '/members';
  const controls = element('fieldset', undefined, 'dataset-members');
  const heading = element('legend', 'Доступ к набору');
  const scope = element('select');
  for (const [value, label] of [['private', 'Только выбранные участники'], ['shared', 'Чтение всем в кабинете']]) {
    const option = element('option', label);
    option.value = value;
    scope.append(option);
  }
  const visibility = element('p', '', 'dataset-access-note');
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
    }, 'Режим чтения обновлён.');
  });
  renderScope();
  const list = element('div', undefined, 'dataset-member-list');
  const invite = element('div');
  const status = statusLine();
  controls.append(heading, field('Кому доступно чтение', scope), visibility, list, invite);
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

  async function update(action, success) {
    if (busy) return;
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
    const row = element('div', undefined, 'dataset-member-row');
    const person = element('span', undefined, 'dataset-person');
    person.append(humanAvatar(username), element('span', username, 'dataset-person-name'));
    row.append(person);
    if (role === 'owner') {
      row.append(element('span', 'Владелец', 'dataset-owner-role'));
      return row;
    }
    const actions = element('div', undefined, 'dataset-member-actions');
    const select = roleSelector(role);
    select.setAttribute('aria-label', 'Роль участника ' + username);
    const memberUrl = membersEndpoint + '/' + encodeURIComponent(username);
    select.addEventListener('change', () => {
      const nextRole = select.value;
      select.value = role;
      update(() => api(memberUrl, {method: 'PUT', json: {role: nextRole}}), 'Роль обновлена.');
    });
    const remove = command('Отозвать', 'button', 'dataset-revoke');
    remove.setAttribute('aria-label', 'Отозвать доступ у ' + username);
    remove.addEventListener('click', () => update(() => api(memberUrl, {method: 'DELETE'}),
      dataset.visibility === 'shared' ? 'Роль отозвана. Общее чтение сохранено.' : 'Доступ отозван.'));
    actions.append(select, remove);
    row.append(actions);
    return row;
  }

  function invitation(candidates) {
    const form = element('form', undefined, 'dataset-invite');
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
      update(() => api(url, {method: 'PUT', json: {role: role.value}}), 'Доступ предоставлен.');
    });
    return form;
  }

  status.textContent = 'Загрузка участников…';
  load();
}
