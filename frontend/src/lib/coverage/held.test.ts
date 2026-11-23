import { describe, expect, it } from 'vitest';
import { heldTogether } from './held.js';

describe('what the accounts hold together', () => {
	it('adds up every balance, to the cent', () => {
		expect(heldTogether([{ balance: '1000.10' }, { balance: '250.20' }])).toEqual({
			total: '1250.30',
			unknown: 0
		});
	});

	it('takes what is owed off', () => {
		expect(heldTogether([{ balance: '500.00' }, { balance: '-800.00' }])).toEqual({
			total: '-300.00',
			unknown: 0
		});
	});

	it('leaves out the balances not known, and says how many', () => {
		expect(heldTogether([{ balance: '700' }, { balance: null }, { balance: null }])).toEqual({
			total: '700',
			unknown: 2
		});
	});

	it('knows no total when no balance is known', () => {
		expect(heldTogether([{ balance: null }])).toEqual({ total: null, unknown: 1 });
		expect(heldTogether([])).toEqual({ total: null, unknown: 0 });
	});
});
