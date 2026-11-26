import { describe, expect, it } from 'vitest';
import type { CategoryTotal } from '#lib/types.js';
import { TRANSFERS } from './filter.js';
import { activeCurrency, summaryCurrencies } from './summary.js';

const row = (currency: string, key: string, count: number, total: string): CategoryTotal => ({
	currency,
	key,
	name: key,
	count,
	total
});

describe('By category currency tabs', () => {
	const mixed: CategoryTotal[] = [
		row('USD', 'Groceries', 40, '-1200.00'),
		row('USD', 'Dining', 20, '-450.00'),
		row('CHF', 'Housing', 12, '-18000.00'),
		row('EUR', 'Travel', 5, '-900.00')
	];

	it('orders currencies by row count, most rows first', () => {
		expect(summaryCurrencies(mixed)).toEqual(['USD', 'CHF', 'EUR']);
	});

	it('opens on the header currency when the transactions have any in it', () => {
		const currencies = summaryCurrencies(mixed);
		expect(activeCurrency(currencies, null, 'CHF')).toBe('CHF');
		expect(activeCurrency(currencies, null, 'EUR')).toBe('EUR');
		expect(activeCurrency(currencies, null, 'USD')).toBe('USD');
	});

	it('falls back to the first currency when the header currency is absent', () => {
		const currencies = summaryCurrencies(mixed);
		expect(activeCurrency(currencies, null, 'GBP')).toBe('USD');
		expect(activeCurrency(currencies, null, null)).toBe('USD');
		expect(activeCurrency(currencies, null, undefined)).toBe('USD');
	});

	it('ignores currencies that only have transfers or zero totals', () => {
		const totals: CategoryTotal[] = [
			row('CHF', TRANSFERS, 50, '5000.00'),
			row('EUR', 'Refunds', 2, '0.00'),
			row('USD', 'Groceries', 10, '-300.00')
		];
		const currencies = summaryCurrencies(totals);
		expect(currencies).toEqual(['USD']);
		expect(activeCurrency(currencies, null, 'CHF')).toBe('USD');
		expect(activeCurrency(currencies, null, 'EUR')).toBe('USD');
	});

	it('keeps an explicit tab pick when available, falling back to header currency then first', () => {
		const currencies = summaryCurrencies(mixed);
		expect(activeCurrency(currencies, 'EUR', 'CHF')).toBe('EUR');
		expect(activeCurrency(currencies, 'GBP', 'CHF')).toBe('CHF');
		expect(activeCurrency(currencies, 'GBP', 'JPY')).toBe('USD');
	});

	it('returns undefined when no currencies have categories to show', () => {
		expect(summaryCurrencies([])).toEqual([]);
		expect(summaryCurrencies([row('CHF', TRANSFERS, 3, '100.00')])).toEqual([]);
		expect(activeCurrency([], null, 'CHF')).toBeUndefined();
	});
});
