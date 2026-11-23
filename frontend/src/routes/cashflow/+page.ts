import { get } from '#lib/api.js';
import type { CashFlow } from '#lib/types.js';
import { pick } from '#lib/view.js';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, url }) => ({
	flow: await get<CashFlow>(fetch, '/api/cashflow', pick(url, ['owner', 'currency', 'from', 'to', 'by']))
});
