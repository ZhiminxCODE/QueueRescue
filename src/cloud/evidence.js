export function cloudWatchLink(value, region) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.hostname !== region + '.console.aws.amazon.com' || url.port || url.username || url.password ||
        url.pathname !== '/cloudwatch/home' || url.searchParams.get('region') !== region ||
        !url.hash.startsWith('#logsV2:log-groups/log-group/') || !url.hash.includes('/log-events/')) return null;
    return url.href;
  } catch { return null; }
}

export function localTimeInput(seconds) {
  const date = new Date(seconds * 1000);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
}

export function evidenceWindow(from, to) {
  const params = {};
  for (const [key, value] of [['from', from], ['to', to]]) {
    if (!value) continue;
    const seconds = new Date(value).getTime() / 1000;
    if (!Number.isFinite(seconds)) throw new Error('Choose a valid lookup time.');
    params[key] = String(Math.floor(seconds));
  }
  if (params.from && params.to && (Number(params.from) > Number(params.to) || Number(params.to) - Number(params.from) > 86400))
    throw new Error('Choose an ordered lookup window of at most 24 hours.');
  return params;
}
