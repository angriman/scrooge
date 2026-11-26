import { get } from '#lib/api.js';
import type { SettingsRules } from '#lib/types.js';
import { pick } from '#lib/view.js';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, url, depends, parent }) => {
	depends('api:settings:rules');
	const { session } = await parent();
	const params = session.rules ? pick(url, ['owner']) : new URLSearchParams();
	const rules = await get<SettingsRules>(fetch, '/api/settings/rules', params);
	return { rules };
};
