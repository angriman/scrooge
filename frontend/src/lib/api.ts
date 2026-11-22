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
