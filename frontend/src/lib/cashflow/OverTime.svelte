<!--
@component
Each period of the range as a column: what came in stacked above the line, by where it came
from, and what went out below it, by category; the savings rate of each period runs across as
a line. A column pointed at, or chosen with the keyboard, is said in full beneath.
-->
<script lang="ts">
	import { month as monthName } from '#lib/format/date.js';
	import { approximate, percent, short } from '#lib/format/number.js';
	import type { CashFlowPeriod } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import { GROUPS, groupName, incomeColor, PAID_BACK } from './names.js';

	interface Props {
		periods: CashFlowPeriod[];
		/** The categories stacked below the line, and their colours. */
		colors: Map<string, string>;
		currency: string;
	}

	let { periods, colors, currency }: Props = $props();

	let width = $state(640);
	const HEIGHT = 240;
	const TOP = 12;
	const BOTTOM = 22; // room for the names of the periods
	const LEFT = 44; // room for the amounts
	const RIGHT = 36; // room for the rate

	const parts = $derived([...colors.keys()].filter((name) => name !== PAID_BACK));
	const highest = $derived(Math.max(1, ...periods.map((p) => approximate(p.income))));
	const lowest = $derived(Math.max(1, ...periods.map((p) => approximate(p.spent))));
	const plot = $derived(HEIGHT - TOP - BOTTOM);
	// The line sits where what came in and what went out share the height in proportion.
	const zero = $derived(TOP + (plot * highest) / (highest + lowest));
	const scale = $derived(plot / (highest + lowest));
	const step = $derived((width - LEFT - RIGHT) / Math.max(1, periods.length));
	const bar = $derived(Math.max(3, Math.min(36, step * 0.6)));

	const columns = $derived(
		periods.map((period, index) => {
			const x = LEFT + step * index + (step - bar) / 2;
			let up = zero;
			const above = GROUPS.map((group) => {
				const height = Math.max(0, approximate(period.earned_by[group] ?? '0')) * scale;
				up -= height;
				return { name: group, y: up, height, fill: incomeColor(group) };
			});
			let down = zero;
			const below = parts.map((name) => {
				const height = Math.max(0, approximate(period.spent_by[name] ?? '0')) * scale;
				const y = down;
				down += height;
				return { name, y, height, fill: colors.get(name) ?? 'var(--data-other)' };
			});
			return { period, x, above, below };
		})
	);
	// The rate of each period, from no saving (the line) to all of it saved (the top).
	const rate = $derived(
		periods.map((period, index) => {
			const value = period.rate === null ? null : Math.min(100, Math.max(0, approximate(period.rate)));
			return value === null ? null : { x: LEFT + step * index + step / 2, y: zero - ((zero - TOP) * value) / 100 };
		})
	);
	const path = $derived(
		rate
			.map((point, index) => (point ? `${rate[index - 1] ? 'L' : 'M'}${point.x},${point.y}` : ''))
			.join('')
	);
	const label = (name: string) =>
		/^\d{4}-\d{2}$/.test(name) ? monthName(`${name}-01`).replace(/ \d{4}$/, '') : name.replace(/^\d{4}-/, '');
	// Names every few columns when they would crowd.
	const every = $derived(Math.ceil(42 / Math.max(1, step)));

	let shown = $state<number | null>(null);
	const detail = $derived(periods[shown ?? periods.length - 1]);
</script>

<div bind:clientWidth={width}>
	<svg
		viewBox="0 0 {width} {HEIGHT}"
		class="block h-[240px] w-full"
		role="img"
		aria-label="Income above the line and spending below it, period by period, with the savings rate"
	>
		<text x={LEFT - 6} y={TOP + 4} text-anchor="end" class="fill-ink-muted text-caption">{short(String(highest))}</text>
		<text x={LEFT - 6} y={HEIGHT - BOTTOM} text-anchor="end" class="fill-ink-muted text-caption">−{short(String(lowest))}</text>
		<text x={width - RIGHT + 6} y={TOP + 4} class="fill-brand text-caption">100%</text>
		{#each columns as column, index (column.period.name)}
			<g
				role="presentation"
				onpointerenter={() => (shown = index)}
				onpointerleave={() => (shown = null)}
				opacity={shown === null || shown === index ? 1 : 0.45}
			>
				<rect x={LEFT + step * index} y={TOP} width={step} height={plot} fill="transparent" />
				{#each column.above as piece (piece.name)}
					{#if piece.height > 0}<rect x={column.x} y={piece.y} width={bar} height={piece.height} fill={piece.fill} />{/if}
				{/each}
				{#each column.below as piece (piece.name)}
					{#if piece.height > 0}<rect x={column.x} y={piece.y} width={bar} height={piece.height} fill={piece.fill} opacity="0.85" />{/if}
				{/each}
				{#if index % every === 0 || index === columns.length - 1}
					<text x={column.x + bar / 2} y={HEIGHT - 6} text-anchor="middle" class="fill-ink-muted text-caption">
						{label(column.period.name)}
					</text>
				{/if}
			</g>
		{/each}
		<line x1={LEFT} x2={width - RIGHT} y1={zero} y2={zero} class="stroke-line-strong" />
		<path d={path} fill="none" class="stroke-brand" stroke-width="2" stroke-linejoin="round" />
		{#each rate as point, index (index)}
			{#if point}<circle cx={point.x} cy={point.y} r={shown === index ? 4 : 2.5} class="fill-brand" />{/if}
		{/each}
	</svg>

	{#if detail}
		<p class="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-small text-ink-secondary" aria-live="polite">
			<span class="font-medium text-ink">{label(detail.name)} {detail.name.slice(0, 4)}</span>
			<span>In <Amount value={detail.income} {currency} places={0} class="text-gain" /></span>
			<span>Out <Amount value={detail.spent} {currency} places={0} class="text-ink" /></span>
			<span>Saved <Amount value={detail.saved} {currency} places={0} class="text-ink" /></span>
			{#if detail.rate !== null}<span>Rate <span class="text-brand">{percent(detail.rate, 0)}</span></span>{/if}
			{#if detail.rate_salary !== null}<span>On salary <span class="text-brand">{percent(detail.rate_salary, 0)}</span></span>{/if}
		</p>
	{/if}

	<ul class="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-caption text-ink-secondary">
		{#each GROUPS as group (group)}
			<li class="flex items-center gap-1.5">
				<span class="size-2 rounded-[2px]" style:background={incomeColor(group)}></span>{groupName(group)}
			</li>
		{/each}
		{#each parts as name (name)}
			<li class="flex items-center gap-1.5">
				<span class="size-2 rounded-[2px] opacity-85" style:background={colors.get(name)}></span>{name}
			</li>
		{/each}
		<li class="flex items-center gap-1.5"><span class="h-0.5 w-3 rounded bg-brand"></span>Savings rate</li>
	</ul>
</div>
