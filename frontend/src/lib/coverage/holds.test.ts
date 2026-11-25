import { describe, expect, it } from 'vitest';
import { heldLines, securitiesQuery, sparkline, trend } from './holds.js';

describe('heldLines', () => {
	it('lists cash by currency, converted where it is not the page’s, then the securities', () => {
		const holds = {
			cash: [
				{ asset: 'Cash', currency: 'USD', amount: '1250.00', converted: '1000.00' },
				{ asset: 'Cash', currency: 'CHF', amount: '100.00', converted: '100.00' }
			],
			securities: '400.00',
			months: []
		};
		expect(heldLines(holds, 'CHF')).toEqual([
			{
				label: 'Cash in USD',
				amount: '1250.00',
				currency: 'USD',
				converted: '1000.00',
				securities: false
			},
			{
				label: 'Cash',
				amount: '100.00',
				currency: 'CHF',
				converted: null,
				securities: false
			},
			{
				label: 'Securities',
				amount: '400.00',
				currency: 'CHF',
				converted: null,
				securities: true
			}
		]);
	});

	it('gives a bank account the one line it has', () => {
		const holds = {
			cash: [{ asset: 'Cash', currency: 'CHF', amount: '700.00', converted: '700.00' }],
			securities: null,
			months: []
		};
		expect(heldLines(holds, 'CHF').map((line) => line.label)).toEqual(['Cash']);
	});

	it('names a card’s balance, a 3a’s cash and a pension fund’s balance by what they are', () => {
		const line = (asset: string, currency = 'CHF') =>
			heldLines(
				{
					cash: [{ asset, currency, amount: '-1.00', converted: '-1.00' }],
					securities: null,
					months: []
				},
				'CHF'
			)[0].label;
		expect([line('Card'), line('Card', 'EUR'), line('Pillar 3a'), line('Pillar 2')]).toEqual([
			'Card balance',
			'Card balance in EUR',
			'Uninvested cash',
			'Pension fund balance'
		]);
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
		expect(securitiesQuery('Anna Lee', 'USD')).toBe(
			'view=holdings&owner=Anna+Lee&currency=USD'
		);
		expect(securitiesQuery('Anna', null)).toBe('view=holdings&owner=Anna');
	});
});

describe('trend', () => {
	const months = (...balances: string[]) =>
		balances.map((balance, index) => ({ day: `2026-0${index + 1}-28`, balance }));

	it('says which way the balance went since the first month-end of its line', () => {
		expect(trend(months('41000.40', '35000', '42000'), 'CHF')).toBe(
			'up from 41’000 CHF at the end of Jan 2026'
		);
		expect(trend(months('500', '400'), 'USD')).toBe('down from 500 USD at the end of Jan 2026');
		expect(trend(months('0.26', '0.26'), 'EUR')).toBe('unchanged since the end of Jan 2026');
	});

	it('says nothing without a line', () => {
		expect(trend(months('100'), 'CHF')).toBe('');
	});
});
