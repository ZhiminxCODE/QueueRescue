function handler(event) {
  var request = event.request;
  if (request.uri === '/' || request.uri === '/index.html' || request.uri === '/callback/') {
    return {
      statusCode: 302,
      statusDescription: 'Found',
      headers: { location: { value: '/callback' }, 'cache-control': { value: 'no-store' } }
    };
  }
  if (request.uri === '/callback') request.uri = '/index.html';
  return request;
}
