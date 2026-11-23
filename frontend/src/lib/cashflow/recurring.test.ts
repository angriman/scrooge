import { describe, expect, it } from 'vitest';
import type { RecurringPayment } from '#lib/types.js';
import { everyName, isNew, priceChange } from './recurring.js';

const payment = (fields: Partial<RecurringPayment>): RecurringPayment => ({
	name: 'Streamio',
	owner: 'Anna',
	account: 'Migros',
	every: 'monthly',
	amount: '12',
	currency: 'CHF',
	previous: null,
	count: 6,
	first: '2026-05-03',
	last: '2026-10-03',
	next: '2026-11-03',
	monthly: '12',
	...fields
});

describe('the recurring payments of the cash flow', () => {
	it('says whether the price went up or down', () => {
		expect(priceChange(payment({ previous: '10' }))).toBe('up');
		expect(priceChange(payment({ previous: '15.50' }))).toBe('down');
		expect(priceChange(payment({ previous: '12.00' }))).toBeNull();
		expect(priceChange(payment({}))).toBeNull();
	});

	it('calls new what was first paid in the last three months', () => {
		expect(isNew(payment({ first: '2026-08-01' }), '2026-10-31')).toBe(true);
		expect(isNew(payment({ first: '2026-07-31' }), '2026-10-31')).toBe(false);
		expect(isNew(payment({ first: '2026-10-01' }), null)).toBe(false);
	});

	it('names the pace', () => {
		expect(everyName('monthly')).toBe('a month');
		expect(everyName('yearly')).toBe('a year');
	});
});
