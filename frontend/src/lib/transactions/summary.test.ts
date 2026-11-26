import { describe, expect, it } from 'vitest';
import type { CategoryTotal, Transaction } from '#lib/types.js';
import { groupByDay, orderCurrencies } from './summary.js';

function row(overrides: Partial<Transaction> = {}): Transaction {
	return {
		id: 't1',
		owner: 'Lukas',
		account: 'Migros',
		date: '2026-09-16',
		booking_date: '2026-09-17',
		amount: '-10.25',
		currency: 'CHF',
		description: 'Groceries',
		counterparty: 'Coop',
		kind: 'expense',
		category: 'Groceries',
		category_parent: 'Food',
		file: 1,
		original: null,
		recurring: null,
		changeable: true,
		merchant: 'Coop',
		category_context: true,
		...overrides
	};
}

function cat(currency: string, count: number, key = 'food'): CategoryTotal {
	return { key, name: 'Food', currency, count, total: '-100.00' };
}

describe('orderCurrencies', () => {
	it('puts headerCurrency first when present in totals, then sorts the rest by row count', () => {
		const totals = [
			cat('CHF', 20, 'food'),
			cat('EUR', 12, 'home'),
			cat('USD', 3, 'shopping'),
			cat('USD', 2, 'food')
		];
		expect(orderCurrencies(totals, 'USD')).toEqual(['USD', 'CHF', 'EUR']);
		expect(orderCurrencies(totals, 'EUR')).toEqual(['EUR', 'CHF', 'USD']);
	});

	it('falls back to the currency with the most rows when headerCurrency is absent or null', () => {
		const totals = [cat('EUR', 4), cat('CHF', 15), cat('USD', 9)];
		expect(orderCurrencies(totals, 'GBP')).toEqual(['CHF', 'USD', 'EUR']);
		expect(orderCurrencies(totals, null)).toEqual(['CHF', 'USD', 'EUR']);
	});

	it('breaks equal row counts alphabetically and handles empty totals', () => {
		expect(orderCurrencies([cat('USD', 5), cat('CHF', 5), cat('EUR', 5)], 'GBP')).toEqual([
			'CHF',
			'EUR',
			'USD'
		]);
		expect(orderCurrencies([], 'CHF')).toEqual([]);
	});
});

describe('groupByDay', () => {
	it('groups consecutive rows by occurrence date and sums per currency without float drift', () => {
		const rows = [
			row({ id: '1', date: '2026-09-16', amount: '-0.10', currency: 'CHF' }),
			row({ id: '2', date: '2026-09-16', amount: '-0.20', currency: 'CHF' }),
			row({ id: '3', date: '2026-09-16', amount: '12.50', currency: 'USD' }),
			row({ id: '4', date: '2026-09-15', amount: '-45.00', currency: 'CHF' })
		];
		const groups = groupByDay(rows, false);
		expect(groups.map((g) => g.day)).toEqual(['2026-09-16', '2026-09-15']);
		expect(groups[0].totals).toEqual([
			{ currency: 'CHF', total: '-0.30' },
			{ currency: 'USD', total: '12.50' }
		]);
		expect(groups[1].totals).toEqual([{ currency: 'CHF', total: '-45.00' }]);
	});

	it('groups by booking_date when booking is true', () => {
		const rows = [
			row({ id: '1', date: '2026-09-16', booking_date: '2026-09-18', amount: '-25.00' }),
			row({ id: '2', date: '2026-09-17', booking_date: '2026-09-18', amount: '-15.50' })
		];
		const groups = groupByDay(rows, true);
		expect(groups).toHaveLength(1);
		expect(groups[0].day).toBe('2026-09-18');
		expect(groups[0].totals).toEqual([{ currency: 'CHF', total: '-40.50' }]);
	});
});
