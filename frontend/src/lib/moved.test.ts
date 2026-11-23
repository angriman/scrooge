import { describe, expect, it } from 'vitest';
import { movedTo } from './moved.js';

describe('a page that moved', () => {
	const old = { pathname: '/app/statements', search: '?owner=Anna&currency=EUR' };

	it('is found at its new address with the same query', () => {
		expect(movedTo('/app/accounts', old, null)).toBe('/app/accounts?owner=Anna&currency=EUR');
		expect(movedTo('/app/accounts', { pathname: '/app/statements', search: '' }, null)).toBe(
			'/app/accounts'
		);
	});

	it('keeps the hash of a visit straight to the old address', () => {
		expect(movedTo('/app/accounts', old, { pathname: '/app/statements', hash: '#checks' })).toBe(
			'/app/accounts?owner=Anna&currency=EUR#checks'
		);
	});

	it('does not take the hash of the page it was reached from', () => {
		expect(movedTo('/app/accounts', old, { pathname: '/app/cashflow', hash: '#month' })).toBe(
			'/app/accounts?owner=Anna&currency=EUR'
		);
	});
});
