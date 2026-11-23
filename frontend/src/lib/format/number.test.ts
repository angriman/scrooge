import { describe, expect, it } from 'vitest';
import {
	approximate,
	exact,
	money,
	percent,
	short,
	shortSigned,
	signed,
	signedPercent
} from './number.js';

describe('money', () => {
	it('groups thousands with an apostrophe and keeps two decimals', () => {
		expect(money('1234567.5')).toBe('1’234’567.50');
		expect(money('12')).toBe('12.00');
		expect(money('0.5')).toBe('0.50');
	});

	it('writes a negative amount with a minus sign', () => {
		expect(money('-1234.5')).toBe('−1’234.50');
	});

	it('rounds a tie to the even cent, as the pages do', () => {
		expect(money('0.125')).toBe('0.12');
		expect(money('0.135')).toBe('0.14');
		expect(money('-2.675')).toBe('−2.68');
	});

	it('drops the minus of an amount that rounds to zero', () => {
		expect(money('-0.001')).toBe('0.00');
	});

	it('rounds to whole francs when asked', () => {
		expect(money('482299.5', 0)).toBe('482’300');
		expect(money('482300.49', 0)).toBe('482’300');
	});

	it('keeps every digit of an amount too large for a float', () => {
		expect(money('12345678901234567.89')).toBe('12’345’678’901’234’567.89');
	});

	it('refuses what is not a decimal', () => {
		expect(() => money('12,50')).toThrow(RangeError);
		expect(() => money('')).toThrow(RangeError);
		expect(() => money('.')).toThrow(RangeError);
	});
});

describe('signed', () => {
	it('always writes the sign', () => {
		expect(signed('1500')).toBe('+1’500.00');
		expect(signed('-1500')).toBe('−1’500.00');
		expect(signed('0')).toBe('+0.00');
	});
});

describe('short', () => {
	it('writes an amount under a thousand whole, half up', () => {
		expect(short('849.5')).toBe('850');
		expect(short('-849.4')).toBe('−849');
		expect(short('-0.4')).toBe('0');
	});

	it('writes thousands to a tenth', () => {
		expect(short('12345')).toBe('12.3k');
		expect(short('12000')).toBe('12.0k');
		expect(short('999.5')).toBe('1.0k');
		expect(short('-75500')).toBe('−75.5k');
	});

	it('writes millions to a tenth, grouped', () => {
		expect(short('999960')).toBe('1.0M');
		expect(short('1250000')).toBe('1.3M');
		expect(short('1234500000')).toBe('1’234.5M');
	});
});

describe('shortSigned', () => {
	it('writes a plus only before a change that rounds above zero', () => {
		expect(shortSigned('12345')).toBe('+12.3k');
		expect(shortSigned('-850')).toBe('−850');
		expect(shortSigned('0.4')).toBe('0');
	});
});

describe('exact', () => {
	it('writes a quantity as stored, without the zeros that say nothing', () => {
		expect(exact('1234.500')).toBe('1’234.5');
		expect(exact('10.000')).toBe('10');
		expect(exact('0.00012345')).toBe('0.00012345');
		expect(exact('1200')).toBe('1’200');
	});
});

describe('percent', () => {
	it('writes a share to a tenth, half up', () => {
		expect(percent('42.35')).toBe('42.4%');
		expect(percent('7')).toBe('7.0%');
		expect(percent('3.14159', 2)).toBe('3.14%');
	});

	it('writes a change with its sign', () => {
		expect(signedPercent('4.25')).toBe('+4.3%');
		expect(signedPercent('-13.26')).toBe('−13.3%');
	});
});

describe('approximate', () => {
	it('gives the nearest float, for drawing', () => {
		expect(approximate('1234.5')).toBe(1234.5);
	});

	it('refuses what is not a decimal', () => {
		expect(() => approximate('NaN')).toThrow(RangeError);
	});
});
