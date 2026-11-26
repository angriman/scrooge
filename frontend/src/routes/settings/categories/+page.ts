import { get } from '#lib/api.js';
import type { SettingsCategories } from '#lib/types.js';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, depends }) => {
	depends('api:settings:categories');
	const categories = await get<SettingsCategories>(fetch, '/api/settings/categories');
	return { categories };
};
