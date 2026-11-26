import { describe, expect, it } from 'vitest';
import { setLanguage } from '#lib/i18n/index.svelte.js';
import { billName, holdingName, mapLabel, payingName } from './names.js';

describe('holding names', () => {
	it('names Treasury bills from their maturity, with a fallback when undated', () => {
		expect(billName('2026-12-24')).toBe('T-bill 24 Dec 2026');
		expect(billName(null)).toBe('T-bill');
		expect(
			holdingName({ symbol: '912797ZZ1', name: '', type: 'bill', maturity: '2026-12-24' })
		).toBe('T-bill 24 Dec 2026');
		expect(holdingName({ symbol: '912797CC7', name: '', type: 'bill', maturity: null })).toBe(
			'T-bill'
		);
		expect(
			holdingName({
				symbol: '912797ZZ1',
				name: 'US 3M Treasury',
				type: 'bill',
				maturity: '2026-12-24'
			})
		).toBe('US 3M Treasury');
	});

	it('keeps stored names and ticker fallbacks for non-bill instruments', () => {
		expect(holdingName({ symbol: 'ACME', name: 'ACME Corp', type: 'stock', maturity: null })).toBe(
			'ACME Corp'
		);
		expect(holdingName({ symbol: 'GADG', name: '', type: 'stock', maturity: null })).toBe('GADG');
		expect(
			holdingName({
				symbol: 'CH0000000011',
				name: 'ACME Global Fund',
				type: 'fund',
				maturity: null
			})
		).toBe('ACME Global Fund');
	});

	it('uses readable names on treemap tiles for T-bills and ISIN funds, tickers for shares', () => {
		expect(mapLabel({ symbol: '912797ZZ1', name: '', type: 'bill', maturity: '2026-12-24' })).toBe(
			'T-bill 24 Dec 2026'
		);
		expect(mapLabel({ symbol: '912797CC7', name: '', type: 'bill', maturity: null })).toBe(
			'T-bill'
		);
		expect(
			mapLabel({
				symbol: 'CH0000000011',
				name: 'ACME Global Fund',
				type: 'fund',
				maturity: null
			})
		).toBe('ACME Global Fund');
		expect(
			mapLabel({ symbol: 'WIDG', name: 'Widget Index Fund', type: 'etf', maturity: null })
		).toBe('WIDG');
	});

	it('names paying T-bills on Investments -> Income without affecting other rows', () => {
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

	it('localizes Treasury bill names in Italian', () => {
		setLanguage('it');
		try {
			expect(
				holdingName({ symbol: '912797ZZ1', name: '', type: 'bill', maturity: '2026-12-24' })
			).toBe('Buono del Tesoro 24 Dic 2026');
			expect(holdingName({ symbol: '912797CC7', name: '', type: 'bill', maturity: null })).toBe(
				'Buono del Tesoro'
			);
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
