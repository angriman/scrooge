import { describe, expect, it } from 'vitest';
import { money } from '#lib/format/number.js';
import { setLanguage } from '#lib/i18n/index.svelte.js';
import type { HeldInstrument, HoldingReturns, Position } from '#lib/types.js';
import {
	columnsOf,
	dashNotes,
	periodLines,
	realisedText,
	sortHoldings,
	sortOptions,
	withheldNote,
	yearOf,
	yearReturn
} from './returns.js';

const NONE = { first: null, percent: null, amount: null };

function returns(year: string | null, first = '2026-03-02'): HoldingReturns {
	return {
		periods: [
			{ range: '1m', first: '2026-10-26', percent: '1.50', amount: '15.00' },
			{ range: 'ytd', ...NONE },
			year === null
				? { range: '1y', ...NONE }
				: { range: '1y', first: '2025-11-26', percent: year, amount: '100.00' },
			{ range: 'all', first, percent: '20.00', amount: '200.00' }
		],
		realised: '80.00',
		unrealised: '120.00',
		dividends: '12.00',
		tax: '-1.80',
		sold: true,
		too_small: false
	};
}

const POSITION: Position = {
	owner: 'Lukas',
	account: 'IBKR',
	quantity: '10',
	price: '100',
	price_day: '2026-11-25',
	price_is_trade: false,
	value_native: '1000.00',
	value: '1000.00',
	cost_native: '900.00',
	gain_native: '100.00',
	gain_percent: '11.11',
	returns: returns('10.00')
};

function holding(symbol: string, fields: Partial<HeldInstrument> = {}): HeldInstrument {
	return {
		symbol,
		name: '',
		asset: 'ETFs',
		type: 'etf',
		currency: 'USD',
		maturity: null,
		value: '1000.00',
		share: '50.00',
		cost: '900.00',
		gain: '100.00',
		return_percent: '11.11',
		returns: returns('10.00'),
		positions: [POSITION],
		...fields
	};
}

describe('sortHoldings', () => {
	const acme = holding('ACME', { gain: '50.00', returns: returns('30.00') });
	const widg = holding('WIDG', { gain: '300.00', returns: returns(null) });
	const gadg = holding('GADG', { gain: null, returns: returns('-5.00') });

	it('keeps the order of value, and puts the most first otherwise', () => {
		expect(sortHoldings([acme, widg, gadg], 'value')).toEqual([acme, widg, gadg]);
		expect(sortHoldings([acme, widg, gadg], 'gain').map((h) => h.symbol)).toEqual([
			'WIDG',
			'ACME',
			'GADG'
		]);
	});

	it('puts a holding not held all year last by the return over it', () => {
		expect(sortHoldings([widg, gadg, acme], 'year').map((h) => h.symbol)).toEqual([
			'ACME',
			'GADG',
			'WIDG'
		]);
		expect(yearReturn(null)).toBeNull();
		expect(yearOf(widg.returns)).toBeNull();
		expect(yearOf(acme.returns)?.amount).toBe('100.00');
	});
});

describe('columnsOf', () => {
	it('is the holding alone in one account', () => {
		const one = holding('ACME');
		expect(columnsOf(one, false)).toEqual([{ label: 'Return', returns: one.returns }]);
	});

	it('is every account together, then each, with whose it is when both are listed', () => {
		const anna = { ...POSITION, owner: 'Anna', returns: returns(null, '2026-06-01') };
		const both = holding('ACME', { positions: [POSITION, anna] });
		expect(columnsOf(both, true).map((column) => column.label)).toEqual([
			'All',
			'IBKR, Lukas',
			'IBKR, Anna'
		]);
		expect(columnsOf(both, false)[2].label).toBe('IBKR');
	});
});

describe('periodLines', () => {
	it('is a line a range, a figure a column, and says since when it was bought', () => {
		const lines = periodLines([{ label: 'Return', returns: returns('10.00') }]);
		expect(lines.map((line) => line.label)).toEqual([
			'1 month',
			'This year',
			'1 year',
			'Since bought'
		]);
		expect(lines[1].cells).toEqual([{ percent: null, amount: null }]);
		expect(lines[3]).toEqual({
			label: 'Since bought',
			note: 'from 2 Mar 2026',
			cells: [{ percent: '20.00', amount: '200.00' }]
		});
	});

	it('says no day when the accounts were bought on different ones', () => {
		const lines = periodLines([
			{ label: 'All accounts', returns: returns('10.00') },
			{ label: 'IBKR', returns: returns('10.00', '2026-06-01') },
			{ label: 'Other', returns: null }
		]);
		expect(lines[3].note).toBe('');
		expect(lines[3].cells[2]).toEqual({ percent: null, amount: null });
		expect(periodLines([{ label: 'Return', returns: null }])).toEqual([]);
	});
});

describe('withheldNote', () => {
	it('says what was withheld, when anything was', () => {
		expect(withheldNote(returns('1.00'), money)).toBe('1.80 withheld');
		expect(withheldNote({ ...returns('1.00'), tax: '0.00' }, money)).toBe('');
		expect(withheldNote(null, money)).toBe('');
	});
});

describe('realisedText', () => {
	it('says none sold when nothing was, and the amount when it is known', () => {
		expect(realisedText(returns('1.00'), money)).toBe('80.00');
		expect(realisedText({ ...returns('1.00'), sold: false, realised: '0.00' }, money)).toBe(
			'none sold'
		);
		expect(realisedText({ ...returns('1.00'), realised: null }, money)).toBeNull();
		expect(realisedText(null, money)).toBeNull();
	});
});

describe('dashNotes', () => {
	const small: HoldingReturns = {
		...returns(null),
		periods: returns(null).periods.map((period) => ({ ...period, ...NONE })),
		too_small: true
	};

	it('says a holding too small for a return is, and not that it was not held', () => {
		const columns = [{ label: 'Return', returns: small }];
		expect(dashNotes(columns, periodLines(columns))).toEqual([
			'Never worth 100 CHF at the start of a day, so too small for a return.'
		]);
	});

	it('names the accounts too small, and says a dash elsewhere is a range not held through', () => {
		const columns = [
			{ label: 'All', returns: returns(null) },
			{ label: 'IBKR', returns: returns('10.00') },
			{ label: 'Schwab', returns: small }
		];
		expect(dashNotes(columns, periodLines(columns))).toEqual([
			'Schwab: never worth 100 CHF at the start of a day, so too small for a return.',
			'– Not held through all of the range.'
		]);
	});

	it('says nothing when every range has a return', () => {
		const full: HoldingReturns = {
			...returns('1.00'),
			periods: returns('1.00').periods.map((period) => ({
				...period,
				first: '2025-01-01',
				percent: '1.00',
				amount: '1.00'
			}))
		};
		const columns = [{ label: 'Return', returns: full }];
		expect(dashNotes(columns, periodLines(columns))).toEqual([]);
	});

	it('localizes holdings returns labels and notes in Italian', () => {
		setLanguage('it');
		try {
			expect(sortOptions().map((o) => o.label)).toEqual(['Valore', 'Guadagno', 'Rendimento', '1A']);
			const columns = columnsOf(holding('ACME'), false);
			expect(columns[0].label).toBe('Rendimento');
			expect(periodLines(columns)[3]).toMatchObject({
				label: 'Dall’acquisto',
				note: 'dal 2 Mar 2026'
			});
			expect(withheldNote(returns('1.00'), money)).toBe('1.80 ritenuti');
			expect(realisedText({ ...returns('1.00'), sold: false }, money)).toBe('nessuna vendita');
		} finally {
			setLanguage('en');
		}
	});
});
