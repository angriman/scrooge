import { describe, expect, it } from 'vitest';
import { setLanguage } from '#lib/i18n/index.svelte.js';
import { netWorthChartLabel } from './netWorth.js';

describe('netWorthChartLabel', () => {
	it('names the breakdown and month span in English', () => {
		expect(netWorthChartLabel('Net worth', 'asset', true, '2026-08-01', '2026-11-30')).toBe(
			'Net worth by type, Aug 2026 to Nov 2026'
		);
		expect(
			netWorthChartLabel('Household net worth', 'owner', true, '2025-11-30', '2026-11-30')
		).toBe('Household net worth by person, Nov 2025 to Nov 2026');
		expect(netWorthChartLabel('Net worth', 'liquidity', true, '2026-08-01', '2026-11-30')).toBe(
			'Net worth by liquidity, Aug 2026 to Nov 2026'
		);
	});

	it('omits the breakdown when total or not cut by a lens', () => {
		expect(netWorthChartLabel('Net worth', 'total', true, '2026-08-01', '2026-11-30')).toBe(
			'Net worth, Aug 2026 to Nov 2026'
		);
		expect(netWorthChartLabel('ETFs', 'asset', false, '2026-08-01', '2026-11-30')).toBe(
			'ETFs, Aug 2026 to Nov 2026'
		);
	});

	it('uses exact days for a same-month span, a single day, or no dates', () => {
		expect(netWorthChartLabel('Net worth', 'asset', true, '2026-11-01', '2026-11-25')).toBe(
			'Net worth by type, 1 Nov 2026 to 25 Nov 2026'
		);
		expect(netWorthChartLabel('Net worth', 'asset', true, '2026-11-25', '2026-11-25')).toBe(
			'Net worth by type, 25 Nov 2026'
		);
		expect(netWorthChartLabel('Net worth', 'asset', true, null, null)).toBe('Net worth by type');
	});

	it('localizes breakdown and spans in Italian', () => {
		setLanguage('it');
		try {
			expect(
				netWorthChartLabel('Patrimonio netto', 'asset', true, '2026-08-01', '2026-11-30')
			).toBe('Patrimonio netto per tipo, da Ago 2026 a Nov 2026');
			expect(
				netWorthChartLabel('Patrimonio netto familiare', 'owner', true, '2026-08-01', '2026-11-30')
			).toBe('Patrimonio netto familiare per persona, da Ago 2026 a Nov 2026');
			expect(
				netWorthChartLabel('Patrimonio netto', 'liquidity', true, '2026-11-01', '2026-11-25')
			).toBe('Patrimonio netto per liquidità, dal 1 Nov 2026 al 25 Nov 2026');
			expect(
				netWorthChartLabel('Patrimonio netto', 'asset', true, '2026-11-25', '2026-11-25')
			).toBe('Patrimonio netto per tipo, 25 Nov 2026');
		} finally {
			setLanguage('en');
		}
	});
});
