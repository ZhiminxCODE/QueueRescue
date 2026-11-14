let token = sessionStorage.getItem('qr-token');

export async function api(path, data) {
  const response = await fetch('/api' + path, {
    method: data === undefined ? 'GET' : 'POST',
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(data === undefined ? {} : { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }),
    },
    ...(data === undefined ? {} : { body: JSON.stringify(data) }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}

export async function login(role) {
  const session = await api('/demo/session', { role });
  token = session.token;
  sessionStorage.setItem('qr-token', token);
  sessionStorage.setItem('qr-role', role);
  return session.actor;
}

export function query(values) {
  return '?' + new URLSearchParams(Object.entries(values).filter(([, v]) => v !== '' && v !== undefined && v !== null));
}

export function download(value, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
