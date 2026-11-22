import { describe, expect, it } from 'vitest';
import { addDays, day, dayOfYear, month } from './date.js';

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

	it('refuses what is not an ISO day', () => {
		expect(() => day('30.09.2026')).toThrow(RangeError);
	});
});
