import { get } from '#lib/api.js';
import type { SettingsCategories, SettingsRules } from '#lib/types.js';
import { pick } from '#lib/view.js';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, url, depends }) => {
	depends('api:settings');
	const [rules, categories] = await Promise.all([
		get<SettingsRules>(fetch, '/api/settings/rules', pick(url, ['owner'])),
		get<SettingsCategories>(fetch, '/api/settings/categories')
	]);
	return { rules, categories };
};
