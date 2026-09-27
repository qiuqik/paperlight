import {NextRequest} from 'next/server';

export const runtime = 'nodejs';
const parserBase = process.env.PAPERLIGHT_PARSER_URL ?? 'http://127.0.0.1:8000';
type Context = {params: Promise<{path: string[]}>};

async function proxy(request: NextRequest, {params}: Context) {
  const {path} = await params;
  if (!path.length || path[0] !== 'api' || !['documents', 'library'].includes(path[1])) return new Response('Not found', {status: 404});
  const target = `${parserBase.replace(/\/$/, '')}/${path.map(encodeURIComponent).join('/')}${request.nextUrl.search}`;
  try {
    const headers = new Headers();
    const contentType = request.headers.get('content-type');
    if (contentType) headers.set('content-type', contentType);
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === 'GET' ? undefined : await request.arrayBuffer(),
      cache: 'no-store',
    });
    const resultHeaders = new Headers();
    const responseType = upstream.headers.get('content-type');
    if (responseType) resultHeaders.set('content-type', responseType);
    return new Response(upstream.body, {status: upstream.status, headers: resultHeaders});
  } catch {
    return Response.json({detail: 'Parser service unavailable'}, {status: 503});
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
