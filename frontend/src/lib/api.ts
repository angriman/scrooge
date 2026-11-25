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

/** POST `body` to `path` of the API, as JSON or as the form it is, with the token of these
 * pages (`Session.token`) that a change needs. Not signed in, the browser goes to the password
 * page and comes back. */
export async function send<T>(path: string, body: unknown, token: string): Promise<Answer<T>> {
	const form = body instanceof FormData;
	let response: Response;
	try {
		response = await fetch(path, {
			method: 'POST',
			headers: form
				? { 'x-token': token }
				: { 'content-type': 'application/json', 'x-token': token },
			body: form ? body : JSON.stringify(body)
		});
	} catch {
		const errors = [t('api.unreachable')];
		return { ok: false, status: 0, sent: { errors } as T & { errors: string[] } };
	}
	if (response.status === 401) {
		location.assign(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
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

/** POST `body` as JSON to `path` of the API with the token of these pages; a change refused
 * is an error with the API's sentences. */
export async function post<T>(path: string, body: unknown, token: string): Promise<T> {
	const answer = await send<T>(path, body, token);
	if (!answer.ok) {
		throw new Error(answer.sent.errors?.join(' ') || t('api.changeRefused'));
	}
	return answer.sent;
}
