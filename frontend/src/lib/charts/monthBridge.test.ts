import { describe, expect, it } from 'vitest';
import { GROUP, MINUS, short } from '#lib/format/number.js';
import { setLanguage } from '#lib/i18n/index.svelte.js';
import { bridgeAriaLabel, bridgeSteps, type BridgeInput } from './monthBridge.js';

/** Parse a figure written by `short` / `shortSigned` (e.g. `1.0k`, `+5.0k`, `−200`) into a number. */
function parseShort(token: string): number {
	const normalized = token.replaceAll(MINUS, '-').replaceAll(GROUP, '');
	const match = /^([+-]?\d+(?:\.\d+)?)([kM]?)$/.exec(normalized);
	if (!match) throw new Error(`Unexpected figure token: ${JSON.stringify(token)}`);
	const scale = match[2] === 'M' ? 1_000_000 : match[2] === 'k' ? 1_000 : 1;
	return Number(match[1]) * scale;
}

/** Extract the start, intermediate move figures, and end from an English bridge aria-label. */
function readEnglishNumbers(label: string): { start: number; moves: number[]; end: number } {
	const match = /^From ([^ ]+) on [^:]+: (.+), to ([^ ]+) on .+$/.exec(label);
	if (!match) throw new Error(`Unexpected aria-label: ${JSON.stringify(label)}`);
	const [, startToken, middle, endToken] = match;
	const moves = middle.split(', ').map((part) => {
		const figure = part.split(' ').at(-1);
		if (!figure) throw new Error(`Missing figure in ${JSON.stringify(part)}`);
		return parseShort(figure);
	});
	return { start: parseShort(startToken), moves, end: parseShort(endToken) };
}

describe('monthBridge', () => {
	const withArrivals: BridgeInput = {
		before: '500',
		income: '10',
		spent: '200',
		arrived: '400',
		other: '100',
		after: '810',
		from: '30 Sep',
		to: '31 Oct'
	};

	it('includes new accounts in both the drawn steps and the screen-reader text, in order', () => {
		const steps = bridgeSteps(withArrivals, 600);
		expect(steps.map((step) => [step.name, step.label])).toEqual([
			['30 Sep', '500'],
			['Income', '+10'],
			['Spending', '−200'],
			['New accounts', '+400'],
			['Markets and other', '+100'],
			['31 Oct', '810']
		]);

		const label = bridgeAriaLabel(steps);
		expect(label).toBe(
			'From 500 on 30 Sep: income +10, spending −200, new accounts +400, markets and other +100, to 810 on 31 Oct'
		);

		const { start, moves, end } = readEnglishNumbers(label);
		expect(moves).toEqual([10, -200, 400, 100]);
		expect(start + moves.reduce((sum, move) => sum + move, 0)).toBe(end);
	});

	it('reads the k-scaled bridge with new accounts so its steps add up to the end', () => {
		const steps = bridgeSteps(
			{
				before: '1000',
				income: '10',
				spent: '200',
				arrived: '5000',
				other: '100',
				after: '5910',
				from: '30 Sep',
				to: '31 Oct'
			},
			600
		);
		const label = bridgeAriaLabel(steps);
		expect(label).toBe(
			'From 1.0k on 30 Sep: income +10, spending −200, new accounts +5.0k, markets and other +100, to 5.9k on 31 Oct'
		);

		const { start, moves, end } = readEnglishNumbers(label);
		const total = start + moves.reduce((sum, move) => sum + move, 0);
		expect(total).toBe(5910);
		expect(short(String(total))).toBe(short(String(end)));
	});

	it('omits new accounts when arrived is zero and still adds up to the end', () => {
		const steps = bridgeSteps(
			{
				before: '500',
				income: '100',
				spent: '200',
				arrived: '0.00',
				other: '50',
				after: '450',
				from: '30 Sep',
				to: '31 Oct'
			},
			600
		);
		expect(steps).toHaveLength(5);

		const label = bridgeAriaLabel(steps);
		expect(label).toBe(
			'From 500 on 30 Sep: income +100, spending −200, markets and other +50, to 450 on 31 Oct'
		);
		expect(label).not.toContain('new accounts');

		const { start, moves, end } = readEnglishNumbers(label);
		expect(start + moves.reduce((sum, move) => sum + move, 0)).toBe(end);
	});

	it('includes negative new accounts and still adds up to the end', () => {
		const steps = bridgeSteps(
			{
				before: '800',
				income: '100',
				spent: '50',
				arrived: '-300',
				other: '20',
				after: '570',
				from: '30 Sep',
				to: '31 Oct'
			},
			600
		);
		const label = bridgeAriaLabel(steps);
		expect(label).toBe(
			'From 800 on 30 Sep: income +100, spending −50, new accounts −300, markets and other +20, to 570 on 31 Oct'
		);

		const { start, moves, end } = readEnglishNumbers(label);
		expect(start + moves.reduce((sum, move) => sum + move, 0)).toBe(end);
	});

	it('uses full step names in the screen-reader text even when bar labels shorten on narrow screens', () => {
		const steps = bridgeSteps(withArrivals, 320);
		expect(steps.map((step) => step.name)).toEqual([
			'30 Sep',
			'Income',
			'Spending',
			'New',
			'Markets',
			'31 Oct'
		]);
		expect(bridgeAriaLabel(steps)).toBe(
			'From 500 on 30 Sep: income +10, spending −200, new accounts +400, markets and other +100, to 810 on 31 Oct'
		);
	});

	it('localizes the bridge steps and screen-reader text in Italian', () => {
		setLanguage('it');
		try {
			const stepsWithArrivals = bridgeSteps({ ...withArrivals, from: '30 Set', to: '31 Ott' }, 600);
			expect(bridgeAriaLabel(stepsWithArrivals)).toBe(
				'Da 500 al 30 Set: entrate +10, spese −200, nuovi conti +400, mercati e altro +100, a 810 al 31 Ott'
			);

			const stepsWithoutArrivals = bridgeSteps(
				{
					before: '500',
					income: '100',
					spent: '200',
					arrived: '0',
					other: '50',
					after: '450',
					from: '30 Set',
					to: '31 Ott'
				},
				600
			);
			expect(bridgeAriaLabel(stepsWithoutArrivals)).toBe(
				'Da 500 al 30 Set: entrate +100, spese −200, mercati e altro +50, a 450 al 31 Ott'
			);
		} finally {
			setLanguage('en');
		}
	});
});
