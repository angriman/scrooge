import { describe, expect, it } from 'vitest';
import type { Attention, RecurringChange } from '#lib/types.js';
import {
	changeText,
	folded,
	noticeLines,
	type NoticeLinks
} from './notices.js';

const links: NoticeLinks = {
	importing: (owner) => `/import?owner=${owner}`,
	checks: '/accounts#checks',
	transactions: (query) => `/transactions?${new URLSearchParams(query)}`
};

const nothing: Attention = {
	staged: [],
	behind: [],
	differ: 0,
	currency: 'CHF',
	unmatched: 'Non classificato',
	uncategorised: [],
	recurring: [],
	errors: []
};

const payment = (fields: Partial<RecurringChange>): RecurringChange => ({
	id: 's1',
	name: 'Streamio',
	account: 'Migros',
	owner: 'Lukas',
	every: 'monthly',
	kind: 'up',
	change: '2.00',
	since: '2026-09-03',
	alone: true,
	...fields
});

const household = { onePerson: false, readOnly: false };
const onePerson = { onePerson: true, readOnly: false };

describe('what needs a look', () => {
	it('is nothing when nothing waits', () => {
		expect(noticeLines(nothing, household, links)).toEqual([]);
	});

	it('keeps the statements first, then the rows to categorise and the payments', () => {
		const lines = noticeLines(
			{
				...nothing,
				staged: [{ owner: 'Anna', count: 2 }],
				behind: [
					{
						owner: 'Lukas',
						account: 'Swisscard',
						label: 'Swisscard',
						committed: '2026-09-30'
					}
				],
				differ: 1,
				uncategorised: [{ owner: 'Anna', count: 3 }],
				recurring: [
					payment({}),
					payment({ id: 'g1', name: 'Gymly', kind: 'new', change: '50.00' })
				]
			},
			household,
			links
		);
		expect(lines.map((line) => [line.kind, line.tone, line.text])).toEqual([
			[
				'staged',
				'waiting',
				'2 statements of Anna are staged and not yet counted.'
			],
			[
				'behind',
				'caution',
				'Lukas’s Swisscard statements end on 30 Sep 2026, over a month before the others.'
			],
			[
				'differ',
				'caution',
				'1 balance differs from what the statements say.'
			],
			[
				'uncategorised',
				'waiting',
				'3 rows of Anna are still in Non classificato.'
			],
			[
				'up',
				'caution',
				'Lukas’s Streamio costs 2.00 CHF more a month since 3 Sep.'
			],
			[
				'new',
				'waiting',
				'Lukas’s Gymly is a new payment: 50.00 CHF a month since 3 Sep.'
			]
		]);
		expect(new Set(lines.map((line) => line.key)).size).toBe(lines.length);
	});

	it('opens the rows to categorise and a payment’s rows, of their own person', () => {
		const lines = noticeLines(
			{
				...nothing,
				uncategorised: [{ owner: 'Anna', count: 1 }],
				recurring: [payment({})]
			},
			onePerson,
			links
		);
		expect(lines.map((line) => [line.text, line.action])).toEqual([
			[
				'1 row is still in Non classificato.',
				{
					label: 'Categorise',
					href: '/transactions?category=Non+classificato&owner=Anna'
				}
			],
			[
				'Streamio costs 2.00 CHF more a month since 3 Sep.',
				{
					label: 'See payments',
					href: '/transactions?merchant=Streamio&account=Migros&owner=Lukas'
				}
			]
		]);
	});

	it('lists no rows to categorise without a category for them', () => {
		const lines = noticeLines(
			{
				...nothing,
				unmatched: null,
				uncategorised: [{ owner: 'Anna', count: 1 }]
			},
			onePerson,
			links
		);
		expect(lines).toEqual([]);
	});

	it('says what changed, or only that it did when a rate is missing', () => {
		expect(
			changeText(
				payment({ every: 'yearly', change: '1200' }),
				'EUR',
				true
			)
		).toBe('Streamio costs 1’200.00 EUR more a year since 3 Sep.');
		expect(changeText(payment({ change: null }), 'EUR', true)).toBe(
			'Streamio costs more since 3 Sep.'
		);
		expect(
			changeText(payment({ kind: 'new', change: null }), 'EUR', true)
		).toBe('Streamio is a new payment since 3 Sep.');
	});

	it('keeps to five lines, the rest behind one that says how many more', () => {
		expect(folded([1, 2, 3, 4, 5])).toEqual({
			shown: [1, 2, 3, 4, 5],
			more: 0
		});
		expect(folded([1, 2, 3, 4, 5, 6])).toEqual({
			shown: [1, 2, 3, 4],
			more: 2
		});
		expect(folded([1, 2, 3], 2)).toEqual({ shown: [1], more: 2 });
	});

	it('opens no payment whose name other payees share, and only shows rows on a copy', () => {
		const lines = noticeLines(
			{
				...nothing,
				uncategorised: [{ owner: 'Anna', count: 1 }],
				recurring: [payment({ alone: false })]
			},
			{ onePerson: true, readOnly: true },
			links
		);
		expect(lines.map((line) => line.action?.label)).toEqual(['See them', undefined]);
	});

	it('keys each payment by its id, even when two share a name', () => {
		const lines = noticeLines(
			{ ...nothing, recurring: [payment({}), payment({ id: 's2' })] },
			onePerson,
			links
		);
		expect(lines.map((line) => line.key)).toEqual(['recurring|s1', 'recurring|s2']);
	});

	it('lists no payments when they were not asked for', () => {
		expect(noticeLines({ ...nothing, recurring: null }, onePerson, links)).toEqual([]);
	});
});
