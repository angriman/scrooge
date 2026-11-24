import { describe, expect, it } from 'vitest';
import { heldLines, securitiesQuery, sparkline } from './holds.js';

describe('heldLines', () => {
	it('lists cash by currency, converted where it is not the page’s, then the securities', () => {
		const holds = {
			cash: [
				{ currency: 'USD', amount: '1250.00', converted: '1000.00' },
				{ currency: 'CHF', amount: '100.00', converted: '100.00' }
			],
			securities: '400.00',
			months: []
		};
		expect(heldLines(holds, 'CHF')).toEqual([
			{ label: 'Cash in USD', amount: '1250.00', currency: 'USD', converted: '1000.00', securities: false },
			{ label: 'Cash', amount: '100.00', currency: 'CHF', converted: null, securities: false },
			{ label: 'Securities', amount: '400.00', currency: 'CHF', converted: null, securities: true }
		]);
	});

	it('gives a bank account the one line it has', () => {
		const holds = { cash: [{ currency: 'CHF', amount: '700.00', converted: '700.00' }], securities: null, months: [] };
		expect(heldLines(holds, 'CHF').map((line) => line.label)).toEqual(['Cash']);
	});
});

describe('sparkline', () => {
	it('puts the lowest balance at the bottom and the highest at the top', () => {
		expect(sparkline(['100', '300', '200'], 40, 12, 1)).toBe('0,11 20,1 40,6');
	});

	it('draws a flat line across the middle and nothing for a single month', () => {
		expect(sparkline(['5', '5'], 10, 8)).toBe('0,4 10,4');
		expect(sparkline(['5'], 10, 8)).toBe('');
		expect(sparkline([], 10, 8)).toBe('');
	});
});

describe('securitiesQuery', () => {
	it('opens the holdings of the account’s owner, in the currency the address names', () => {
		expect(securitiesQuery('Anna Lee', 'USD')).toBe('view=holdings&owner=Anna+Lee&currency=USD');
		expect(securitiesQuery('Anna', null)).toBe('view=holdings&owner=Anna');
	});
});
