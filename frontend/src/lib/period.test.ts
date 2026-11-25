import { describe, expect, it } from 'vitest';
import { choose, chosen, type Preset } from './period.js';

const OFFERED: Preset[] = [
	{ key: 'ytd', from: '2026', to: '', range: 'ytd' },
	{ key: '12m', from: '2025-10', to: '', range: '1y' },
	{ key: '2025', from: '2025', to: '2025', range: '' },
	{ key: 'all', from: '2024', to: '', range: 'all' }
];
const at = (query: string) => new URL(`http://scrooge.test/app/cashflow${query}`);

describe('chosen', () => {
	it('is the first one when nothing is asked', () => {
		expect(chosen(OFFERED, at(''))).toBe('ytd');
	});
	it('is the one of the period another page chose', () => {
		expect(chosen(OFFERED, at('?range=1y'))).toBe('12m');
		expect(chosen(OFFERED, at('?range=all'))).toBe('all');
	});
	it('is none for a period no range offered is', () => {
		expect(chosen(OFFERED, at('?range=3m'))).toBe('');
	});
	it('goes by the ends when they are given, whatever the period', () => {
		expect(chosen(OFFERED, at('?from=2025&to=2025&range=1y'))).toBe('2025');
		expect(chosen(OFFERED, at('?from=2025-03'))).toBe('');
	});
	it("is the page's own default when it is not the first one offered", () => {
		expect(chosen(OFFERED, at(''), '12m')).toBe('12m');
		expect(chosen(OFFERED, at('?range=ytd'), '12m')).toBe('ytd');
	});
	it('is none when nothing is offered', () => {
		expect(chosen([], at('?range=1y'))).toBe('');
	});
});

describe('choose', () => {
	it("sets the period even of the page's own default, so it goes along unchanged", () => {
		expect(choose(OFFERED, 'ytd')).toEqual({ from: null, to: null, range: 'ytd' });
	});
	it('clears the address for a default that is no period', () => {
		const years = [{ key: '2025', from: '2025', to: '2025', range: '' }, ...OFFERED];
		expect(choose(years, '2025')).toEqual({ from: null, to: null, range: null });
	});
	it('sets the period of one that is a period, so it goes along to other pages', () => {
		expect(choose(OFFERED, '12m')).toEqual({ from: null, to: null, range: '1y' });
	});
	it('sets the ends of a year of its own', () => {
		expect(choose(OFFERED, '2025')).toEqual({ from: '2025', to: '2025', range: null });
	});
	it("sets the period of the page's own default when it is not the first one offered", () => {
		expect(choose(OFFERED, '12m', '12m')).toEqual({ from: null, to: null, range: '1y' });
		expect(choose(OFFERED, 'ytd', '12m')).toEqual({ from: null, to: null, range: 'ytd' });
	});
	it('clears the address for a key not offered', () => {
		expect(choose(OFFERED, 'Someday')).toEqual({ from: null, to: null, range: null });
	});
});
