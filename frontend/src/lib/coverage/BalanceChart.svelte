<!--
@component
Interactive balance-over-time chart for one account inside the side panel: plots month-end
balances with hover/touch inspection, zero reference line when crossing zero, and clean
fallbacks for a single month-end.
-->
<script lang="ts">
	import { day, month } from '#lib/format/date.js';
	import { money } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { Amount as AmountText, Day } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import { balancePlot, nearestBalancePoint } from './accounts.js';

	interface Props {
		/** Oldest first. */
		months: { day: Day; balance: AmountText }[];
		currency: string;
	}

	let { months, currency }: Props = $props();

	const WIDTH = 440;
	const HEIGHT = 128;

	const plot = $derived(balancePlot(months, WIDTH, HEIGHT));
	let hovered = $state<number | null>(null);
	let svgEl = $state<SVGSVGElement | null>(null);

	const active = $derived(plot ? plot.points[hovered ?? plot.points.length - 1] : null);

	const extremes = $derived.by(() => {
		if (!plot) return null;
		let minPoint = plot.points[0];
		let maxPoint = plot.points[0];
		for (const point of plot.points) {
			if (point.value < minPoint.value) minPoint = point;
			if (point.value > maxPoint.value) maxPoint = point;
		}
		return { min: minPoint.balance, max: maxPoint.balance };
	});

	const ariaLabel = $derived.by(() => {
		if (!plot) return '';
		const first = plot.points[0];
		const last = plot.points[plot.points.length - 1];
		return t('accounts.balanceChartAria', {
			start: `${money(first.balance, 0)} ${currency}`,
			first: day(first.day),
			end: `${money(last.balance, 0)} ${currency}`,
			last: day(last.day)
		});
	});

	function inspect(event: PointerEvent) {
		if (!plot || !svgEl) return;
		const rect = svgEl.getBoundingClientRect();
		if (rect.width <= 0) return;
		const x = ((event.clientX - rect.left) / rect.width) * WIDTH;
		hovered = nearestBalancePoint(plot.points, x);
	}
</script>

{#if plot && active && extremes}
	<section class="mb-6">
		<div class="mb-2 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
			<h3 class="text-small font-medium text-ink-secondary">{t('accounts.balanceOverTime')}</h3>
			<p class="flex items-baseline gap-2 text-small tabular-nums">
				<span class="text-ink-muted">{day(active.day)}</span>
				<Amount value={active.balance} {currency} places={0} class="font-medium text-ink" />
			</p>
		</div>

		<div class="rounded-control border border-line bg-raised/40 px-3 pt-2 pb-2">
			<div class="mb-1 flex items-center justify-between text-caption text-ink-muted tabular-nums">
				<span>{money(extremes.max, 0)} {currency}</span>
				{#if extremes.min !== extremes.max}
					<span>{money(extremes.min, 0)} {currency}</span>
				{/if}
			</div>
			<svg
				bind:this={svgEl}
				viewBox="0 0 {WIDTH} {HEIGHT}"
				class="h-32 w-full touch-pan-y select-none"
				role="img"
				aria-label={ariaLabel}
				onpointermove={inspect}
				onpointerdown={inspect}
				onpointerleave={() => (hovered = null)}
			>
				<line
					x1={plot.pad.left}
					x2={WIDTH - plot.pad.right}
					y1={plot.pad.top}
					y2={plot.pad.top}
					class="stroke-line/60"
					stroke-width="1"
				/>
				<line
					x1={plot.pad.left}
					x2={WIDTH - plot.pad.right}
					y1={HEIGHT - plot.pad.bottom}
					y2={HEIGHT - plot.pad.bottom}
					class="stroke-line/60"
					stroke-width="1"
				/>
				{#if plot.zeroY !== null}
					<line
						x1={plot.pad.left}
						x2={WIDTH - plot.pad.right}
						y1={plot.zeroY}
						y2={plot.zeroY}
						class="stroke-line-strong"
						stroke-width="1"
						stroke-dasharray="3 3"
					/>
				{/if}
				<path d={plot.area} fill="var(--brand)" fill-opacity="0.1" />
				<polyline
					points={plot.line}
					fill="none"
					stroke="var(--brand)"
					stroke-width="2"
					stroke-linejoin="round"
					stroke-linecap="round"
				/>
				{#if hovered !== null}
					<line
						x1={active.x}
						x2={active.x}
						y1={plot.pad.top}
						y2={HEIGHT - plot.pad.bottom}
						class="stroke-line-strong"
						stroke-width="1"
						stroke-dasharray="2 2"
					/>
				{/if}
				<circle
					cx={active.x}
					cy={active.y}
					r="3.5"
					fill="var(--brand)"
					stroke="var(--panel)"
					stroke-width="1.5"
				/>
			</svg>
			<div class="mt-1 flex items-center justify-between text-caption text-ink-muted">
				<span>{month(months[0].day)}</span>
				<span>{month(months[months.length - 1].day)}</span>
			</div>
		</div>
	</section>
{:else if months.length === 1}
	<p class="mb-6 text-small text-ink-muted">
		{t('accounts.balanceSingleMonth', { date: day(months[0].day) })}
	</p>
{/if}
