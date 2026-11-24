import { get } from '#lib/api.js';
import type { Holdings, InvestmentIncome, InvestmentPerformance } from '#lib/types.js';
import { pick } from '#lib/view.js';
import type { PageLoad } from './$types';

// What the investments returned; with `view=holdings` what is held, with `view=income` what
// they paid: only what is shown is asked.
export const load: PageLoad = async ({ fetch, url }) => {
	const view = url.searchParams.get('view');
	if (view === 'holdings') {
		return {
			held: await get<Holdings>(fetch, '/api/holdings', pick(url, ['owner', 'currency'])),
			income: null,
			performance: null
		};
	}
	if (view === 'income') {
		return {
			held: null,
			income: await get<InvestmentIncome>(
				fetch,
				'/api/income',
				pick(url, ['owner', 'currency', 'from', 'to'])
			),
			performance: null
		};
	}
	return {
		held: null,
		income: null,
		performance: await get<InvestmentPerformance>(
			fetch,
			'/api/performance',
			pick(url, ['owner', 'currency', 'range'])
		)
	};
};
