import { describe, expect, it } from 'vitest';
import type { InvestedDay, MeasuredAccount } from '#lib/types.js';
import { described, direction, nearest, over, plot, rangeOf } from './performance.js';

describe('the range', () => {
	it('is the one asked for, or the year to date', () => {
		expect(rangeOf('3Y')).toBe('3y');
		expect(rangeOf('all')).toBe('all');
		expect(rangeOf(null)).toBe('ytd');
		expect(rangeOf('5y')).toBe('ytd');
	});

	it('is named after the figure it is of', () => {
		expect(over('ytd', '2026-01-01', '2026-09-30')).toBe('this year');
		expect(over('1m', '2026-09-01', '2026-09-30')).toBe('over the past month');
		expect(over('3y', '2023-10-02', '2026-09-30')).toBe('over the past 3 years');
		expect(over('all', '2024-03-01', '2026-09-30')).toBe('since 1 Mar 2024');
	});

	it('says where it starts when the history starts after it would', () => {
		expect(over('1y', '2026-08-31', '2026-09-30')).toBe('since 31 Aug 2026');
		expect(over('ytd', '2026-03-01', '2026-09-30')).toBe('since 1 Mar 2026');
	});
});

describe('which way a figure went', () => {
	it('is as its rounding writes it', () => {
		expect(direction('12.40')).toBe('up');
		expect(direction('-0.4')).toBe('flat');
		expect(direction('-0.4', 1)).toBe('down');
		expect(direction(null)).toBe('flat');
	});
});

describe('the chart', () => {
	const frame = {
		width: 110,
		height: 120,
		top: 10,
		right: 0,
		bottom: 10,
		left: 10
	};
	const series: InvestedDay[] = [
		{ day: '2026-09-01', value: '0.00', invested: '0.00' },
		{ day: '2026-09-02', value: '1000.00', invested: '1000.00' },
		{ day: '2026-09-11', value: '1100.00', invested: '1000.00' }
	];

	it('runs across by day and up by amount, both lines on one scale', () => {
		const drawn = plot(series, frame)!;
		expect(drawn.xs).toEqual([10, 20, 110]);
		// From nothing: the plot starts at zero and leaves a little room above the highest.
		expect([drawn.low, drawn.high]).toEqual([0, 1188]);
		expect(drawn.y(0)).toBe(110);
		expect(drawn.y(1188)).toBe(10);
		expect(drawn.value).toBe('M10,110L20,25.8L110,17.4');
		expect(drawn.invested).toBe('M10,110L20,25.8L110,25.8');
		expect(drawn.area).toBe('M10,110L20,25.8L110,17.4L110,110L10,110Z');
	});

	it('draws nothing without two days', () => {
		expect(plot(series.slice(0, 1), frame)).toBeNull();
	});

	it('finds the day nearest the pointer', () => {
		expect(nearest([10, 20, 110], 14)).toBe(0);
		expect(nearest([10, 20, 110], 70)).toBe(2);
	});
});

describe('an account', () => {
	const ibkr: MeasuredAccount = {
		owner: 'Lukas',
		name: 'IBKR',
		accounts: ['IBKR CHF', 'IBKR USD'],
		start: '0.00',
		end: '1000.00',
		net_flows: '900.00',
		gain: '100.00',
		twr: '11.10',
		mwr: null
	};

	it('says whose it is when both people are listed, and what it is made of', () => {
		expect(described(ibkr, true)).toBe('Lukas · IBKR CHF, IBKR USD');
		expect(described(ibkr, false)).toBe('IBKR CHF, IBKR USD');
		expect(described({ ...ibkr, accounts: ['IBKR USD'] }, true)).toBe('Lukas');
		expect(described({ ...ibkr, accounts: ['IBKR USD'] }, false)).toBe('');
	});
});
