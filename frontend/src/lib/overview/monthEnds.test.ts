import { describe, expect, it } from 'vitest';
import type { NetWorthMonths } from '#lib/types.js';
import { cells, COLUMNS, inOrder, table } from './monthEnds.js';

const sent: NetWorthMonths = {
	currencies: ['CHF', 'USD', 'EUR'],
	months: [
		{
			month: '2026-10',
			day: '2026-10-15',
			partial: true,
			values: {
				CHF: { total: '9900.00', change: '1000.00', percent: '11.24' },
				USD: { total: '11000.00', change: '-125.00', percent: '-1.12' },
				EUR: { total: '9900.00', change: '0.40', percent: '0.00' }
			}
		},
		{
			month: '2026-09',
			day: '2026-09-30',
			partial: false,
			values: {
				CHF: { total: '8900.00', change: null, percent: null },
				USD: { total: '11125.00', change: null, percent: null },
				EUR: { total: '8900.00', change: null, percent: null }
			}
		}
	],
	errors: []
};

describe('the currencies of the month-end table', () => {
	it('lead with the one shown, then follow the config', () => {
		expect(inOrder(['CHF', 'USD', 'EUR'], 'EUR')).toEqual(['EUR', 'CHF', 'USD']);
		expect(inOrder(['CHF', 'USD', 'EUR'], 'CHF')).toEqual(['CHF', 'USD', 'EUR']);
	});

	it('stay as they are when the one shown is not among them', () => {
		expect(inOrder(['CHF', 'USD'], 'GBP')).toEqual(['CHF', 'USD']);
	});
});

describe('the month-end table', () => {
	const shown = table(sent, 'USD');

	it('has a row per month, newest first, named in full, the month not over marked', () => {
		expect(shown.currencies).toEqual(['USD', 'CHF', 'EUR']);
		expect(shown.rows.map((row) => [row.label, row.short, row.partial])).toEqual([
			['October 2026', 'Oct 2026', true],
			['September 2026', 'Sep 2026', false]
		]);
	});

	it('writes each currency as its total, its change and its change in percent', () => {
		const [dollars, francs, euros] = shown.rows[0].groups;
		expect(dollars).toEqual([
			{ text: '11’000', way: 'none' },
			{ text: '−125', way: 'loss' },
			{ text: '−1.1%', way: 'loss' }
		]);
		expect(francs.map((cell) => cell.way)).toEqual(['none', 'gain', 'gain']);
		expect(francs[1].text).toBe('+1’000');
		// What rounds to nothing is neither a gain nor a loss.
		expect(euros.slice(1)).toEqual([
			{ text: '0', way: 'flat' },
			{ text: '0.0%', way: 'flat' }
		]);
		expect(dollars).toHaveLength(COLUMNS.length);
	});

	it('has no change for the first month, and dashes for a currency not sent', () => {
		expect(shown.rows[1].groups[0].slice(1)).toEqual([
			{ text: '–', way: 'none' },
			{ text: '–', way: 'none' }
		]);
		expect(cells(undefined).map((cell) => cell.text)).toEqual(['–', '–', '–']);
	});

	it('shows dashes in the columns of a currency without values, the others kept', () => {
		const { CHF, USD } = sent.months[0].values;
		const partly = table({ ...sent, months: [{ ...sent.months[0], values: { CHF, USD } }] }, 'EUR');
		const [euros, francs] = partly.rows[0].groups;
		expect(euros.map((cell) => cell.text)).toEqual(['–', '–', '–']);
		expect(francs[0].text).toBe('9’900');
	});
});
