import { describe, expect, it } from 'vitest';
import { oneOf, pick, withQuery, type Address } from './view.js';

function at(href: string): Address {
	const url = new URL(`http://x${href}`);
	return { pathname: url.pathname, search: url.search, searchParams: url.searchParams };
}

describe('the address of a page', () => {
	it('adds, replaces and removes query fields, and drops ? when none remain', () => {
		expect(withQuery(at('/app/cashflow?owner=Anna&by=month'), { by: null, from: '2025' })).toBe(
			'/app/cashflow?owner=Anna&from=2025'
		);
		expect(withQuery(at('/app/cashflow?by=year'), { by: null })).toBe('/app/cashflow');
	});

	it('picks only the non-empty query fields an API asks for', () => {
		const picked = pick(at('/app/transactions?owner=Lukas&text=&kind=expense&extra=1'), [
			'owner',
			'text',
			'kind',
			'missing'
		]);
		expect(picked.toString()).toBe('owner=Lukas&kind=expense');
	});

	it('chooses an allowed query value or falls back', () => {
		expect(oneOf(at('/app?by=quarter'), 'by', ['month', 'quarter', 'year'], 'month')).toBe(
			'quarter'
		);
		expect(oneOf(at('/app?by=decade'), 'by', ['month', 'quarter', 'year'], 'month')).toBe('month');
		expect(oneOf(at('/app'), 'by', ['month', 'quarter', 'year'], 'month')).toBe('month');
	});
});
