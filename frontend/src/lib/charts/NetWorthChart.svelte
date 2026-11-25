<!--
@component
Net worth day by day, its parts stacked: what is owned above zero, what is owed below it, and
net worth itself as the line over them. It draws no tooltip of its own:
the day under the pointer is handed out (`hovered`), and the page writes its figures where the
reader is already looking, in the headline and the legend.
-->
<script lang="ts">
	import { AreaChart, Spline, type ChartState } from 'layerchart';
	import { approximate, short } from '#lib/format/number.js';
	import { addDays, dayOf, monthOf, toDate } from '#lib/format/date.js';
	import type { Series } from '#lib/types.js';

	/** One day of the chart: its index in the series, and when it is. */
	export interface Day {
		index: number;
		day: string;
		date: Date;
	}

	interface Props {
		series: Series;
		/** The colour of each part, by name; a series without parts is drawn in `color`. */
		colors: ReadonlyMap<string, string>;
		color?: string;
		/** How many days before the last are shown; all of them when null. */
		back: number | null;
		/** Parts left out: the others are drawn, and the line is what they add up to. */
		hidden?: ReadonlySet<string>;
		/** The day under the pointer, null when there is none. */
		hovered?: Day | null;
		class?: string;
	}

	let {
		series,
		colors,
		color = 'var(--brand)',
		back,
		hidden = new Set(),
		hovered = $bindable(null),
		class: className
	}: Props = $props();

	// Each value read once, as a float to draw: the figures written stay those of the API.
	const total = $derived(series.total.map(approximate));
	const parts = $derived(
		series.parts
			.filter((part) => !hidden.has(part.name))
			.map((part) => ({ name: part.name, values: part.values.map(approximate) }))
	);
	// The line over the parts: net worth, or what the parts shown add up to.
	const line = $derived(
		hidden.size
			? total.map((_, index) => parts.reduce((added, part) => added + part.values[index], 0))
			: total
	);

	const days = $derived.by((): Day[] => {
		if (!series.first) return [];
		const first = back === null ? 0 : Math.max(0, total.length - 1 - back);
		return total.slice(first).map((_, offset) => {
			const index = first + offset;
			const day = addDays(series.first!, index);
			return { index, day, date: toDate(day) };
		});
	});

	const drawn = $derived(
		parts.length
			? parts.map((part) => ({
					key: part.name,
					label: part.name,
					value: (row: Day) => part.values[row.index],
					color: colors.get(part.name) ?? 'var(--data-other)'
				}))
			: series.parts.length
				? []
				: [{ key: 'total', label: 'Net worth', value: (row: Day) => total[row.index], color }]
	);

	// A change of range moves the chart to its new days instead of redrawing it, unless the
	// reader asked for less motion.
	const motion = matchMedia('(prefers-reduced-motion: reduce)').matches
		? ('none' as const)
		: { type: 'tween' as const, duration: 320 };

	// LayerChart's state: what is under the pointer.
	let context = $state<ChartState<Day>>();
	$effect(() => {
		hovered = context?.tooltip.data ?? null;
	});
</script>

<div class={className}>
	<AreaChart
		bind:context
		data={days}
		x="date"
		series={drawn}
		seriesLayout="stackDiverging"
		padding={{ left: 28, right: 60, bottom: 24, top: 8 }}
		{motion}
		grid={{ x: false, y: true }}
		rule={false}
		props={{
			area: { fillOpacity: 1, line: { strokeWidth: 1.25, class: 'stroke-panel' } },
			// A few months or less: its ticks are days, which a month's name would repeat.
			xAxis: { format: days.length <= 100 ? dayOf : monthOf, tickLength: 0, ticks: 6 },
			yAxis: {
				placement: 'right',
				format: (value: number) => short(String(value)),
				tickLength: 0,
				// Clear of the plot: the labels are read apart from the areas they sit beside.
				tickLabelProps: { dx: 10 }
			},
			grid: { class: '[&_line]:stroke-chart-grid' },
			highlight: {
				points: { r: 3, class: 'stroke-panel stroke-2' },
				lines: { class: 'stroke-ink-muted' }
			}
		}}
	>
		{#snippet aboveMarks()}
			{#if parts.length}
				<Spline y={(row: Day) => line[row.index]} class="fill-none stroke-ink stroke-[1.5]" />
			{/if}
		{/snippet}
		{#snippet tooltip()}{/snippet}
	</AreaChart>
</div>
