import { describe, expect, it } from 'vitest';
import { movedTo, withBarHash } from './moved.js';

const moves = new Map([
	['/app/statements', '/app/accounts'],
	['/app/holdings', '/app/investments']
]);

describe('a page that moved', () => {
	const old = { pathname: '/app/statements', search: '?owner=Anna&currency=EUR', hash: '' };

	it('is found at its new address with the same query', () => {
		expect(movedTo(old, moves)).toBe('/app/accounts?owner=Anna&currency=EUR');
		expect(movedTo({ pathname: '/app/holdings', search: '', hash: '' }, moves)).toBe(
			'/app/investments'
		);
	});

	it('keeps the hash of a step inside the app to the old address', () => {
		expect(movedTo({ ...old, hash: '#checks' }, moves)).toBe(
			'/app/accounts?owner=Anna&currency=EUR#checks'
		);
	});

	it('is found with a slash at the end too', () => {
		expect(movedTo({ pathname: '/app/statements/', search: '', hash: '#checks' }, moves)).toBe(
			'/app/accounts#checks'
		);
	});

	it('leaves every other address where it is', () => {
		const others = ['/app/accounts', '/app', '/app/', '/', '/statements', '/app/statementsx'];
		for (const pathname of others) {
			expect(movedTo({ pathname, search: '', hash: '#checks' }, moves)).toBeNull();
		}
	});
});

describe('the hash of a visit straight to an old address', () => {
	const url = { pathname: '/app/statements', search: '?owner=Anna' };

	it('is the one the address bar shows', () => {
		expect(withBarHash(url, { pathname: '/app/statements', search: '', hash: '#checks' })).toEqual({
			...url,
			hash: '#checks'
		});
	});

	it('is not taken from the page it was reached from', () => {
		expect(withBarHash(url, { pathname: '/app/cashflow', search: '', hash: '#month' })).toEqual({
			...url,
			hash: ''
		});
		expect(withBarHash(url, null)).toEqual({ ...url, hash: '' });
	});
});
