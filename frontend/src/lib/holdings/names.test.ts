import { describe, expect, it } from 'vitest';
import { setLanguage } from '#lib/i18n/index.svelte.js';
import { billName, holdingDue, holdingName, isBill, mapLabel } from './names.js';

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

	it('localizes bill names and due dates in Italian', () => {
		setLanguage('it');
		try {
			expect(holdingName(bill)).toBe('Buono del Tesoro');
			expect(holdingName(bond)).toBe('Obbligazione');
			expect(holdingDue(bill)).toBe('scad. 24 Dic 2026');
			expect(holdingDue(undatedBill)).toBe('senza scadenza');
			expect(mapLabel(bill)).toBe('Buono del Tesoro · 24 Dic');
		} finally {
			setLanguage('en');
		}
	});
});
