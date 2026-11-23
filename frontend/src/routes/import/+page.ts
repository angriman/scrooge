import { get } from '#lib/api.js';
import type { Staged } from '#lib/types.js';
import { pick } from '#lib/view.js';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, url, depends }) => {
	depends('api:import');
	return { staged: await get<Staged>(fetch, '/api/import', pick(url, ['owner'])) };
};
