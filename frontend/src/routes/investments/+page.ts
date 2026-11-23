import { get } from '#lib/api.js';
import type { Holdings } from '#lib/types.js';
import { pick } from '#lib/view.js';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, url }) => ({
	held: await get<Holdings>(fetch, '/api/holdings', pick(url, ['owner', 'currency']))
});
