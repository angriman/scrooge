import { get } from '#lib/api.js';
import type { Overview } from '#lib/types.js';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, url }) => ({
	shown: await get<Overview>(fetch, '/api/overview', url.searchParams)
});
