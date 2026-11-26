import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = readFileSync(new URL('./NetWorthCard.svelte', import.meta.url), 'utf8');

describe('NetWorthCard layout stability', () => {
	it('places the arrival note in row 3 beside the date on sm: and after moved on mobile', () => {
		expect(source).toContain(
			"[grid-template-areas:'title'_'figure'_'when'_'span'_'moved'_'arrived']"
		);
		expect(source).toContain("sm:[grid-template-areas:'title_span'_'figure_moved'_'when_arrived']");
		expect(source).not.toContain("'when_.'");
	});

	it('renders leftOut in [grid-area:arrived] outside [grid-area:moved]', () => {
		expect(source).toMatch(
			/<Performance[\s\S]*?\[grid-area:moved\][\s\S]*?sm:justify-end[\s\S]*?sm:text-right/
		);
		expect(source).toMatch(
			/\{#if leftOut\}\s*<p[^>]*\[grid-area:arrived\][^>]*sm:text-right[^>]*>/
		);
		expect(source).not.toMatch(/<div[^>]*\[grid-area:moved\]/);
	});

	it('reserves 2-row legend height and aligns the Show all button box with legend pills', () => {
		expect(source).toContain('min-h-17 px-5 pb-3');
		expect(source).not.toContain('min-h-10');
		expect(source).toMatch(
			/onclick=\{\(\) => \(hidden = new Set\(\)\)\}\s*class="[^"]*\bflex\b[^"]*\bitems-center\b[^"]*"/
		);
	});
});
