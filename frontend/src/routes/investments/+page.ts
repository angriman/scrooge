import { isRedirect } from '@sveltejs/kit';
import { get } from '#lib/api.js';
import { whyNot } from '#lib/investments/costs.js';
import type {
	Holdings,
	InvestmentCosts,
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
		return { held, pensions, income: null, costs: null, performance: null };
	}
	if (view === 'income') {
		// What investing cost each year is under what was paid, whatever the range. Costs that
		// cannot be loaded leave what was paid shown, and their panel says why.
		const [income, costs] = await Promise.all([
			get<InvestmentIncome>(fetch, '/api/income', pick(url, ['owner', 'currency', 'from', 'to'])),
			get<InvestmentCosts>(fetch, '/api/costs', pick(url, ['owner', 'currency'])).then(
				(sent) => ({ sent, unavailable: '' }),
				(failed: unknown) => {
					if (isRedirect(failed)) throw failed;
					return { sent: null, unavailable: whyNot(failed) };
				}
			)
		]);
		return { held: null, pensions: null, income, costs, performance: null };
	}
	return {
		held: null,
		pensions: null,
		income: null,
		costs: null,
		performance: await get<InvestmentPerformance>(
			fetch,
			'/api/performance',
			pick(url, ['owner', 'currency', 'range'])
		)
	};
};
