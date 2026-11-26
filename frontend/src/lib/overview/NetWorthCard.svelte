<!--
@component
The lead of the first page: net worth, how it moved over the range shown, and its chart. The
legend is also a filter: a part clicked away leaves the chart, and the headline and its change
become what the parts still shown add up to. Pointing at a day writes that day's figures in the
headline and the legend. The chart can give way to a table of the month-ends.
-->
<script lang="ts" module>
	export const VIEWS = [
		{ value: 'chart', label: 'Chart' },
		{ value: 'table', label: 'Table' }
	] as const;
	export type View = (typeof VIEWS)[number]['value'];
</script>

<script lang="ts">
	import NetWorthChart, { type Day } from '#lib/charts/NetWorthChart.svelte';
	import { netWorthChartLabel } from '#lib/charts/netWorth.js';
	import { addDays, day } from '#lib/format/date.js';
	import { money, short, sum } from '#lib/format/number.js';
	import { joinList, t } from '#lib/i18n/index.svelte.js';
	import type { NetWorthMonths, Series } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Performance from '#lib/ui/Performance.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import Swatch from '#lib/ui/Swatch.svelte';
	import { cn } from '#lib/ui/cn.js';
	import MonthEnds from './MonthEnds.svelte';
	import {
		arrivedBetween,
		change as moveOf,
		daysBack,
		rangeLabel,
		rangeOptions,
		type Range
	} from './range.js';

	interface Props {
		series: Series;
		colors: ReadonlyMap<string, string>;
		nameOf?: (name: string) => string;
		/** What the headline is of: `Net worth`, `Anna’s net worth`. */
		title: string;
		currency: string;
		/** The last day the statements of those shown cover, when it is before the last day and
		 * not after the header's "Complete up to" day (`statementsUpTo`). */
		through: string | null;
		range: Range;
		onrange: (range: Range) => void;
		/** How the chart may be cut, and how it is; nothing to choose when there is one way. */
		breakdowns: readonly { value: string; label: string }[];
		by: string;
		onby: (by: string) => void;
		/** The chart, or the table of the month-ends. */
		view: View;
		onview: (view: View) => void;
		/** The month-ends, asked for once the table is shown. */
		months: Promise<NetWorthMonths> | null;
	}

	let {
		series,
		colors,
		nameOf = (name: string) => name,
		title,
		currency,
		through,
		range,
		onrange,
		breakdowns,
		by,
		onby,
		view,
		onview,
		months
	}: Props = $props();

	let hidden = $state<ReadonlySet<string>>(new Set());
	let hovered = $state<Day | null>(null);

	$effect.pre(() => {
		void by;
		hidden = new Set();
		hovered = null;
	});

	const last = $derived(series.total.length - 1);
	const lastDay = $derived(addDays(series.first!, last));
	const back = $derived(daysBack(range, lastDay));
	const parts = $derived(series.parts.filter((part) => !hidden.has(part.name)));

	/** What is shown adds up to this on the `index`th day: net worth, or the parts left in. */
	const shownOn = (index: number) =>
		hidden.size ? sum(parts.map((part) => part.values[index])) : series.total[index];

	const at = $derived(hovered?.index ?? last);
	const headline = $derived(shownOn(at));
	// What moved over the range, leaving out what accounts brought when they came into view.
	const change = $derived.by(() => {
		const start = back === null ? 0 : last - back;
		if (start < 0) return null;
		const arrivals = hidden.size ? parts.map((part) => part.arrived) : [series.arrived];
		return moveOf(shownOn(start), shownOn(last), arrivedBetween(arrivals, start, last));
	});
	const leftOut = $derived(change && change.arrived !== '0' ? change.arrived : null);

	// What the headline adds up: the fewer names, whether those left out or those kept.
	const shownTitle = $derived(
		hidden.size === 0
			? title
			: hidden.size <= parts.length
				? t('overview.without', { title, excluded: joinList([...hidden].map(nameOf)) })
				: joinList(parts.map((part) => nameOf(part.name)))
	);
	const firstShownDay = $derived(
		series.first ? addDays(series.first, back === null ? 0 : Math.max(0, last - back)) : null
	);
	const chartLabel = $derived(
		netWorthChartLabel(
			shownTitle,
			by,
			parts.length > 0 && hidden.size <= parts.length,
			firstShownDay,
			lastDay
		)
	);

	const views = $derived([
		{ value: 'chart' as const, label: t('overview.chart') },
		{ value: 'table' as const, label: t('overview.table') }
	]);
	const ranges = $derived(rangeOptions());

	/** Leave `name` out of the chart, or bring it back; leaving out the last one brings all back. */
	function toggle(name: string) {
		const next = new Set(hidden);
		if (next.has(name)) next.delete(name);
		else next.add(name);
		hidden = next.size >= series.parts.length ? new Set() : next;
	}
