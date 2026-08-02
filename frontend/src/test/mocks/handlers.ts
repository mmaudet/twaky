import { http, HttpResponse } from 'msw'

// Default handlers used across tests. Override per-test with server.use(...).
export const handlers = [
    http.get('/api/me', () =>
        HttpResponse.json({
            owner_email: 'alice@x',
            langfuse_base_url: 'https://langfuse.example.com',
        }),
    ),
    http.get('/api/missions', () => HttpResponse.json([])),
]
