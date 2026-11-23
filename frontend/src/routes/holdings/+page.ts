import { redirect } from '@sveltejs/kit';
import { resolve } from '$app/paths';
import { movedTo } from '#lib/moved.js';
import type { PageLoad } from './$types';

// Holdings is called Investments now: an old link to it still finds the page.
export const load: PageLoad = ({ url }) => {
	redirect(307, movedTo(resolve('/investments'), url, globalThis.location ?? null));
};
