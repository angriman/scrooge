import { describe, expect, it } from 'vitest';
import type { Pension, PensionChange } from '#lib/types.js';
import { changeOf, described, periodOf, totals } from './pensions.js';

const NOTHING: PensionChange = {
	since: '2025-09-30',
	start: null,
	paid_in: null,
	twr: null,
	mwr: null,
	mwr_a_year: false,
	twr_since: null
};
const THREE_A: Pension = {
	owner: 'Lukas',
	account: 'Finpension 3a',
	pillar: 'Pillar 3a',
	value: '2345.00',
	cash: '145.00',
	stated: null,
	typed: false,
	changes: {
		'1y': { ...NOTHING, start: '1000.00', paid_in: '1000.00', twr: '22.83', mwr_a_year: true },
		ytd: { ...NOTHING, since: '2025-12-31', start: '1100.00', paid_in: '1000.00', twr: '11.67' }
	}
};
const FUND: Pension = {
	owner: 'Anna',
	account: 'Pillar 2 AXA',
	pillar: 'Pillar 2',
	value: '43000.00',
	cash: null,
	stated: '2026-06-30',
	typed: false,
	changes: { '1y': NOTHING, ytd: { ...NOTHING, since: '2025-12-31', start: '40000.00' } }
};

describe('the period', () => {
	it('is the one asked for, or the twelve months', () => {
		expect(periodOf('ytd')).toBe('ytd');
		expect(periodOf('1y')).toBe('1y');
		expect(periodOf(null)).toBe('1y');
		expect(periodOf('3y')).toBe('1y');
	});
});

describe('a pension', () => {
	it('moved by what it is worth now less what it was worth then', () => {
		expect(changeOf(THREE_A, '1y')).toBe('1345.00');
		expect(changeOf(FUND, 'ytd')).toBe('3000.00');
	});

	it('has no change over a period that starts before anything was known of it', () => {
		expect(changeOf(FUND, '1y')).toBeNull();
	});

	it('says whose it is, its pillar, and its certificate or its cash', () => {
		expect(described(FUND, true)).toBe('Anna · Pillar 2 · certificate of 30 Jun 2026');
		expect(described({ ...FUND, stated: '2026-09-20', typed: true }, false)).toBe(
			'Pillar 2 · typed for 20 Sep 2026'
		);
		expect(described(THREE_A, false)).toBe('Pillar 3a · 145 of it cash');
	});
});

describe('the group', () => {
	it('adds up the pensions and the money paid into those that say it', () => {
		expect(totals([FUND, THREE_A], 'ytd')).toEqual({
			value: '45345.00',
			change: '4245.00',
			paidIn: '1000.00',
			paidInPartly: true
		});
		expect(totals([THREE_A], 'ytd').paidInPartly).toBe(false);
	});

	it('has no change unless every pension’s is known', () => {
		expect(totals([FUND, THREE_A], '1y').change).toBeNull();
		expect(totals([FUND], '1y')).toEqual({
			value: '43000.00',
			change: null,
			paidIn: null,
			paidInPartly: false
		});
	});
});
