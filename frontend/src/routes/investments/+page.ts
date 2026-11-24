import { get } from '#lib/api.js';
import type {
	Holdings,
	InvestmentCosts,
	InvestmentIncome,
	InvestmentPerformance
} from '#lib/types.js';
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
			costs: null,
			performance: null
		};
	}
	if (view === 'income') {
		// What investing cost each year is under what was paid, whatever the range.
		const [income, costs] = await Promise.all([
			get<InvestmentIncome>(fetch, '/api/income', pick(url, ['owner', 'currency', 'from', 'to'])),
			get<InvestmentCosts>(fetch, '/api/costs', pick(url, ['owner', 'currency']))
		]);
		return { held: null, income, costs, performance: null };
	}
	return {
		held: null,
		income: null,
		costs: null,
		performance: await get<InvestmentPerformance>(
			fetch,
			'/api/performance',
			pick(url, ['owner', 'currency', 'range'])
		)
	};
};
