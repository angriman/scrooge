import { redirect } from '@sveltejs/kit';
import { resolve } from '$app/paths';
import { movedTo } from '#lib/moved.js';
import type { PageLoad } from './$types';

// Statements is called Accounts now: an old link to it (to its checks too) still finds the page.
export const load: PageLoad = ({ url }) => {
	redirect(307, movedTo(resolve('/accounts'), url, globalThis.location ?? null));
};
