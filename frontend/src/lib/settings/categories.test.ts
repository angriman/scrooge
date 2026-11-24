import { describe, expect, it } from 'vitest';
import type { ListedCategory } from '#lib/types.js';
import { categoryKey, mergeInto, removable, renamable, rowsName, sections } from './categories.js';

const category = (
	name: string,
	side: ListedCategory['side'],
	parent: string | null = null,
	{ built_in = true, rows = 0 } = {}
): ListedCategory => ({ name, side, parent, built_in, rows });

const LISTED = [
	category('Spesa', 'uscite', 'Alimentari', { rows: 3 }),
	category('Trading', 'uscite', 'Investimenti'),
	category('Prestiti', 'uscite', 'Prestiti', { built_in: false }),
	category('Stipendio', 'entrate'),
	category('Trading', 'entrate'),
	category('Giroconto', 'transfer'),
	category('Topup Revolut', 'transfer')
];

describe('sections', () => {
	it('puts each category under its heading, in the order they come', () => {
		expect(sections(LISTED).map((s) => [s.heading, s.categories.map((c) => c.name)])).toEqual([
			['Alimentari', ['Spesa']],
			['Investimenti', ['Trading']],
			['Prestiti', ['Prestiti']],
			['Income', ['Stipendio', 'Trading']],
			['Between accounts', ['Giroconto', 'Topup Revolut']]
		]);
	});
});

describe('mergeInto', () => {
	it('offers the others of its kind, each name once', () => {
		expect(mergeInto(LISTED, LISTED[2])).toEqual([
			{ heading: 'Alimentari', names: ['Spesa'] },
			{ heading: 'Investimenti', names: ['Trading'] },
			{ heading: 'Income', names: ['Stipendio'] }
		]);
		expect(mergeInto(LISTED, LISTED[5])).toEqual([
			{ heading: 'Between accounts', names: ['Topup Revolut'] }
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
	});

	it('names rows and keys a category by its side', () => {
		expect([rowsName(0), rowsName(1), rowsName(4)]).toEqual(['no rows', '1 row', '4 rows']);
		expect(categoryKey(LISTED[4])).toBe('entrate|Trading');
	});
});
