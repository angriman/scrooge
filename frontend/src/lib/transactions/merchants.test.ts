import { describe, expect, it } from 'vitest';
import type { KnownMerchant } from '#lib/types.js';
import { fold, narrow, typedName } from './merchants.js';

const known = (...names: string[]): KnownMerchant[] =>
	names.map((name, index) => ({ name, spent: String(1000 - index * 100), count: 1, last: '2026-08-01' }));

const names = (merchants: KnownMerchant[]) => merchants.map((merchant) => merchant.name);

describe('the merchants the chip offers', () => {
	const merchants = known('Hotel Adler', 'KONSUM ZUERICH', 'Coop Bern', 'Café Zürich', 'Coop Pronto');

	it('lists them all, the largest first, until something is typed', () => {
		expect(names(narrow(merchants, ''))).toEqual(names(merchants));
		expect(names(narrow(merchants, '   '))).toEqual(names(merchants));
		expect(names(narrow(merchants, '', 2))).toEqual(['Hotel Adler', 'KONSUM ZUERICH']);
	});

	it('narrows them to the names that hold every word typed, whatever the case', () => {
		expect(names(narrow(merchants, 'coop'))).toEqual(['Coop Bern', 'Coop Pronto']);
		expect(names(narrow(merchants, 'bern  COOP'))).toEqual(['Coop Bern']);
		expect(narrow(merchants, 'migros')).toEqual([]);
	});

	it('puts a name that starts with what is typed first, then one with a word that does', () => {
		const shops = known('Bäckerei Zur Ecke', 'Zurich Insurance', 'Hotel Zurlinden', 'Café Zürich');
		expect(names(narrow(shops, 'zur'))).toEqual([
			'Zurich Insurance',
			'Bäckerei Zur Ecke',
			'Hotel Zurlinden',
			'Café Zürich'
		]);
		expect(names(narrow(shops, 'ich'))).toEqual(['Zurich Insurance', 'Café Zürich']);
	});

	it('leaves accents out of the comparison', () => {
		expect(names(narrow(merchants, 'cafe zurich'))).toEqual(['Café Zürich']);
		expect(fold('  Café   ZÜRICH ')).toBe('cafe zurich');
	});

	it('lists no more than asked', () => {
		const many = known(...Array.from({ length: 80 }, (_, n) => `Shop ${'abcdefghij'[n % 10]}${n}`));
		expect(narrow(many, 'shop')).toHaveLength(50);
		expect(narrow(many, 'shop', 5)).toHaveLength(5);
	});
});

describe('what is typed, as a merchant of its own', () => {
	const merchants = known('Coop Bern', 'Café Zürich');

	it('is offered with its spaces tidied when no merchant known has that name', () => {
		expect(typedName(merchants, '  Coop   Basel ')).toBe('Coop Basel');
		expect(typedName([], 'Jelmoli')).toBe('Jelmoli');
	});

	it('is not offered when it is nothing or a merchant known', () => {
		expect(typedName(merchants, '  ')).toBeNull();
		expect(typedName(merchants, 'coop  BERN')).toBeNull();
		expect(typedName(merchants, 'Cafe Zurich')).toBeNull();
	});
});
