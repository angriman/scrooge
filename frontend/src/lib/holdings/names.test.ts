import { describe, expect, it } from 'vitest';
import { setLanguage } from '#lib/i18n/index.svelte.js';
import { billName, holdingDue, holdingName, isBill, mapLabel, payingName } from './names.js';

describe('holding names and bill maturity formatting', () => {
	const bill = {
		symbol: '912797ZZ1',
		name: '',
		type: 'bill',
		asset: 'Bills',
		maturity: '2026-12-24'
	};
	const undatedBill = { ...bill, maturity: null };
	const namedBill = { ...bill, name: 'US T-Bill Dec 2026' };
	const bond = { symbol: 'CH0001', name: '', type: 'bond', asset: 'Bills', maturity: '2028-06-15' };
	const stock = {
		symbol: 'ACME',
		name: 'Acme Corp',
		type: 'stock',
		asset: 'Stocks',
		maturity: null
	};
	const fund = {
		symbol: 'CH0000000011',
		name: 'Alpha CH Fund',
		type: 'fund',
		asset: 'Pillar 3a',
		maturity: null
	};

	it('identifies bills and bonds and gives readable names in English', () => {
		expect(isBill(bill)).toBe(true);
		expect(isBill(bond)).toBe(true);
		expect(isBill(stock)).toBe(false);

		expect(billName(bill)).toBe('T-bill');
		expect(billName(namedBill)).toBe('US T-Bill Dec 2026');
		expect(billName(bond)).toBe('Bond');

		expect(holdingName(bill)).toBe('T-bill');
		expect(holdingName(namedBill)).toBe('US T-Bill Dec 2026');
		expect(holdingName(stock)).toBe('Acme Corp');
		expect(holdingName({ ...stock, name: '' })).toBe('ACME');
	});

	it('formats due dates without duplicating them and handles undated bills', () => {
		expect(holdingDue(bill)).toBe('due 24 Dec 2026');
		expect(holdingDue(undatedBill)).toBe('no maturity');
		expect(holdingDue(stock)).toBe('');
		// Bill display name and due date are separate strings so the date never appears twice.
		expect(holdingName(bill)).not.toContain('2026');
	});

	it('produces concise map labels for bills, ISIN funds, and tickers', () => {
		expect(mapLabel(bill)).toBe('T-bill · 24 Dec');
		expect(mapLabel(undatedBill)).toBe('T-bill');
		expect(mapLabel(namedBill)).toBe('US T-Bill Dec 2026');
		expect(mapLabel(fund)).toBe('Alpha CH Fund');
		expect(mapLabel(stock)).toBe('ACME');
	});

	it('names paying T-bills on Investments -> Payouts without affecting other rows', () => {
		expect(
			payingName({
				key: '912797YY2',
				name: '912797YY2',
				symbol: '912797YY2',
				type: 'bill',
				maturity: '2026-09-16',
				account: null
			})
		).toBe('T-bill 16 Sep 2026');
		expect(
			payingName({
				key: '912797YY2',
				name: '912797YY2',
				symbol: '912797YY2',
				type: 'bill',
				maturity: null,
				account: null
			})
		).toBe('T-bill');
		expect(
			payingName({
				key: '912797YY2',
				name: 'Custom Bill',
				symbol: '912797YY2',
				type: 'bill',
				maturity: '2026-09-16',
				account: null
			})
		).toBe('Custom Bill');
		expect(
			payingName({
				key: 'ACME',
				name: 'ACME Corp',
				symbol: 'ACME',
				type: 'stock',
				maturity: null,
				account: null
			})
		).toBe('ACME Corp');
		expect(
			payingName({
				key: 'Migros',
				name: 'Migros (Anna)',
				symbol: null,
				type: null,
				maturity: null,
				account: 'Migros'
			})
		).toBe('Migros (Anna)');
	});

	it('localizes bill names, due dates, and payout names in Italian', () => {
		setLanguage('it');
		try {
			expect(holdingName(bill)).toBe('Buono del Tesoro');
			expect(holdingName(bond)).toBe('Obbligazione');
			expect(holdingDue(bill)).toBe('scad. 24 Dic 2026');
			expect(holdingDue(undatedBill)).toBe('senza scadenza');
			expect(mapLabel(bill)).toBe('Buono del Tesoro · 24 Dic');
			expect(
				payingName({
					key: '912797YY2',
					name: '912797YY2',
					symbol: '912797YY2',
					type: 'bill',
					maturity: '2026-09-16',
					account: null
				})
			).toBe('Buono del Tesoro 16 Set 2026');
		} finally {
			setLanguage('en');
		}
	});
});
