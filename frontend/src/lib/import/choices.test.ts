import { describe, expect, it } from 'vitest';
import type { StagedRow } from '#lib/types.js';
import { fresh, groupKey, toSend } from './choices.js';

const row = (id: string, category: string | null = null): StagedRow => ({
	id,
	date: '2026-09-01',
	account: 'Migros',
	amount: '-10',
	currency: 'CHF',
	counterparty: 'ACME',
	description: 'ACME',
	category,
	side: null,
	choosable: true,
	statement: 1
});

const rent = { name: 'Landlord AG', incoming: false, ids: ['a', 'b'] };
const nameless = { name: '', incoming: true, ids: ['c'] };
const rows = new Map([row('a'), row('b'), row('c'), row('d', 'Groceries')].map((r) => [r.id, r]));

describe('the categories chosen on the import page', () => {
	it('start empty, remembered where a counterparty can be, and keep what was chosen', () => {
		const first = fresh([rent, nameless], true);
		expect(first).toEqual({
			'a b': { category: '', rule: true },
			c: { category: '', rule: false }
		});
		first[groupKey(rent)].category = 'Rent';
		expect(fresh([rent], false, first)[groupKey(rent)].category).toBe('Rent');
		expect(fresh([rent], false)[groupKey(rent)].rule).toBe(false);
	});

	it('send the rows chosen on their own before their group, and nothing unchanged', () => {
		const chosen = fresh([rent, nameless], true);
		chosen[groupKey(rent)] = { category: 'Rent', rule: true };
		const own = { b: 'Utilities', d: 'Groceries', c: '' };
		expect(toSend([rent, nameless], chosen, own, rows)).toEqual([
			{ ids: ['b'], category: 'Utilities', rule: false },
			{ ids: ['a'], category: 'Rent', rule: true }
		]);
	});
});
