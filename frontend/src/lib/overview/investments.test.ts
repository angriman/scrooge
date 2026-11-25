import { error, redirect } from '@sveltejs/kit';
import { describe, expect, it } from 'vitest';
import { setLanguage } from '#lib/i18n/index.svelte.js';
import type {
	InvestmentIncome,
	InvestmentPerformance,
	Measured,
	PerformanceReport
} from '#lib/types.js';
import {
	incomeQuery,
	incomeShown,
	loaded,
	refusal,
	returnShown,
	returnSpan,
	returnsQuery,
	taxWithheld
} from './investments.js';

const address = (search: string) => {
	const url = new URL(`http://scrooge.test/${search}`);
	return { pathname: url.pathname, search: url.search, searchParams: url.searchParams };
};

const measured: Measured = {
	start: '1000',
	end: '1200',
	net_flows: '100',
	gain: '100',
	twr: '9.5',
	mwr: '9.1',
	mwr_a_year: true,
	twr_since: null
};

const report = (changes: Partial<PerformanceReport> = {}): PerformanceReport => ({
	currency: 'CHF',
	range: '1y',
	first: '2025-10-01',
	last: '2026-09-30',
	totals: measured,
	series: [],
	accounts: [],
	benchmarks: [],
	missing_benchmarks: [],
	...changes
});

describe('loaded', () => {
	it('keeps what the app sent, with its sentences', async () => {
		const sent = { figure: 1, errors: ['No rate for JPY.'] };
		expect(await loaded(Promise.resolve(sent), 'What they returned')).toEqual({
			sent,
			errors: ['No rate for JPY.']
		});
	});

	it('says what is not known, and why, when the app refuses', async () => {
		const refused = (async () => error(502, 'The prices could not be read.'))();
		expect(await loaded(refused, 'What they returned')).toEqual({
			sent: null,
			errors: ['What they returned is not known: The prices could not be read.']
		});
	});

	it('says the app could not be reached when nothing answered', async () => {
		const failed = loaded(Promise.reject(new TypeError('Failed to fetch')), 'What they paid');
		expect((await failed).errors[0]).toMatch(/^What they paid is not known: The app could not/);
	});

	it('still goes to the password page when not signed in', async () => {
		const away = (async () => redirect(303, '/login'))();
		await expect(loaded(away, 'What they paid')).rejects.toMatchObject({ location: '/login' });
	});
});

describe('returnSpan', () => {
	it('names the range when the history covers it', () => {
		expect(returnSpan('1y', report())).toBe('over the past year');
	});

	it('starts on the day money was first put in, when that was later', () => {
		const later = report({ totals: { ...measured, twr_since: '2026-03-03' } });
		expect(returnSpan('1y', later)).toBe('since 3 Mar 2026');
	});

	it('starts on the first day of a history shorter than the range', () => {
		expect(returnSpan('1y', report({ first: '2026-06-01' }))).toBe('since 1 Jun 2026');
	});

	it('says nothing without any days', () => {
		expect(returnSpan('1y', report({ first: null, last: null, totals: null }))).toBe('');
	});
});

describe('the links to the investments page', () => {
	it('keep whose figures and their currency, and the range unless it is the page’s own', () => {
		const url = address('?owner=Anna&currency=CHF&by=owner&range=1y');
		expect(returnsQuery(url, '1y').toString()).toBe('owner=Anna&currency=CHF&range=1y');
		expect(returnsQuery(address(''), 'ytd').toString()).toBe('');
	});

	it('open the income over the last twelve months', () => {
		expect(incomeQuery(address('?owner=Anna&range=3m')).toString()).toBe('owner=Anna&view=income');
	});
});

const performance = (changes: Partial<PerformanceReport> = {}): InvestmentPerformance => ({
	performance: report(changes),
	errors: []
});

const paidOut = { dividends: '120', interest: '30', tax: '-20', net: '130' };
const income: InvestmentIncome = {
	income: {
		currency: 'CHF',
		first: '2025-10-01',
		last: '2026-09-30',
		through: '2026-09-30',
		totals: paidOut,
		year_before: null,
		trailing: paidOut,
		months: [],
		holdings: []
	},
	logos: {},
	ranges: [],
	errors: []
};

describe('the card', () => {
	it('shows the return and what was paid when both are known', () => {
		expect(returnShown({ sent: performance(), errors: [] }, '1y')).toEqual({
			state: 'measured',
			span: 'over the past year',
			twr: '9.5',
			gain: '100',
			currency: 'CHF',
			errors: []
		});
		expect(incomeShown({ sent: income, errors: [] })).toEqual({
			paid: { net: '130', tax: '20', currency: 'CHF' },
			errors: []
		});
	});

	it('keeps what was paid when the return is not known, and says why once', () => {
		const why = 'What the investments returned is not known: The prices could not be read.';
		const shown = returnShown({ sent: null, errors: [why, why] }, '1y');
		expect(shown).toMatchObject({ state: 'unknown', twr: null, gain: null, errors: [why] });
		expect(incomeShown({ sent: income, errors: [] }).paid).not.toBeNull();
	});

	it('keeps the return when what was paid is not known', () => {
		const why = 'What the investments paid is not known: No rate for JPY.';
		expect(incomeShown({ sent: null, errors: [why] })).toEqual({ paid: null, errors: [why] });
		expect(returnShown({ sent: performance(), errors: [] }, '1y').state).toBe('measured');
	});

	it('says so when there is no investment account, or nothing was invested', () => {
		const none = performance({ first: null, last: null, totals: null });
		expect(returnShown({ sent: none, errors: [] }, '1y')).toMatchObject({
			state: 'none',
			gain: null
		});
		const idle = performance({ totals: { ...measured, twr: null, gain: '0' } });
		expect(returnShown({ sent: idle, errors: [] }, '1y')).toMatchObject({
			state: 'uninvested',
			gain: '0'
		});
	});

	it('says why a range chosen later is not known, signed out included', () => {
		const refused = (() => {
			try {
				error(500, 'Internal Error');
			} catch (failure) {
				return failure;
			}
		})();
		expect(refusal(refused, 'What they returned')).toBe(
			'What they returned is not known: Internal Error'
		);
		const away = (() => {
			try {
				redirect(303, '/login');
			} catch (failure) {
				return failure;
			}
		})();
		expect(refusal(away, 'What they returned')).toMatch(/sign in again/);
	});
});

describe('taxWithheld', () => {
	it('is positive, or null when nothing was withheld', () => {
		expect(taxWithheld({ dividends: '100', interest: '0', tax: '-15.00' })).toBe('15.00');
		expect(taxWithheld({ dividends: '100', interest: '0', tax: '0.00' })).toBeNull();
	});

	it('localizes spans and refusals in Italian', () => {
		setLanguage('it');
		try {
			expect(returnSpan('1y', report())).toBe('nell’ultimo anno');
			const refused = (() => {
				try {
					error(500, 'Internal Error');
				} catch (failure) {
					return failure;
				}
			})();
			expect(refusal(refused, 'Il rendimento')).toBe('Il rendimento non è noto: Internal Error');
		} finally {
			setLanguage('en');
		}
	});
});
