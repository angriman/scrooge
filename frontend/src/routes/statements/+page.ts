import { get } from '#lib/api.js';
import type { Attention, Statements } from '#lib/types.js';
import { pick } from '#lib/view.js';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, url }) => {
	const [listed, attention] = await Promise.all([
		get<Statements>(fetch, '/api/statements', pick(url, ['owner', 'currency'])),
		get<Attention>(fetch, '/api/attention', pick(url, ['owner']))
	]);
	return { listed, attention };
};
