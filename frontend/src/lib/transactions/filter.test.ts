import { describe, expect, it } from 'vitest';
import { filtered, kindName, periodName, periods } from './filter.js';

const at = (search: string) => {
	const url = new URL(`http://x/transactions${search}`);
	return {
		pathname: url.pathname,
		search: url.search,
		searchParams: url.searchParams
	};
};

describe('the filter of the transactions page', () => {
	it('is set by any of its fields, not by whose figures they are', () => {
		expect(filtered(at(''))).toBe(false);
		expect(filtered(at('?owner=Anna'))).toBe(false);
		expect(filtered(at('?text=rent'))).toBe(true);
	});

	it('offers periods counted from today, across the turn of a year', () => {
		const offered = periods('2026-01-12');
		expect(offered.map((period) => [period.from, period.to])).toEqual([
			['2026-01', '2026-01'],
			['2025-12', '2025-12'],
			['2025-02', '2026-01'],
			['2026', '2026'],
			['2025', '2025']
		]);
	});

	it('names a period it offers by its name and any other by its ends', () => {
		expect(periodName('2026-10', '2026-10', '2026-11-23')).toBe('Last month');
		expect(periodName('2024', '2024', '2026-11-23')).toBe('2024');
		expect(periodName('2026-03', '2026-05', '2026-11-23')).toBe('Mar 2026 to May 2026');
		expect(periodName('2026-03-02', '', '2026-11-23')).toBe('Since 2 Mar 2026');
		expect(periodName('', '', '2026-11-23')).toBeNull();
	});

	it('names every kind of row in words', () => {
		expect(kindName('internal')).toBe('Between own accounts');
		expect(kindName('something_new')).toBe('Something new');
	});
});
