import { describe, expect, it } from 'vitest';
import { setLanguage } from '#lib/i18n/index.svelte.js';
import { daysBack, moved, rangeLabel, rangeOptions } from './range.js';

describe('ranges', () => {
	it('start a month, a year or the year to date before the last day', () => {
		expect(daysBack('1m', '2026-09-30')).toBe(30);
		expect(daysBack('1y', '2026-09-30')).toBe(365);
		expect(daysBack('ytd', '2026-09-30')).toBe(273);
		expect(daysBack('ytd', '2026-01-01')).toBe(1);
		expect(daysBack('all', '2026-09-30')).toBeNull();
	});

	it('are named for what their change covers', () => {
		expect(rangeLabel('1m', '2026-08-31')).toBe('Past month');
		expect(rangeLabel('all', '2026-08-31')).toBe('Since 31 Aug 2026');
	});

	it('localizes range labels and pills in Italian', () => {
		setLanguage('it');
		try {
			expect(rangeOptions().find((o) => o.value === 'all')?.label).toBe('Tutto');
			expect(rangeLabel('1y', null)).toBe('Ultimo anno');
			expect(rangeLabel('all', '2026-08-31')).toBe('Dal 31 Ago 2026');
		} finally {
			setLanguage('en');
		}
	});
});

describe('moved, with accounts coming into view', () => {
	// An account worth 900 comes in on the third day: net worth rises by it, nothing is made.
	const values = ['1000.00', '1010.00', '1915.00', '1930.00'];
	const arrived = { '2': '900.00' };

	it('leaves out what an account brought, and sets the rest against all there was', () => {
		expect(moved(values, null, arrived)).toEqual({
			before: '1000.00',
			amount: '30.00',
			percent: '1.58',
			arrived: '900.00'
		});
	});

	it('counts what came in only within the range, not on its first day', () => {
		expect(moved(values, 1, arrived)).toMatchObject({ amount: '15.00', arrived: '0' });
		expect(moved(values, 2, arrived)).toMatchObject({ amount: '20.00', arrived: '900.00' });
	});
});

describe('moved', () => {
	const values = ['1000.00', '1100.00', '1050.50', '1200.00'];

	it('is the last day less the range’s first, exactly, and its percent', () => {
		expect(moved(values, 2)).toEqual({
			before: '1100.00',
			amount: '100.00',
			percent: '9.09',
			arrived: '0'
		});
		expect(moved(values, null)).toMatchObject({ amount: '200.00', percent: '20.00' });
	});

	it('is not known when the history is shorter than the range', () => {
		expect(moved(values, 4)).toBeNull();
		expect(moved([], null)).toBeNull();
	});

	it('has no percent of what was not above zero', () => {
		expect(moved(['0', '50'], 1)).toMatchObject({ before: '0', amount: '50', percent: null });
	});
});
