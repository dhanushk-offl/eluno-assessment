import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types';

const forward: RequestHandler = async ({ request, params, url }) => {
  const base = (env.POS_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
  try {
    const response = await globalThis.fetch(`${base}/${params.path}${url.search}`, {
      method: request.method,
      headers: { 'content-type': 'application/json' },
      body: request.method === 'GET' || request.method === 'HEAD' ? undefined : await request.text()
    });
    return new Response(response.body, { status: response.status, headers: { 'content-type': response.headers.get('content-type') || 'application/json' } });
  } catch {
    return Response.json({ error: 'service_unavailable', message: 'The POS service is unavailable. Check the connection and try again.' }, { status: 503 });
  }
};
export const GET = forward;
export const POST = forward;
