import { redirect } from '@sveltejs/kit';
import { resolve } from '$app/paths';
import type { PageLoad } from './$types';

export const load: PageLoad = ({ url }) => {
	redirect(307, resolve('/settings/categories') + url.search);
};
