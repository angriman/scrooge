import { describe, expect, it } from 'vitest';
import type { MonthEndFigure, NetWorthMonths } from '#lib/types.js';
import {
	anyArrived,
	ARRIVED_NOTE,
	cells,
	CHANGE_NOTE,
	COLUMNS,
	inOrder,
	SPLIT,
	table
} from './monthEnds.js';

/** A month-end's figure, its split written out as the API sends it; nothing came into view
 * unless `arrived` says so, and nothing can have before the first month. */
function figure(
	total: string,
	change: string | null,
	percent: string | null,
	liquid: string,
	frozen: string,
	arrived: string | null = change === null ? null : '0.00'
): MonthEndFigure {
	return { total, change, percent, liquid, frozen, arrived };
}

const sent: NetWorthMonths = {
	currencies: ['CHF', 'USD', 'EUR'],
	months: [
		{
			month: '2026-10',
			day: '2026-10-15',
			partial: true,
			values: {
				CHF: figure('9900.00', '1000.00', '11.24', '4900.00', '5000.00'),
				USD: figure('11000.00', '-125.00', '-1.12', '5444.44', '5555.56'),
				EUR: figure('9900.00', '0.40', '0.00', '4900.00', '5000.00')
			}
		},
		{
			month: '2026-09',
			day: '2026-09-30',
			partial: false,
			values: {
				CHF: figure('8900.00', null, null, '8900.00', '0.00'),
				USD: figure('11125.00', null, null, '11125.00', '0.00'),
				EUR: figure('9899.60', null, null, '9899.60', '0.00')
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
		expect(cells(undefined, [...COLUMNS, ...SPLIT]).map((cell) => cell.text)).toEqual(
			Array(5).fill('–')
		);
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

describe('what came into view in the month-end table', () => {
	const came = {
		...sent,
		months: [
			{
				...sent.months[0],
				values: {
					...sent.months[0].values,
					USD: figure('11000.00', '-2625.00', '-23.60', '5444.44', '5555.56', '2500.00')
				}
			},
			sent.months[1]
		]
	};

	it('is a column of the header’s currency when some month has any', () => {
		const shown = table(came, 'USD');
		expect(shown.columns.map((columns) => columns.map((column) => column.label))).toEqual([
			['Net worth', 'Change', 'Change %', 'Came into view', 'Liquid', 'Frozen'],
			['Net worth', 'Change', 'Change %'],
			['Net worth', 'Change', 'Change %']
		]);
		const [october, september] = shown.rows.map((row) => row.groups[0]);
		// Neither a gain nor a loss: written plain, and a dash before the first change.
		expect(october[COLUMNS.length]).toEqual({ text: '2’500', way: 'none' });
		expect(september[COLUMNS.length]).toEqual({ text: '–', way: 'none' });
		expect(october.slice(COLUMNS.length + 1).map((cell) => cell.text)).toEqual(['5’444', '5’556']);
		expect(shown.note).toBe(ARRIVED_NOTE);
	});

	it('is not a column when nothing came into view in the header’s currency', () => {
		expect(table(sent, 'USD').columns[0].map((column) => column.key)).not.toContain('arrived');
		expect(table(sent, 'USD').note).toBe(CHANGE_NOTE);
		// What came into view in another currency alone is not what the header shows.
		expect(table(came, 'CHF').columns.every((columns) => columns.length !== 6)).toBe(true);
	});

	it('is not a column when what came into view is written 0, a few cents', () => {
		const cents = (change: string, arrived: string): NetWorthMonths => ({
			...sent,
			months: [
				{
					...sent.months[0],
					values: {
						...sent.months[0].values,
						EUR: figure('9900.00', change, '0.00', '4900.00', '5000.00', arrived)
					}
				},
				sent.months[1]
			]
		});
		expect(anyArrived(cents('0.00', '0.40'), 'EUR')).toBe(false);
		expect(anyArrived(cents('0.80', '-0.40'), 'EUR')).toBe(false);
		expect(table(cents('0.00', '0.40'), 'EUR').note).toBe(CHANGE_NOTE);
		expect(anyArrived(cents('-0.20', '0.60'), 'EUR')).toBe(true);
	});
});
