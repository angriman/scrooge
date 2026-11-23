import { error, redirect } from '@sveltejs/kit';

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

/** POST `body` as JSON to `path` of the API, with the token of these pages (`Session.token`)
 * that a change needs. A change refused is an error with the API's sentences. */
export async function post<T>(path: string, body: unknown, token: string): Promise<T> {
	const response = await fetch(path, {
		method: 'POST',
		headers: { 'content-type': 'application/json', 'x-token': token },
		body: JSON.stringify(body)
	});
	if (response.status === 401) {
		location.assign(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
	}
	const sent = await response.json().catch(() => ({}));
	if (!response.ok) {
		throw new Error(
			(sent.errors ?? []).join(' ') ||
				'The change was refused. Reload the page and try again.'
		);
	}
	return sent as T;
}
