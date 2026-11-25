import { describe, expect, it } from 'vitest';
import { setLanguage } from '#lib/i18n/index.svelte.js';
import type { ListedCategory } from '#lib/types.js';
import {
	categoryKey,
	mergeable,
	mergeInto,
	movable,
	removable,
	renamable,
	rowsName,
	sections
} from './categories.js';

const category = (
	name: string,
	side: ListedCategory['side'],
	parent: string | null = null,
	{ built_in = true, rows = 0 } = {}
): ListedCategory => ({ name, side, parent, built_in, rows });

const LISTED = [
	category('Groceries', 'expenses', 'Food', { rows: 3 }),
	category('Trading', 'expenses', 'Investments'),
	category('Prestiti', 'expenses', 'Prestiti', { built_in: false }),
	category('Salary', 'income'),
	category('Trading', 'income'),
	category('Own transfer', 'transfer'),
	category('Revolut top-up', 'transfer')
];

describe('sections', () => {
	it('puts each category under its heading, in the order they come', () => {
		expect(sections(LISTED).map((s) => [s.heading, s.categories.map((c) => c.name)])).toEqual([
			['Food', ['Groceries']],
			['Investments', ['Trading']],
			['Prestiti', ['Prestiti']],
			['Income', ['Salary', 'Trading']],
			['Between accounts', ['Own transfer', 'Revolut top-up']]
		]);
	});
});

describe('mergeInto', () => {
	it('offers the others on every side it is on, each name once', () => {
		expect(mergeInto(LISTED, LISTED[2])).toEqual([
			{ heading: 'Food', names: ['Groceries'] },
			{ heading: 'Investments', names: ['Trading'] }
		]);
		expect(mergeInto(LISTED, LISTED[3])).toEqual([{ heading: 'Income', names: ['Trading'] }]);
		expect(mergeInto(LISTED, LISTED[1])).toEqual([]); // Trading is on both sides
		expect(mergeInto(LISTED, LISTED[5])).toEqual([
			{ heading: 'Between accounts', names: ['Revolut top-up'] }
		]);
	});
});

describe('what can be done', () => {
	it('renames and takes out only what was added, and that only when empty', () => {
		expect(renamable(LISTED[0])).toBe(false);
		expect(renamable(LISTED[2])).toBe(true);
		expect(removable(LISTED[2])).toBe(true);
		expect(removable({ ...LISTED[2], rows: 1 })).toBe(false);
		expect(removable(LISTED[0])).toBe(false);
		expect(movable(LISTED[2])).toBe(true);
		expect(movable(LISTED[0])).toBe(false);
	});

	it('keeps the category of what no rule matches', () => {
		expect(renamable(LISTED[2], 'Prestiti')).toBe(false);
		expect(removable(LISTED[2], 'Prestiti')).toBe(false);
		expect(mergeable(LISTED[2], 'Prestiti')).toBe(false);
		expect(mergeable(LISTED[2], null)).toBe(true);
	});

	it('names rows and keys a category by its side', () => {
		expect([rowsName(0), rowsName(1), rowsName(4)]).toEqual(['no rows', '1 row', '4 rows']);
		expect(categoryKey(LISTED[4])).toBe('income|Trading');

		setLanguage('it');
		try {
			expect([rowsName(0), rowsName(1), rowsName(4)]).toEqual([
				'0 movimenti',
				'1 movimento',
				'4 movimenti'
			]);
			expect(sections(LISTED).map((s) => s.heading)).toEqual([
				'Food',
				'Investments',
				'Prestiti',
				'Entrate',
				'Tra conti'
			]);
		} finally {
			setLanguage('en');
		}
	});
});
