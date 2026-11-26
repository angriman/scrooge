// The subpages of Settings, in the order the left rail and the phone picker list them.

import type { MessageKey } from '#lib/i18n/index.svelte.js';

export type SettingsSectionKey =
	'categories' | 'rules' | 'people' | 'currencies' | 'logos' | 'appearance';

export type SettingsPath =
	| '/settings/categories'
	| '/settings/rules'
	| '/settings/people'
	| '/settings/currencies'
	| '/settings/logos'
	| '/settings/appearance';

export interface SettingsSection {
	readonly key: SettingsSectionKey;
	readonly path: SettingsPath;
	readonly labelKey: MessageKey;
}

export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
	{ key: 'categories', path: '/settings/categories', labelKey: 'settings.sectionCategories' },
	{ key: 'rules', path: '/settings/rules', labelKey: 'settings.sectionRules' },
	{ key: 'people', path: '/settings/people', labelKey: 'settings.sectionPeople' },
	{ key: 'currencies', path: '/settings/currencies', labelKey: 'settings.sectionCurrencies' },
	{ key: 'logos', path: '/settings/logos', labelKey: 'settings.sectionLogos' },
	{ key: 'appearance', path: '/settings/appearance', labelKey: 'settings.sectionAppearance' }
];

export const DEFAULT_SECTION: SettingsSectionKey = 'categories';

/** Which settings section `pathname` is on (with or without a `/app` base prefix); defaults
 * to `categories` when on `/settings` itself or anything unrecognised. */
export function activeSection(pathname: string, base = ''): SettingsSectionKey {
	const trimmed = pathname.replace(/\/+$/, '');
	const prefix = base.replace(/\/+$/, '');
	const relative = prefix && trimmed.startsWith(prefix) ? trimmed.slice(prefix.length) : trimmed;
	for (const section of SETTINGS_SECTIONS) {
		if (
			relative === section.path ||
			relative.startsWith(`${section.path}/`) ||
			trimmed.endsWith(section.path)
		) {
			return section.key;
		}
	}
	return DEFAULT_SECTION;
}

export function sectionByKey(key: string): SettingsSection | undefined {
	return SETTINGS_SECTIONS.find((section) => section.key === key);
}
