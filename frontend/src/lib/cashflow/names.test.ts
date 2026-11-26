import { describe, expect, it } from 'vitest';
import { setLanguage } from '#lib/i18n/index.svelte.js';
import {
	OTHERS,
	PAID_BACK,
	partColors,
	rangeName,
	rangePillLabel,
	spendingPartName
} from './names.js';

describe('the names of the cash flow', () => {
	it('says the range in words', () => {
		expect(rangeName('Year to date', '2026-01-01', null)).toBe('this year so far');
		expect(rangeName('2025', '2025-01-01', '2025-12-31')).toBe('in 2025');
		expect(rangeName(null, '2026-08-01', null)).toBe('since 1 Aug 2026');
		expect(rangeName(null, '2026-03-01', '2026-05-31')).toBe('from 1 Mar 2026 to 31 May 2026');
	});

	it('gives the largest categories a colour each and the rest one together', () => {
		const colors = partColors(['Home', 'Groceries', OTHERS, PAID_BACK]);
		expect([...colors]).toEqual([
			['Home', 'var(--data-1)'],
			['Groceries', 'var(--data-2)'],
			[OTHERS, 'var(--data-other)']
		]);
	});

	it('localizes range labels, range sentences, and spending slice names in Italian', () => {
		setLanguage('it');
		try {
			expect(rangePillLabel({ key: 'ytd', label: 'Year to date' })).toBe('Da inizio anno');
			expect(rangePillLabel({ key: '12m', label: 'Last 12 months' })).toBe('Ultimi 12 mesi');
			expect(rangePillLabel({ key: 'all', label: 'All' })).toBe('Tutto');
			expect(rangeName('Year to date', '2026-01-01', null)).toBe('da inizio anno');
			expect(rangeName('2025', '2025-01-01', '2025-12-31')).toBe('nel 2025');
			expect(rangeName(null, '2026-08-01', null)).toBe('dal 1 Ago 2026');
			expect(spendingPartName(OTHERS)).toBe('Altro');
			expect(spendingPartName(PAID_BACK)).toBe('Rimborsato');
		} finally {
			setLanguage('en');
		}
	});
});
