import { get } from '#lib/api.js';
import type {
	Holdings,
	InvestmentIncome,
	InvestmentPensions,
	InvestmentPerformance
} from '#lib/types.js';
import { pick } from '#lib/view.js';
import type { PageLoad } from './$types';

// What the investments returned; with `view=holdings` what is held and the pensions, with
// `view=income` what they paid: only what is shown is asked.
export const load: PageLoad = async ({ fetch, url }) => {
	const view = url.searchParams.get('view');
	if (view === 'holdings') {
		const asked = pick(url, ['owner', 'currency']);
		const [held, pensions] = await Promise.all([
			get<Holdings>(fetch, '/api/holdings', asked),
			get<InvestmentPensions>(fetch, '/api/pensions', asked)
		]);
		return { held, pensions, income: null, performance: null };
	}
	if (view === 'income') {
		return {
			held: null,
			pensions: null,
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
		pensions: null,
		income: null,
		performance: await get<InvestmentPerformance>(
			fetch,
			'/api/performance',
			pick(url, ['owner', 'currency', 'range'])
		)
	};
};
