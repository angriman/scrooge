import { describe, expect, it } from 'vitest';
import type { CashFlowPeriod, Saved } from '#lib/types.js';
import { periodName, periodRows, totalRow } from './table.js';

const saved = (income: string, work: string, spent: string, saved: string): Saved => ({
	income,
	work,
	spent,
	saved,
	rate: income === '0.00' ? null : '50.000',
	rate_salary: work === '0.00' ? null : '62.500',
	from_work: null
});

const period = (name: string, first: string, totals: Saved): CashFlowPeriod => ({
	...totals,
	name,
	first,
	last: first,
	rate_12: null,
	rate_salary_12: null,
	spent_by: {},
	earned_by: {}
});

describe('the cash flow as a table', () => {
	it('says each period in words', () => {
		expect(periodName('2026-09')).toBe('Sep 2026');
		expect(periodName('2026-Q3')).toBe('Q3 2026');
		expect(periodName('2026')).toBe('2026');
	});

	it('puts the periods oldest first, with both rates', () => {
		const rows = periodRows([
			period('2026-08', '2026-08-01', saved('5000.00', '4000.00', '2500.00', '2500.00')),
			period('2026-07', '2026-07-01', saved('0.00', '0.00', '100.00', '-100.00'))
		]);
		expect(rows.map((row) => row.period)).toEqual(['Jul 2026', 'Aug 2026']);
		expect(rows[1]).toEqual({
			key: '2026-08',
			period: 'Aug 2026',
			income: '5000.00',
			salary: '4000.00',
			spent: '2500.00',
			saved: '2500.00',
			rate: '50.0%',
			rateSalary: '62.5%'
		});
		// Nothing came in: there is nothing to take a part of.
		expect([rows[0].rate, rows[0].rateSalary]).toEqual(['–', '–']);
	});

	it('sums up the range in a last row', () => {
		const total = totalRow(saved('5000.00', '4000.00', '2600.00', '2400.00'));
		expect([total.period, total.salary, total.rate, total.rateSalary]).toEqual([
			'In all',
			'4000.00',
			'50.0%',
			'62.5%'
		]);
	});

	it('leaves the rate on salary open when the range had no salary', () => {
		const total = totalRow(saved('300.00', '0.00', '100.00', '200.00'));
		expect([total.salary, total.rate, total.rateSalary]).toEqual(['0.00', '50.0%', '–']);
	});
});
