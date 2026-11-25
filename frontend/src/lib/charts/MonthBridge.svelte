<!--
@component
How net worth went from the eve of a month to its last day: what came in, what was spent, what
accounts brought when they first counted (when any did), and what markets and the rest did,
each a step from where the one before left it. The two ends
stand on a baseline below the lowest step, not on zero, so that steps of a few thousand show
next to a net worth of hundreds of thousands; their figures are written over them.
-->
<script lang="ts">
	import { bridgeAriaLabel, bridgeSteps, type BridgeInput } from './monthBridge.js';

	let { before, income, spent, arrived, other, after, from, to }: BridgeInput = $props();

	// Drawn at the size it is shown, so that its words are the size of the page's.
	let width = $state(320);
	const HEIGHT = 180;
	const TOP = 22; // room for the figure over the highest bar
	const BOTTOM = 26; // room for the names under the bars

	const steps = $derived(
		bridgeSteps({ before, income, spent, arrived, other, after, from, to }, width)
	);
	const COUNT = $derived(steps.length);
	const BAR = $derived(Math.min(72, (width / COUNT) * 0.62));
	const ariaLabel = $derived(bridgeAriaLabel(steps));

	const scale = $derived.by(() => {
		const levels = steps.flatMap((step) => (step.from === null ? [step.to] : [step.from, step.to]));
		const low = Math.min(...levels);
		const high = Math.max(...levels);
		const pad = (high - low) * 0.35 || Math.abs(high) * 0.02 || 1;
		const base = low - pad;
		const top = high + pad * 0.15;
		return (value: number) => TOP + ((top - value) / (top - base)) * (HEIGHT - TOP - BOTTOM);
	});
	const floor = HEIGHT - BOTTOM;
	const gap = $derived((width - BAR * COUNT) / (COUNT - 1));

	const fills = {
		end: 'var(--data-other)',
		gain: 'var(--gain)',
		loss: 'var(--loss)',
		arrived: 'var(--ink-muted)',
		other: 'var(--data-7)'
	} as const;
</script>

<div bind:clientWidth={width}>
	<svg {width} height={HEIGHT} class="block overflow-visible" role="img" aria-label={ariaLabel}>
		<line x1="0" x2={width} y1={floor} y2={floor} class="stroke-chart-grid" />
		{#each steps as step, index (step.name + index)}
			{@const x = index * (BAR + gap)}
			{@const y0 = step.from === null ? floor : scale(step.from)}
			{@const y1 = scale(step.to)}
			{@const top = Math.min(y0, y1)}
			<rect
				{x}
				y={top}
				width={BAR}
				height={Math.max(1, Math.abs(y1 - y0))}
				rx="2"
				fill={fills[step.tone]}
			/>
			{#if index < steps.length - 1}
				<line
					x1={x + BAR}
					x2={x + BAR + gap}
					{y1}
					y2={y1}
					class="stroke-ink-muted"
					stroke-dasharray="2 2"
				/>
			{/if}
			<text
				x={x + BAR / 2}
				y={top - 5}
				text-anchor="middle"
				class="fill-ink text-small tabular-nums"
				style:fill={step.tone === 'end' ? undefined : fills[step.tone]}>{step.label}</text
			>
			<text x={x + BAR / 2} y={HEIGHT - 6} text-anchor="middle" class="fill-ink-muted text-caption"
				>{step.name}</text
			>
		{/each}
	</svg>
</div>
