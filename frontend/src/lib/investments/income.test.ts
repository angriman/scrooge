import { describe, expect, it } from 'vitest';
import type { PayingHolding } from '#lib/types.js';
import {
	change,
	highest,
	isTrailing,
	kindsOf,
	monthLabel,
	paid,
	stack,
	trailingFrom,
	transactionsOf,
	withheld
} from './income.js';

const ACME: PayingHolding = {
	key: 'ACME',
	name: 'ACME Corp',
	symbol: 'ACME',
	type: 'stock',
	maturity: null,
	account: null,
	owner: null,
	dividends: '180.00',
	interest: '0.00',
	tax: '-13.50',
	count: 3,
	last: '2026-06-15'
};
const CASH: PayingHolding = {
	...ACME,
	key: 'IBKR USD',
	name: 'IBKR USD',
	symbol: null,
	type: null,
	maturity: null,
	account: 'IBKR USD',
	owner: null,
	dividends: '0.00',
	interest: '18.00',
	tax: '0.00'
};

describe('what investments paid', () => {
	it('adds dividends and interest, before tax', () => {
		expect(paid({ dividends: '180.00', interest: '18.50', tax: '-13.50' })).toBe('198.50');
		expect(withheld('-13.50')).toBe('13.50');
		expect(withheld('0.00')).toBe('0.00');
	});

	it('compares with the year before only when something was paid then', () => {
		const now = { dividends: '150', interest: '0', tax: '0' };
		expect(change(now, { dividends: '100', interest: '20', tax: '-15' })).toBe('25.0');
		expect(change(now, { dividends: '0', interest: '0', tax: '0' })).toBeNull();
		expect(change(now, null)).toBeNull();
	});

	it('stacks the columns of the chart from nothing up', () => {
		const months = [
			{ dividends: '90', interest: '10', tax: '0' },
			{ dividends: '0', interest: '-4', tax: '0' }
		];
		expect(highest(months)).toBe(100);
		expect(highest([])).toBe(1);
		expect(stack(months[0], 2)).toEqual({ dividends: 180, interest: 20 });
		expect(stack(months[1], 2)).toEqual({ dividends: 0, interest: 0 });
		expect(monthLabel('2026-03')).toBe('Mar');
	});

	it('opens the transactions a holding is made of', () => {
		const range = { first: '2025-09-01', last: '2026-08-31' };
		expect(transactionsOf(ACME, range, 'Anna').toString()).toBe(
			'owner=Anna&from=2025-09-01&to=2026-08-31&kind=dividend%2Ctax&text=ACME'
		);
		// An account only one person has keeps everyone's figures shown.
		expect(transactionsOf(CASH, { first: null, last: null }, null).toString()).toBe(
			'kind=interest&account=IBKR+USD'
		);
		// One both have opens the rows of the person it is named after.
		const hers = { ...CASH, name: 'IBKR USD (Anna)', owner: 'Anna' };
		expect(transactionsOf(hers, range, null).toString()).toBe(
			'owner=Anna&from=2025-09-01&to=2026-08-31&kind=interest&account=IBKR+USD'
		);
		expect(kindsOf({ ...CASH, interest: '0', tax: '-4.50' })).toEqual(['tax']);
		expect(kindsOf({ ...CASH, interest: '0' })).toEqual(['dividend', 'interest', 'tax']);
	});

	it('knows when the range is the last twelve months', () => {
		expect(trailingFrom('2026-08-31')).toBe('2025-09-01');
		expect(trailingFrom('2026-12-31')).toBe('2026-01-01');
		const through = '2026-08-31';
		expect(isTrailing({ first: '2025-09-01', last: through, through })).toBe(true);
		expect(isTrailing({ first: '2026-01-01', last: through, through })).toBe(false);
		expect(isTrailing({ first: '2025-09-01', last: '2026-07-31', through })).toBe(false);
		expect(isTrailing({ first: null, last: null, through: null })).toBe(false);
	});
});
