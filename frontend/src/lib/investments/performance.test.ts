import { describe, expect, it } from 'vitest';
import { setLanguage } from '#lib/i18n/index.svelte.js';
import type { InvestedDay, MeasuredAccount } from '#lib/types.js';
import {
	compared,
	described,
	direction,
	instead,
	missingBenchmarksNote,
	mwrPer,
	nearest,
	over,
	plot,
	rangeOf
} from './performance.js';

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
		{ day: '2026-09-01', value: '0.00', invested: '0.00', benchmarks: [] },
		{ day: '2026-09-02', value: '1000.00', invested: '1000.00', benchmarks: [] },
		{ day: '2026-09-11', value: '1100.00', invested: '1000.00', benchmarks: [] }
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

	it('draws each benchmark on the same scale, higher than the rest if it went higher', () => {
		const beside = series.map((point, index) => ({
			...point,
			benchmarks: [['0.00', '1000.00', '1250.00'][index]]
		}));
		const drawn = plot(beside, frame)!;
		expect([drawn.low, drawn.high]).toEqual([0, 1350]);
		expect(drawn.benchmarks).toEqual(['M10,110L20,35.9L110,17.4']);
		expect(plot(series, frame)!.benchmarks).toEqual([]);
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
		mwr: null,
		mwr_a_year: false,
		twr_since: null
	};

	it('says whose it is when both people are listed, and what it is made of', () => {
		expect(described(ibkr, true)).toBe('Lukas · IBKR CHF, IBKR USD');
		expect(described(ibkr, false)).toBe('IBKR CHF, IBKR USD');
		expect(described({ ...ibkr, accounts: ['IBKR USD'] }, true)).toBe('Lukas');
		expect(described({ ...ibkr, accounts: ['IBKR USD'] }, false)).toBe('');
	});

	it('says the day its return counts from when that is not the one of the whole', () => {
		const later = { ...ibkr, accounts: ['IBKR USD'], twr_since: '2026-09-15' };
		expect(described(later, false)).toBe('return from 15 Sep 2026');
		expect(described(later, true, '2026-09-15')).toBe('Lukas');
	});
});

describe('the money-weighted return', () => {
	it('is a rate a year from a year up, and over the period below', () => {
		expect(mwrPer({ mwr_a_year: true })).toBe('a year');
		expect(mwrPer({ mwr_a_year: false })).toBe('over the period');
	});
});

describe('the benchmarks', () => {
	const vt = { symbol: 'VT', return: '12.34' };
	const voo = { symbol: 'VOO', return: '-3.05' };

	it('are set beside the time-weighted return over the same days', () => {
		expect(compared('10.00', [vt, voo])).toBe('VT +12.3%, VOO −3.1% over the same period');
		expect(compared('10.00', [vt])).toBe('VT +12.3% over the same period');
	});

	it('are not set beside a return there is not, nor said when there are none', () => {
		expect(compared(null, [vt])).toBeNull();
		expect(compared('10.00', [])).toBeNull();
	});

	it('are named in the caption of the chart', () => {
		const worth = 'what that money would be worth had it gone into';
		expect(instead([])).toBe('');
		expect(instead([vt])).toBe(` In VT is ${worth} VT instead, on the same days.`);
		expect(instead([vt, voo, { symbol: 'ACWI', return: '1' }])).toBe(
			` In VT, in VOO and in ACWI are ${worth} each instead, on the same days.`
		);
	});

	it('names missing benchmarks in one quiet note without terminal commands', () => {
		expect(missingBenchmarksNote([])).toBeNull();
		expect(missingBenchmarksNote(['VT'])).toBe('VT will show once its prices are fetched.');
		expect(missingBenchmarksNote(['VT', 'VOO'])).toBe(
			'VT and VOO will show once their prices are fetched.'
		);
		expect(missingBenchmarksNote(['VT', 'VOO', 'ACWI'])).toBe(
			'VT, VOO and ACWI will show once their prices are fetched.'
		);
	});

	it('localizes performance labels and captions in Italian', () => {
		setLanguage('it');
		try {
			expect(over('ytd', '2026-01-01', '2026-09-30')).toBe('quest’anno');
			expect(mwrPer({ mwr_a_year: true })).toBe('annuo');
			expect(compared('10.00', [vt])).toBe('VT +12.3% nello stesso periodo');
			expect(instead([vt, voo, { symbol: 'ACWI', return: '1' }])).toBe(
				' Le linee VT, VOO e ACWI mostrano quanto varrebbe lo stesso capitale investito in ciascun indice negli stessi giorni.'
			);
			expect(missingBenchmarksNote([])).toBeNull();
			expect(missingBenchmarksNote(['VT'])).toBe(
				'VT apparirà non appena i suoi prezzi verranno scaricati.'
			);
			expect(missingBenchmarksNote(['VT', 'VOO'])).toBe(
				'VT e VOO appariranno non appena i loro prezzi verranno scaricati.'
			);
			expect(missingBenchmarksNote(['VT', 'VOO', 'ACWI'])).toBe(
				'VT, VOO e ACWI appariranno non appena i loro prezzi verranno scaricati.'
			);
		} finally {
			setLanguage('en');
		}
	});
});
