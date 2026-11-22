// Original geometric portraits. The seed only chooses artwork, never account permissions.
const palettes = [
  ['#f6d7ad', '#eb9f69', '#553c48', '#396e77', '#8fc8bb'],
  ['#d5dcb7', '#eab579', '#4a403a', '#407357', '#a9ca8d'],
  ['#e2d8e9', '#dda078', '#473447', '#7565a0', '#b5a3d7'],
  ['#badbd7', '#ce8660', '#433d4e', '#cc7561', '#f0b990'],
  ['#efcabb', '#f1be8f', '#614256', '#5e70a1', '#a9bbcc'],
  ['#edd88e', '#b97458', '#443541', '#437571', '#8fb7a1'],
];
const haircuts = [
  '<path d="M13 24c-1-10 2-15 11-15 7 0 11 6 11 13l-4-5c-3 1-8 0-11-2-1 4-3 7-7 9Z"/>',
  '<path d="M13 23c-2-3-2-6 0-8-2-4 2-7 5-6 2-5 7-4 9-2 4-1 7 1 7 4 5 2 4 8 0 11l-3-5c-4 3-10 3-15 0Z"/>',
  '<path d="M12 24v-5c0-7 5-12 12-12s12 5 12 12v7l-3-2-1-6c-5 0-10-1-13-4 0 3-3 6-7 10Z"/>',
  '<path d="M13 24v-5c0-6 5-10 11-10s11 4 11 10v6l-3-2v-5H16v5Z"/><path d="M11 17c0-3 2-5 4-6 0-4 4-6 8-5 4-2 9 1 10 5 2 1 4 3 4 6Z"/>',
  '<path d="M12 23c-1-5 0-11 5-14 0-4 4-5 8-4 6-1 11 4 11 11l-1 8-3-3v-5c-6 1-11-1-14-3-1 3-3 7-6 10Z"/>',
];
const collars = [
  '<path d="m17 35 7 5 7-5-3 10-4-3-4 3Z"/>',
  '<path d="m16 36 3-3 5 5 5-5 3 3-4 7-4-4-4 4Z"/>',
  '<path d="M16 35q8 8 16 0l1 3q-9 9-18 0Z"/>',
];

export function humanAvatar(username) {
  let seed = 2166136261;
  for (const letter of String(username ?? '').normalize('NFC')) {
    seed = Math.imul(seed ^ letter.codePointAt(0), 16777619) >>> 0;
  }
  const [background, skin, ink, jacket, trim] = palettes[seed % palettes.length];
  const hair = haircuts[(seed >>> 8) % haircuts.length];
  const collar = collars[(seed >>> 16) % collars.length];
  const glasses = (seed >>> 24) % 4 === 0
    ? '<path d="M16 23h6v2q-3 3-6 0Zm10 0h6v2q-3 3-6 0Zm-4 1h4" fill="none" stroke-width="1.3"/>'
    : '';
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">
    <rect width="48" height="48" fill="${background}"/>
    <circle cx="39" cy="5" r="16" fill="#fff" opacity=".22"/>
    <path d="M0 48c2-8 7-12 17-14h14c10 2 15 6 17 14" fill="${jacket}"/>
    <path d="M20 29h8v6l-4 4-4-4Z" fill="${skin}"/>
    <path d="M20 30h8v2c-2 3-6 3-8 1Z" fill="${ink}" opacity=".2"/>
    <path d="M14 21c-4-2-4 5 0 6h1q2 6 9 7 7-1 9-7h1c4-1 4-8 0-6v-3c0-7-5-9-10-9s-10 2-10 9Z" fill="${skin}"/>
    <g fill="${ink}">${hair}<circle cx="19.5" cy="24" r=".9"/><circle cx="28.5" cy="24" r=".9"/></g>
    <g stroke="${ink}" stroke-linecap="round">${glasses}<path d="M22 29q2 1.4 4 0" fill="none" stroke-width="1"/></g>
    <g fill="${trim}">${collar}</g>
  </svg>`;
  const image = document.createElement('img');
  image.className = 'human-avatar';
  image.alt = '';
  image.width = image.height = 34;
  image.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
  return image;
}
