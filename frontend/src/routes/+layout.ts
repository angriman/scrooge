import { get } from '#lib/api.js';
import { setLanguage } from '#lib/i18n/index.svelte.js';
import type { Session } from '#lib/types.js';
import type { LayoutLoad } from './$types';

// Nothing is rendered on a server: Python serves the built page, and the data comes from /api.
export const ssr = false;
export const prerender = false;

export const load: LayoutLoad = async ({ fetch }) => {
	const session = await get<Session>(fetch, '/api/session');
	setLanguage(session.language);
	return { session };
};
