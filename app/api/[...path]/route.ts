import { NextRequest, NextResponse } from 'next/server'

const endpoints: Record<string, 'GET' | 'POST'> = {
  'state': 'GET',
  'spin': 'POST',
  'buy': 'POST',
  'topup': 'POST',
  'deposit': 'POST',
}

async function forward(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params
  const endpoint = path.join('/')
  const method = endpoints[endpoint]

  if (!method || request.method !== method) {
    return NextResponse.json({ error: 'not_found' }, { status: 404 })
  }

  const backendUrl = process.env.GIFT_API_URL
  if (!backendUrl) {
    return NextResponse.json({ error: 'backend_not_configured' }, { status: 503 })
  }

  let origin: URL
  try {
    origin = new URL(backendUrl)
  } catch {
    return NextResponse.json({ error: 'backend_configuration_invalid' }, { status: 503 })
  }

  if (origin.protocol !== 'https:' && !(process.env.NODE_ENV !== 'production' && origin.hostname === 'localhost')) {
    return NextResponse.json({ error: 'backend_configuration_invalid' }, { status: 503 })
  }

  try {
    const upstream = await fetch(new URL(`/api/${endpoint}`, origin), {
      method,
      headers: {
        'X-Init': request.headers.get('X-Init') || '',
        ...(method === 'POST' ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(method === 'POST' ? { body: await request.text() } : {}),
      cache: 'no-store',
      redirect: 'error',
      signal: AbortSignal.timeout(10_000),
    })
    const payload = await upstream.text()
    return new NextResponse(payload, {
      status: upstream.status,
      headers: { 'Content-Type': upstream.headers.get('Content-Type') || 'application/json' },
    })
  } catch {
    return NextResponse.json({ error: 'backend_unavailable' }, { status: 502 })
  }
}

export const GET = forward
export const POST = forward
