import { describe, expect, it } from 'vitest';
import { DEFAULT_SECTION, SETTINGS_SECTIONS, activeSection, sectionByKey } from './sections.js';

describe('settings sections', () => {
	it('lists all five subpages in order', () => {
		expect(SETTINGS_SECTIONS.map((section) => section.key)).toEqual([
			'categories',
			'rules',
			'people',
			'currencies',
			'logos'
		]);
		expect(SETTINGS_SECTIONS.map((section) => section.path)).toEqual([
			'/settings/categories',
			'/settings/rules',
			'/settings/people',
			'/settings/currencies',
			'/settings/logos'
		]);
	});

	it('matches active section with or without /app base and trailing slash', () => {
		expect(activeSection('/app/settings/categories', '/app')).toBe('categories');
		expect(activeSection('/app/settings/rules/', '/app')).toBe('rules');
		expect(activeSection('/app/settings/people', '/app/')).toBe('people');
		expect(activeSection('/settings/currencies')).toBe('currencies');
		expect(activeSection('/app/settings/logos')).toBe('logos');
		expect(activeSection('/app/settings', '/app')).toBe(DEFAULT_SECTION);
		expect(activeSection('/app/settings/unknown', '/app')).toBe(DEFAULT_SECTION);
	});

	it('finds a section by key', () => {
		expect(sectionByKey('people')?.path).toBe('/settings/people');
		expect(sectionByKey('missing')).toBeUndefined();
	});
});
