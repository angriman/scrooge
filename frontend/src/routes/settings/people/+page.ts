import { get } from '#lib/api.js';
import type { SettingsPeople } from '#lib/types.js';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, depends }) => {
	depends('api:settings:people');
	const people = await get<SettingsPeople>(fetch, '/api/settings/people');
	return { people };
};