</script>

<section class="rounded-lead border border-line bg-panel" aria-labelledby="net-worth">
	<!-- Net worth and how it moved, side by side: their names on one line, their figures on one
	     baseline. On a phone, the one under the other. -->
	<div
		class="grid grid-cols-1 gap-x-10 px-5 pt-5 [grid-template-areas:'title'_'figure'_'when'_'span'_'moved'] sm:grid-cols-[minmax(0,1fr)_auto] sm:items-baseline sm:[grid-template-areas:'title_span'_'figure_moved'_'when_.']"
	>
		<h1 id="net-worth" class="truncate text-small text-ink-secondary [grid-area:title]">
			{shownTitle}
		</h1>
		<p
			class="mt-1 text-hero font-medium tracking-tight text-ink [grid-area:figure]"
			aria-live="polite"
		>
			<Amount value={headline} {currency} />
		</p>
		<p class="mt-1 text-small text-ink-muted [grid-area:when]">
			{#if hovered}
				{t('overview.onDay', { date: day(hovered.day) })}
			{:else}
				{t('overview.asOf', { date: day(lastDay) })}{#if through}{t('overview.statementsUpTo', {
						date: day(through)
					})}{/if}
			{/if}
		</p>
		<p class="mt-4 text-small text-ink-secondary [grid-area:span] sm:mt-0 sm:text-right">
			{rangeLabel(range, series.first)}
		</p>
		<div class="mt-1 [grid-area:moved] sm:text-right">
			<Performance
				amount={change?.amount ?? null}
				percent={change?.percent ?? null}
				class="sm:justify-end"
			/>
			{#if leftOut}
				<p class="mt-1 text-small text-ink-muted">
					{t('overview.leavesOutArrived', { amount: money(leftOut, 0), currency })}
				</p>
			{/if}
		</div>
	</div>

	<div class="mt-4 flex flex-wrap items-center justify-end gap-2 px-5">
		<Segmented
			label={t('overview.showAria')}
			value={view}
			options={views}
			onchange={(chosen) => {
				hidden = new Set();
				hovered = null;
				onview(chosen);
			}}
		/>
		{#if view === 'chart' && breakdowns.length > 1}
			<Segmented
				label={t('overview.cutChartByAria')}
				value={by}
				options={breakdowns}
				onchange={onby}
			/>
		{/if}
		<Segmented label={t('overview.rangeAria')} value={range} options={ranges} onchange={onrange} />
	</div>

	{#if view === 'table' && months}
		<MonthEnds {months} {currency} class="mt-3 pb-3" />
	{:else}
		<NetWorthChart
			{series}
			{colors}
			{nameOf}
			{back}
			{hidden}
			label={chartLabel}
			bind:hovered
			class="h-[clamp(220px,38vh,380px)] px-2 pt-2 pb-1"
		/>
		<div class="min-h-10 px-5 pb-3">
			{#if series.parts.length}
				<ul
					class="flex flex-wrap items-center gap-x-1 gap-y-1"
					aria-label={t('overview.partsShownAria')}
				>
					{#each series.parts as part (part.name)}
						{@const shown = !hidden.has(part.name)}
						{@const label = nameOf(part.name)}
						<li>
							<button
								type="button"
								aria-pressed={shown}
								title={shown
									? t('overview.leaveOut', { name: label })
									: t('overview.showPart', { name: label })}
								onclick={() => toggle(part.name)}
								class={cn(
									'flex items-center gap-2 rounded-control px-2 py-1 text-small hover:bg-sunken dark:hover:bg-raised',
									!shown && 'opacity-45'
								)}
							>
								<Swatch
									color={shown ? (colors.get(part.name) ?? 'var(--data-other)') : 'transparent'}
									class={shown ? undefined : 'ring-1 ring-ink-muted ring-inset'}
								/>
								<span class={shown ? 'text-ink-secondary' : 'text-ink-muted line-through'}>
									{label}
								</span>
								<span class="text-ink tabular-nums">{short(part.values[at])}</span>
							</button>
						</li>
					{/each}
					{#if hidden.size}
						<li>
							<button
								type="button"
								onclick={() => (hidden = new Set())}
								class="rounded-control px-2 py-1 text-small text-brand hover:text-brand-hover"
							>
								{t('overview.showAll')}
							</button>
						</li>
					{/if}
				</ul>
			{/if}
		</div>
	{/if}
</section>
