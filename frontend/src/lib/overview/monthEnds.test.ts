import { describe, expect, it } from 'vitest';
import type { MonthEndFigure, NetWorthMonths } from '#lib/types.js';
import { cells, COLUMNS, inOrder, table } from './monthEnds.js';

/** A month-end's figure, what is not liquid of its total frozen. */
function figure(
	total: string,
	change: string | null,
	percent: string | null,
	liquid: string
): MonthEndFigure {
	return { total, change, percent, liquid, frozen: (Number(total) - Number(liquid)).toFixed(2) };
}

const sent: NetWorthMonths = {
	currencies: ['CHF', 'USD', 'EUR'],
	months: [
		{
			month: '2026-10',
			day: '2026-10-15',
			partial: true,
			values: {
				CHF: figure('9900.00', '1000.00', '11.24', '4900.00'),
				USD: figure('11000.00', '-125.00', '-1.12', '5444.44'),
				EUR: figure('9900.00', '0.40', '0.00', '4900.00')
			}
		},
		{
			month: '2026-09',
			day: '2026-09-30',
			partial: false,
			values: {
				CHF: figure('8900.00', null, null, '8900.00'),
				USD: figure('11125.00', null, null, '11125.00'),
				EUR: figure('8900.00', null, null, '8900.00')
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
		expect(dollars.slice(0, COLUMNS.length)).toEqual([
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
	});

	it('splits the currency of the header alone into what is liquid and what frozen', () => {
		expect(shown.columns.map((columns) => columns.map((column) => column.label))).toEqual([
			['Net worth', 'Change', 'Change %', 'Liquid', 'Frozen'],
			['Net worth', 'Change', 'Change %'],
			['Net worth', 'Change', 'Change %']
		]);
		const [october, september] = shown.rows.map((row) => row.groups[0]);
		// The month not over is split as it is so far, like its total.
		expect(shown.rows[0].partial).toBe(true);
		expect(october.slice(COLUMNS.length)).toEqual([
			{ text: '5’444', way: 'none' },
			{ text: '5’556', way: 'none' }
		]);
		expect(september.slice(COLUMNS.length).map((cell) => cell.text)).toEqual(['11’125', '0']);
		const widths = shown.columns.map((columns) => columns.length);
		expect(shown.rows.map((row) => row.groups.map((group) => group.length))).toEqual([
			widths,
			widths
		]);
		expect(cells(undefined, true).map((cell) => cell.text)).toEqual(Array(5).fill('–'));
	});

	it('has no change for the first month, and dashes for a currency not sent', () => {
		expect(shown.rows[1].groups[0].slice(1, COLUMNS.length)).toEqual([
			{ text: '–', way: 'none' },
			{ text: '–', way: 'none' }
		]);
		expect(cells(undefined).map((cell) => cell.text)).toEqual(['–', '–', '–']);
	});

	it('shows dashes in the columns of a currency without values, the others kept', () => {
		const { CHF, USD } = sent.months[0].values;
		const partly = table({ ...sent, months: [{ ...sent.months[0], values: { CHF, USD } }] }, 'EUR');
		const [euros, francs] = partly.rows[0].groups;
		expect(euros.map((cell) => cell.text)).toEqual(['–', '–', '–', '–', '–']);
		expect(francs[0].text).toBe('9’900');
	});
});
