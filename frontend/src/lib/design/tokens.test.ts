import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const themeCss = readFileSync(new URL('./tokens/theme.css', import.meta.url), 'utf8');
const chartsDir = new URL('../charts/', import.meta.url);

describe('chart label type token', () => {
	it('defines --text-caption in the 11-12px range (0.75rem = 12px)', () => {
		const match = /--text-caption:\s*([\d.]+)rem;/.exec(themeCss);
		expect(match).not.toBeNull();
		const px = Number(match![1]) * 16;
		expect(px).toBeGreaterThanOrEqual(11);
		expect(px).toBeLessThanOrEqual(12);
	});

	it('sizes LayerChart axis and legend labels via --text-caption in @layer components', () => {
		const layerBlock = /@layer components\s*\{([\s\S]*?)\n\}/.exec(themeCss);
		expect(layerBlock).not.toBeNull();
		const body = layerBlock![1];
		expect(body).toContain('.lc-axis-label');
		expect(body).toContain('.lc-axis-tick-label');
		expect(body).toContain('.lc-legend-title');
		expect(body).toContain('.lc-legend-tick-text');
		expect(body).toContain('font-size: var(--text-caption);');
	});

	it('does not hardcode per-chart font sizes in frontend/src/lib/charts/', () => {
		const charts = readdirSync(chartsDir).filter((file) => file.endsWith('.svelte'));
		expect(charts.length).toBeGreaterThan(0);
		for (const file of charts) {
			const source = readFileSync(new URL(file, chartsDir), 'utf8');
			expect(source).not.toMatch(/\bfontSize\b|\bfont-size\b|text-\[\d+px\]/);
		}
	});
});
