import { error, redirect } from '@sveltejs/kit';
import { t } from '#lib/i18n/index.svelte.js';

/** GET `path` of the API as JSON. Not signed in, the browser goes to the password page and
 * comes back; a field the API cannot read is an error page with its sentences. */
export async function get<T>(
	fetch: typeof globalThis.fetch,
	path: string,
	params?: URLSearchParams
): Promise<T> {
	const query = params?.size ? `?${params}` : '';
	const response = await fetch(path + query);
	if (response.status === 401) {
		const back = location.pathname + location.search;
		redirect(303, `/login?next=${encodeURIComponent(back)}`);
	}
	const sent = await response.json();
	if (!response.ok) {
		error(response.status, (sent.errors ?? []).join(' ') || response.statusText);
	}
	return sent as T;
}

/** What a change answered: whether it was made, and what the API sent either way. */
export interface Answer<T> {
	ok: boolean;
	status: number;
	sent: T & { errors?: string[] };
}

async function request<T>(path: string, init: RequestInit): Promise<Answer<T>> {
	let response: Response;
	try {
		response = await fetch(path, init);
	} catch {
		const errors = [t('api.unreachable')];
		return { ok: false, status: 0, sent: { errors } as T & { errors: string[] } };
	}
	if (response.status === 401) {
		location.assign(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
		const errors = [t('api.signedOut')];
		return { ok: false, status: 401, sent: { errors } as T & { errors: string[] } };
	}
	const sent = await response.json().catch(() => ({
		errors: [
			response.status === 403
				? t('api.stalePage')
				: t('api.unexpectedStatus', { status: response.status })
		]
	}));
	return { ok: response.ok, status: response.status, sent };
}

/** GET `path` of the API with the token of these pages (`Session.token`). */
export function getWithToken<T>(path: string, token: string): Promise<Answer<T>> {
	return request<T>(path, { headers: { 'x-token': token } });
}

/** POST `body` to `path` of the API, as JSON or as the form it is, with the token of these
 * pages (`Session.token`) that a change needs. Not signed in, the browser goes to the password
 * page and comes back. */
export function send<T>(path: string, body: unknown, token: string): Promise<Answer<T>> {
	const form = body instanceof FormData;
	return request<T>(path, {
		method: 'POST',
		headers: form ? { 'x-token': token } : { 'content-type': 'application/json', 'x-token': token },
		body: form ? body : JSON.stringify(body)
	});
}

/** The error sentences of a refused `Answer`, or `fallback` when none were sent; null when
 * `answer.ok`. */
export function errorMessage(
	answer: Answer<unknown>,
	fallback: string = t('api.changeRefused')
): string | null {
	if (answer.ok) return null;
	return (answer.sent.errors ?? []).join(' ') || fallback;
}

/** POST `body` as JSON to `path` of the API with the token of these pages; a change refused
 * is an error with the API's sentences. */
export async function post<T>(path: string, body: unknown, token: string): Promise<T> {
	const answer = await send<T>(path, body, token);
	const problem = errorMessage(answer);
	if (problem !== null) throw new Error(problem);
	return answer.sent;
}
