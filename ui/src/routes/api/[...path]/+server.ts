import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types';

const forward: RequestHandler = async ({ request, params, url }) => {
  // Vercel injects POS_API_URL through the UI service's private binding.
  // Local development keeps its existing direct API fallback.
  const base = (env.POS_API_URL || (env.VERCEL ? '' : 'http://127.0.0.1:8000')).replace(/\/$/, '');
  if (!base) {
    return Response.json({ error: 'service_unavailable', message: 'The POS API service binding is not configured.' }, { status: 503 });
  }
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
