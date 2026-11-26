import { get } from '#lib/api.js';
import type { SettingsLogos } from '#lib/types.js';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, depends }) => {
	depends('api:settings:logos');
	const logos = await get<SettingsLogos>(fetch, '/api/settings/logos');
	return { logos };
};
