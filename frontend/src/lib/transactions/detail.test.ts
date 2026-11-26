import { describe, expect, it } from 'vitest';
import { detailNavigation, type Address } from '#lib/view.js';
import { formatDetail, parseDetail } from './detail.js';

function at(href: string): Address {
	const url = new URL(`http://x${href}`);
	return { pathname: url.pathname, search: url.search, searchParams: url.searchParams };
}

describe('transactions detail parameter', () => {
	it('round-trips categories and merchants, including spaces and colons', () => {
		const category = formatDetail('category', 'Doctor/Pharmacy');
		expect(category).toBe('category:Doctor/Pharmacy');
		expect(parseDetail(category)).toEqual({ kind: 'category', name: 'Doctor/Pharmacy' });

		const merchant = formatDetail('merchant', 'Cafe: Zurich Central');
		expect(merchant).toBe('merchant:Cafe: Zurich Central');
		expect(parseDetail(merchant)).toEqual({ kind: 'merchant', name: 'Cafe: Zurich Central' });
	});

	it('rejects null, empty, unknown kinds, or blank names', () => {
		expect(parseDetail(null)).toBeNull();
		expect(parseDetail('')).toBeNull();
		expect(parseDetail('Food')).toBeNull();
		expect(parseDetail(':Food')).toBeNull();
		expect(parseDetail('category:')).toBeNull();
		expect(parseDetail('category:   ')).toBeNull();
		expect(parseDetail('account:Migros')).toBeNull();
	});

	it('pushes on open/close and replaces history when switching between details', () => {
		const closed = at('/app/transactions?view=categories');
		expect(detailNavigation(closed, 'detail', formatDetail('category', 'Food'))).toEqual({
			href: '/app/transactions?view=categories&detail=category%3AFood',
			replace: false
		});

		const open = at('/app/transactions?view=categories&detail=category%3AFood');
		expect(detailNavigation(open, 'detail', formatDetail('merchant', 'Coop Bern'))).toEqual({
			href: '/app/transactions?view=categories&detail=merchant%3ACoop+Bern',
			replace: true
		});
		expect(detailNavigation(open, 'detail', null)).toEqual({
			href: '/app/transactions?view=categories',
			replace: false
		});
	});
});
