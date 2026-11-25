import { get } from '#lib/api.js';
import type { CashFlow, Recurring } from '#lib/types.js';
import { pick } from '#lib/view.js';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, url }) => {
	const [flow, recurring] = await Promise.all([
		get<CashFlow>(
			fetch,
			'/api/cashflow',
			pick(url, ['owner', 'currency', 'from', 'to', 'by', 'range'])
		),
		get<Recurring>(fetch, '/api/recurring', pick(url, ['owner', 'currency']))
	]);
	return { flow, recurring };
};
