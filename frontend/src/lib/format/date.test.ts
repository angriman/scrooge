import { describe, expect, it } from 'vitest';
import {
	addDays,
	day,
	dayOf,
	dayOfYear,
	daysBetween,
	month,
	monthName,
	monthOf,
	toDate
} from './date.js';

describe('days', () => {
	it('writes the day an ISO day names, whatever the time zone', () => {
		expect(day('2026-09-30')).toBe('30 Sep 2026');
		expect(day('2026-01-01')).toBe('1 Jan 2026');
		expect(dayOfYear('2026-12-31')).toBe('31 Dec');
		expect(month('2026-02-14')).toBe('Feb 2026');
	});

	it('counts days across months, years and a leap day', () => {
		expect(addDays('2028-02-28', 1)).toBe('2028-02-29');
		expect(addDays('2026-12-31', 1)).toBe('2027-01-01');
		expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
	});

	it('places a day at its local midnight, and names the month of one', () => {
		const date = toDate('2026-10-01');
		expect([date.getFullYear(), date.getMonth(), date.getDate(), date.getHours()]).toEqual([
			2026, 9, 1, 0
		]);
		expect(monthOf(date)).toBe('Oct 2026');
		expect(dayOf(date)).toBe('1 Oct');
	});

	it('names a month written as the API writes it', () => {
		expect(monthName('2026-10')).toBe('October 2026');
		expect(() => monthName('October')).toThrow(RangeError);
	});

	it('counts the days between two days, across a leap day', () => {
		expect(daysBetween('2025-12-31', '2026-09-30')).toBe(273);
		expect(daysBetween('2028-02-28', '2028-03-01')).toBe(2);
		expect(daysBetween('2026-09-30', '2026-09-30')).toBe(0);
	});

	it('refuses what is not an ISO day', () => {
		expect(() => day('30.09.2026')).toThrow(RangeError);
	});
});
