import { describe, expect, it } from 'vitest';
import type { Recurring, RecurringPayment } from '#lib/types.js';
import { anyRecurring, everyName, isNew, monthsBefore, priceChange } from './recurring.js';

const payment = (fields: Partial<RecurringPayment>): RecurringPayment => ({
	id: 't1',
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
	alone: true,
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
		// Three months before the last of May are the last of February, not 3 March.
		expect(isNew(payment({ first: '2026-03-01' }), '2026-05-31')).toBe(true);
		expect(isNew(payment({ first: '2026-02-28' }), '2026-05-31')).toBe(false);
	});

	it('counts months back to the same day, or the last of a shorter month', () => {
		expect(monthsBefore('2026-05-31', 3)).toBe('2026-02-28');
		expect(monthsBefore('2028-05-31', 3)).toBe('2028-02-29');
		expect(monthsBefore('2026-10-25', 2)).toBe('2026-08-25');
		expect(monthsBefore('2026-01-15', 3)).toBe('2025-10-15');
	});

	it('leaves the panel out when nothing is listed, active or stopped', () => {
		const list = (fields: Partial<Recurring>): Recurring => ({
			currency: 'CHF',
			through: '2026-10-31',
			monthly: '0',
			active: [],
			ended: [],
			errors: [],
			...fields
		});
		expect(anyRecurring(list({}))).toBe(false);
		expect(anyRecurring(list({ through: null }))).toBe(false);
		expect(anyRecurring(list({ active: [payment({})] }))).toBe(true);
		expect(anyRecurring(list({ ended: [payment({})] }))).toBe(true);
	});

	it('names the pace', () => {
		expect(everyName('monthly')).toBe('a month');
		expect(everyName('yearly')).toBe('a year');
	});
});
