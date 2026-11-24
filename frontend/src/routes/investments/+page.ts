import { get } from '#lib/api.js';
import type { Holdings, InvestmentIncome } from '#lib/types.js';
import { pick } from '#lib/view.js';
import type { PageLoad } from './$types';

// What is held, or with `view=income` what the investments paid: only what is shown is asked.
export const load: PageLoad = async ({ fetch, url }) =>
	url.searchParams.get('view') === 'income'
		? {
				held: null,
				income: await get<InvestmentIncome>(
					fetch,
					'/api/income',
					pick(url, ['owner', 'currency', 'from', 'to'])
				)
			}
		: {
				held: await get<Holdings>(fetch, '/api/holdings', pick(url, ['owner', 'currency'])),
				income: null
			};
