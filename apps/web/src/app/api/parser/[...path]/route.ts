import {NextRequest} from 'next/server';

export const runtime = 'nodejs';
const parserBase = process.env.PAPERLIGHT_PARSER_URL ?? 'http://127.0.0.1:8000';
type Context = {params: Promise<{path: string[]}>};

async function proxy(request: NextRequest, {params}: Context) {
  const {path} = await params;
  const allowed = (path[0] === 'api' && ['documents', 'library', 'annotations', 'auth', 'admin', 'settings'].includes(path[1]))
    || (path[0] === 'parser' && path[1] === 'jobs');
  if (!allowed) return new Response('Not found', {status: 404});
  const origin = request.headers.get('origin');
  if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method)) {
    if (origin) {
      try {
        const publicOrigin = process.env.PAPERLIGHT_PUBLIC_ORIGIN;
        if (origin !== request.nextUrl.origin && origin !== publicOrigin) return Response.json({detail: 'Invalid request origin'}, {status: 403});
      } catch {return Response.json({detail: 'Invalid request origin'}, {status: 403});}
    }
  }
  const target = `${parserBase.replace(/\/$/, '')}/${path.map(encodeURIComponent).join('/')}${request.nextUrl.search}`;
  try {
    const headers = new Headers();
    const contentType = request.headers.get('content-type');
    if (contentType) headers.set('content-type', contentType);
    const cookie = request.headers.get('cookie');
    if (cookie) headers.set('cookie', cookie);
    headers.set('x-forwarded-proto', origin?.startsWith('https://') ? 'https' : request.headers.get('x-forwarded-proto') ?? request.nextUrl.protocol.replace(':', ''));
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: ['GET', 'HEAD'].includes(request.method) ? undefined : await request.arrayBuffer(),
      cache: 'no-store',
    });
    const resultHeaders = new Headers();
    const responseType = upstream.headers.get('content-type');
    if (responseType) resultHeaders.set('content-type', responseType);
    const setCookie = upstream.headers.get('set-cookie');
    if (setCookie) resultHeaders.set('set-cookie', setCookie);
    resultHeaders.set('cache-control', 'no-store');
    return new Response(upstream.body, {status: upstream.status, headers: resultHeaders});
  } catch {
    return Response.json({detail: 'Parser service unavailable'}, {status: 503});
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const PUT = proxy;
