import { isHttpError, isRedirect } from '@sveltejs/kit';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { errorMessage, get, getWithToken, post, send } from './api.js';

describe('API client helpers', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('reads JSON with optional query parameters, redirects on 401, and throws HttpError on 4xx', async () => {
		vi.stubGlobal('location', {
			pathname: '/app/accounts',
			search: '?owner=Anna',
			assign: vi.fn()
		});
		const okFetch = vi.fn(async () => new Response(JSON.stringify({ value: 42 }), { status: 200 }));
		expect(
			await get<{ value: number }>(okFetch, '/api/x', new URLSearchParams('owner=Anna'))
		).toEqual({ value: 42 });
		expect(okFetch).toHaveBeenCalledWith('/api/x?owner=Anna');

		const unauth = vi.fn(async () => new Response('', { status: 401 }));
		try {
			await get(unauth, '/api/x');
			expect.unreachable();
		} catch (caught) {
			expect(isRedirect(caught)).toBe(true);
		}

		const bad = vi.fn(
			async () => new Response(JSON.stringify({ errors: ['Bad field.'] }), { status: 400 })
		);
		try {
			await get(bad, '/api/x');
			expect.unreachable();
		} catch (caught) {
			expect(isHttpError(caught, 400)).toBe(true);
		}
	});

	it('sends JSON or FormData with X-Token, handles 401/403/network errors, and extracts errorMessage', async () => {
		const assign = vi.fn();
		vi.stubGlobal('location', { pathname: '/app/settings', search: '', assign });

		vi.stubGlobal(
			'fetch',
			vi.fn(async () => new Response(JSON.stringify({ count: 3, errors: [] }), { status: 200 }))
		);
		const tested = await getWithToken<{ count: number }>('/api/settings/rules/test?match=A', 'tok');
		expect(tested).toEqual({ ok: true, status: 200, sent: { count: 3, errors: [] } });
		expect(errorMessage(tested)).toBeNull();

		vi.stubGlobal(
			'fetch',
			vi.fn(async () => new Response('Forbidden', { status: 403 }))
		);
		const stale = await send('/api/x', { a: 1 }, 'tok');
		expect(stale.ok).toBe(false);
		expect(errorMessage(stale)).toContain('reload');

		vi.stubGlobal(
			'fetch',
			vi.fn(async () => new Response('', { status: 401 }))
		);
		const signedOut = await send('/api/x', new FormData(), 'tok');
		expect(signedOut.status).toBe(401);
		expect(assign).toHaveBeenCalledWith('/login?next=%2Fapp%2Fsettings');

		vi.stubGlobal(
			'fetch',
			vi.fn(async () => {
				throw new TypeError('Failed to fetch');
			})
		);
		const offline = await getWithToken('/api/x', 'tok');
		expect(offline.status).toBe(0);
		expect(errorMessage(offline)).toContain('could not be reached');

		vi.stubGlobal(
			'fetch',
			vi.fn(async () => new Response(JSON.stringify({ errors: [] }), { status: 409 }))
		);
		await expect(post('/api/x', {}, 'tok')).rejects.toThrow('The change was refused.');
	});
});
