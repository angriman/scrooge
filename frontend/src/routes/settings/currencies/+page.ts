import { get } from '#lib/api.js';
import type { SettingsCurrencies } from '#lib/types.js';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, depends }) => {
	depends('api:settings:currencies');
	const currencies = await get<SettingsCurrencies>(fetch, '/api/settings/currencies');
	return { currencies };
};
