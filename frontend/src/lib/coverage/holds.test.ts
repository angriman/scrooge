import { describe, expect, it } from 'vitest';
import { setLanguage } from '#lib/i18n/index.svelte.js';
import { heldLines, securitiesQuery } from './holds.js';

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
			{ label: 'Cash', amount: '100.00', currency: 'CHF', converted: null, securities: false },
			{ label: 'Securities', amount: '400.00', currency: 'CHF', converted: null, securities: true }
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

	it('localizes held lines into Italian', () => {
		setLanguage('it');
		try {
			const holds = {
				cash: [{ asset: 'Card', currency: 'EUR', amount: '-1.00', converted: '-1.00' }],
				securities: '400.00',
				months: []
			};
			expect(heldLines(holds, 'CHF').map((line) => line.label)).toEqual([
				'Saldo carta in EUR',
				'Titoli'
			]);
		} finally {
			setLanguage('en');
		}
	});
});

describe('securitiesQuery', () => {
	it('opens the holdings of the account’s owner, in the currency the address names', () => {
		expect(securitiesQuery('Anna Lee', 'USD')).toBe('view=holdings&owner=Anna+Lee&currency=USD');
		expect(securitiesQuery('Anna', null)).toBe('view=holdings&owner=Anna');
	});
});
