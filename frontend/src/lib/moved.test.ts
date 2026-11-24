import { describe, expect, it } from 'vitest';
import { asked, barHash, movedTo, stepTo, type Step, type Tagged } from './moved.js';

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

describe('a step to an old address', () => {
	const ours = 'http://127.0.0.1:8765';
	const step = (fields: Partial<Step> = {}, origin = ours): Step => ({
		type: 'link',
		willUnload: false,
		to: { url: { origin, pathname: '/app/statements', search: '', hash: '#checks' } },
		...fields
	});

	it('goes to the new address inside the app, by a link, a form or a goto', () => {
		for (const type of ['link', 'form', 'goto']) {
			expect(stepTo(step({ type }), ours, moves)).toBe('/app/accounts#checks');
		}
	});

	it('is left alone when it goes to another site', () => {
		expect(stepTo(step({}, 'https://other.example'), ours, moves)).toBeNull();
	});

	it('is left alone when it loads the document again', () => {
		expect(stepTo(step({ willUnload: true }), ours, moves)).toBeNull();
		expect(stepTo(step({ type: 'leave', to: null }), ours, moves)).toBeNull();
	});

	it('is left alone on Back and Forward', () => {
		expect(stepTo(step({ type: 'popstate' }), ours, moves)).toBeNull();
	});

	it('is left alone when the address did not move', () => {
		const to = { url: { origin: ours, pathname: '/app/cashflow', search: '', hash: '' } };
		expect(stepTo(step({ to }), ours, moves)).toBeNull();
	});
});

describe('what a link or form asks of a step', () => {
	const element = (attributes: Record<string, string>, parentElement: Tagged | null = null) => ({
		getAttribute: (name: string) => attributes[name] ?? null,
		parentElement
	});

	it('is nothing when nothing says, or there is no element', () => {
		expect(asked(element({}, element({})))).toEqual({});
		expect(asked(null)).toEqual({});
	});

	it('is what the element says, or the nearest one around it', () => {
		const around = element({ 'data-sveltekit-reset': 'false' });
		const link = element({ 'data-sveltekit-replacestate': '' }, around);
		expect(asked(link)).toEqual({ replace: true, reset: false });
	});

	it('is the nearest that says, not one further out', () => {
		const around = element({ 'data-sveltekit-replacestate': 'true' });
		const link = element({ 'data-sveltekit-replacestate': 'false' }, around);
		expect(asked(link)).toEqual({ replace: false });
	});
});

describe('the hash of a visit straight to an old address', () => {
	const url = { pathname: '/app/statements' };

	it('is the one the address bar shows', () => {
		expect(barHash(url, { pathname: '/app/statements', search: '', hash: '#checks' })).toBe(
			'#checks'
		);
	});

	it('is not taken from the page it was reached from', () => {
		expect(barHash(url, { pathname: '/app/cashflow', search: '', hash: '#month' })).toBe('');
		expect(barHash(url, null)).toBe('');
	});
});
