<!--
@component
What the investments returned over the range: the time-weighted return first, as the figure of
the investments themselves, and beside it what the benchmarks returned over the same days;
under it the money-weighted return, what the money put in earned a year, and the gain; then
what the accounts were worth day by day against the money put in and against what it would be
worth in each benchmark, and the same figures account by account.
-->
<script lang="ts">
	import { errorMessage, send } from '#lib/api.js';
	import { day, daysBetween } from '#lib/format/date.js';
	import { difference, short, signed, signedPercent } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { PerformanceReport, PricesFetched } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import { cn } from '#lib/ui/cn.js';
	import { benchmarkColor } from '#lib/design/data.js';
	import {
		compared,
		described,
		instead,
		direction,
		missingBenchmarksNote,
		mwrPer,
		nearest,
		over,
		plot,
		type Range
	} from './performance.js';

	interface Props {
		performance: PerformanceReport;
		range: Range;
		/** Whether both people's accounts are listed: each is then named with its owner. */
		people: boolean;
		/** Session token when prices can be fetched from the server; null in read-only mode. */
		token?: string | null;
		/** Called after prices are fetched so the page reloads its figures. */
		onfetched?: () => Promise<void> | void;
	}

	let { performance, range, people, token = null, onfetched }: Props = $props();

	const currency = $derived(performance.currency);
	const totals = $derived(performance.totals!);
	const span = $derived(over(range, performance.first!, performance.last!));
	const way = $derived(direction(totals.twr, 1));
	const change = $derived(difference(totals.end, totals.start));
	const beside = $derived(compared(totals.twr, performance.benchmarks));
	const missingNote = $derived(missingBenchmarksNote(performance.missing_benchmarks));

	let fetching = $state(false);
	let fetchErrors = $state<string[]>([]);
	$effect.pre(() => {
		void range;
		fetchErrors = [];
	});

	async function fetchPrices() {
		if (!token || fetching) return;
		fetching = true;
		fetchErrors = [];
		try {
			const answer = await send<PricesFetched>('/api/prices', {}, token);
			const problem = errorMessage(answer);
			if (problem !== null) {
				fetchErrors = [problem];
				return;
			}
			fetchErrors = [...new Set(answer.sent.errors ?? [])];
			await onfetched?.();
		} finally {
			fetching = false;
		}
	}

	let width = $state(640);
	const HEIGHT = 240;
	const frame = $derived({ width, height: HEIGHT, top: 12, right: 8, bottom: 24, left: 44 });
	const drawn = $derived(plot(performance.series, frame));
	let pointed = $state<number | null>(null);
	// Another range is other days: nothing of it is under the pointer yet.
	$effect.pre(() => {
		void performance.series;
		pointed = null;
	});
	const shown = $derived(performance.series[pointed ?? performance.series.length - 1]);
	// Over a long range the API sends a point a week.
	const weekly = $derived(
		performance.series.length > 2 &&
			daysBetween(performance.series[1].day, performance.series[2].day) > 1
	);

	function point(event: PointerEvent) {
		if (!drawn) return;
		const box = (event.currentTarget as SVGElement).getBoundingClientRect();
		pointed = nearest(drawn.xs, ((event.clientX - box.left) / box.width) * width);
	}

	const tone = (value: string | null) =>
		({ up: 'text-gain', down: 'text-loss', flat: 'text-ink-secondary' })[direction(value, 1)];
</script>

<section class="rounded-lead border border-line bg-panel px-5 py-5" aria-labelledby="returned">
	<div class="grid gap-6 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
		<div>
			<p id="returned" class="text-small text-ink-secondary">
				{t('overview.twrSpan', { span })}
			</p>
			{#if totals.twr === null}
				<p class="mt-1 text-title text-ink-muted">{t('investments.notInvested')}</p>
			{:else}
				<p
					class={cn(
						'mt-1 text-hero font-medium tracking-tight tabular-nums',
						way === 'up' && 'text-gain',
						way === 'down' && 'text-loss',
						way === 'flat' && 'text-ink'
					)}
				>
					{signedPercent(totals.twr)}
				</p>
			{/if}
			{#if beside}
				<p class="mt-1 text-body text-ink">{beside}</p>
				<p class="text-caption text-ink-muted">
					{t('investments.benchmarkPriceNote', { currency })}
				</p>
			{/if}
			{#if missingNote}
				<p class="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-caption text-ink-muted">
					<span>{missingNote}</span>
					{#if token}
						<button
							type="button"
							class="font-medium text-brand hover:text-brand-hover disabled:opacity-50"
							disabled={fetching}
							onclick={fetchPrices}
						>
							{t(fetching ? 'investments.fetchingPrices' : 'investments.fetchPricesNow')}
						</button>
					{/if}
				</p>
			{/if}
			{#if fetchErrors.length}
				<div class="mt-1 grid gap-0.5 text-caption text-loss" role="alert">
					{#each fetchErrors as problem (problem)}
						<p>{problem}</p>
					{/each}
				</div>
			{/if}
			<p class="mt-2 max-w-prose text-body text-ink-secondary">
				{#if totals.mwr !== null}
					{t('investments.moneyWeighted')}
					<span class={cn('font-medium', tone(totals.mwr))}>{signedPercent(totals.mwr)}</span>
					{t('investments.mwrFlowsNote', { per: mwrPer(totals) })}
				{:else}
					{t('investments.mwrNone')}
				{/if}
				{#if totals.twr_since}
					{t('investments.twrSinceNote', { date: day(totals.twr_since) })}
				{/if}
			</p>
		</div>
		<dl class="grid grid-cols-2 gap-x-6 gap-y-2 text-small sm:grid-cols-4 md:text-right">
			<div>
				<dt class="text-ink-secondary">{t('overview.gain')}</dt>
				<dd class={cn('text-title tabular-nums', tone(totals.gain))}>{signed(totals.gain, 0)}</dd>
			</div>
			<div>
				<dt class="text-ink-secondary">{t('investments.putIn')}</dt>
				<dd class="text-title text-ink">{signed(totals.net_flows, 0)}</dd>
			</div>
			<div>
				<dt class="text-ink-secondary">{t('investments.atTheStart')}</dt>
				<dd class="text-title text-ink"><Amount value={totals.start} places={0} /></dd>
			</div>
			<div>
				<dt class="text-ink-secondary">{t('investments.now')}</dt>
				<dd class="text-title text-ink"><Amount value={totals.end} {currency} places={0} /></dd>
			</div>
		</dl>
	</div>
	<p class="mt-4 max-w-prose text-small text-ink-muted">
		{t('investments.changeBreakdownBefore')}
		<Amount value={change} {currency} places={0} class="text-ink-secondary" />
		{t('investments.changeBreakdownMid1', {
			from: day(performance.series[0].day),
			to: day(performance.last!)
		})}
		<Amount value={totals.net_flows} {currency} places={0} class="text-ink-secondary" />
		{t('investments.changeBreakdownMid2')}
		<Amount value={totals.gain} {currency} places={0} class="text-ink-secondary" />
		{t('investments.changeBreakdownAfter')}
	</p>
</section>

<Panel class="mt-6" title={t('investments.chartTitle')}>
	<div bind:clientWidth={width}>
		{#if drawn}
			<svg
				viewBox="0 0 {width} {HEIGHT}"
				class="block h-[240px] w-full touch-pan-y"
				role="img"
				aria-label={t('investments.chartAria')}
				onpointermove={point}
				onpointerdown={point}
				onpointerleave={() => (pointed = null)}
			>
				{#each [drawn.high, (drawn.high + drawn.low) / 2, drawn.low] as tick (tick)}
					<line
						x1={frame.left}
						x2={width - frame.right}
						y1={drawn.y(tick)}
						y2={drawn.y(tick)}
						class="stroke-chart-grid"
					/>
					<text
						x={frame.left - 6}
						y={drawn.y(tick) + 4}
						text-anchor="end"
						class="fill-ink-muted text-caption"
					>
						{short(String(Math.round(tick)))}
					</text>
				{/each}
				<path d={drawn.area} class="fill-brand-soft" />
				<path
					d={drawn.invested}
					class="fill-none stroke-ink-muted"
					stroke-width="1.5"
					stroke-dasharray="4 3"
				/>
				{#each drawn.benchmarks as line, index (performance.benchmarks[index].symbol)}
					<path
						d={line}
						class="fill-none"
						style:stroke={benchmarkColor(index)}
						stroke-width="1.5"
						stroke-dasharray="6 3"
					/>
				{/each}
				<path d={drawn.value} class="fill-none stroke-brand" stroke-width="2" />
				{#if pointed !== null}
					<line
						x1={drawn.xs[pointed]}
						x2={drawn.xs[pointed]}
						y1={frame.top}
						y2={HEIGHT - frame.bottom}
						class="stroke-ink-muted"
					/>
				{/if}
				<text x={frame.left} y={HEIGHT - 6} class="fill-ink-muted text-caption"
					>{day(performance.series[0].day)}</text
				>
				<text
					x={width - frame.right}
					y={HEIGHT - 6}
					text-anchor="end"
					class="fill-ink-muted text-caption"
				>
					{day(performance.last!)}
				</text>
			</svg>
		{/if}
		{#if shown}
			<p
				class="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-small text-ink-secondary"
				aria-live="polite"
			>
				<span class="font-medium text-ink">{day(shown.day)}</span>
				<span class="flex items-center gap-1.5">
					<span class="h-0.5 w-3 bg-brand" aria-hidden="true"></span>{t('investments.worthLegend')}
					<Amount value={shown.value} {currency} places={0} class="text-ink" />
				</span>
				<span class="flex items-center gap-1.5">
					<span class="h-0 w-3 border-t-[1.5px] border-dashed border-ink-muted" aria-hidden="true"
					></span>{t('investments.putIn')}
					<Amount value={shown.invested} {currency} places={0} class="text-ink" />
				</span>
				{#each performance.benchmarks as benchmark, index (benchmark.symbol)}
					<span class="flex items-center gap-1.5">
						<span
							class="h-0 w-3 border-t-[1.5px] border-dashed"
							style:border-color={benchmarkColor(index)}
							aria-hidden="true"
						></span>{t('investments.inBenchmarkLegend', { symbol: benchmark.symbol })}
						<Amount value={shown.benchmarks[index]} {currency} places={0} class="text-ink" />
					</span>
				{/each}
			</p>
		{/if}
		<p class="mt-1 text-caption text-ink-muted">
			{t('investments.chartNote', {
				instead: instead(performance.benchmarks),
				weekly: weekly ? t('investments.weeklyNote') : ''
			})}
		</p>
	</div>
</Panel>

<section class="mt-10" aria-labelledby="accounts-title">
	<header class="mb-4 border-b border-line pb-3">
		<h2 id="accounts-title" class="text-heading font-medium text-ink">
			{performance.accounts.length === 1
				? t('accounts.countOne')
				: t('accounts.countMany', { count: performance.accounts.length })}
		</h2>
		<p class="text-small text-ink-secondary">
			{t('investments.brokersSubtitle')}
		</p>
	</header>
	<div
		class="hidden grid-cols-[minmax(0,1fr)_8rem_7rem_7rem_6rem_6rem] gap-x-4 px-4 pb-2 text-caption text-ink-muted sm:grid"
		aria-hidden="true"
	>
		<span>{t('investments.colAccount')}</span>
		<span class="text-right">{t('investments.now')}</span>
		<span class="text-right">{t('investments.putIn')}</span>
		<span class="text-right">{t('overview.gain')}</span>
		<span class="text-right">{t('investments.colTwr')}</span>
		<span class="text-right">{t('investments.colMwr')}</span>
	</div>
	<ul class="rounded-panel bg-panel">
		{#each performance.accounts as row (`${row.owner}|${row.name}`)}
			{@const about = described(row, people, totals.twr_since ?? performance.first)}
			<li
				class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 border-t border-line px-4 py-3 first:border-t-0 sm:grid-cols-[minmax(0,1fr)_8rem_7rem_7rem_6rem_6rem]"
			>
				<span class="min-w-0">
					<span class="block truncate font-medium text-ink">{row.name}</span>
					<span class="block truncate text-small text-ink-muted">
						<span class="hidden sm:inline">{about}</span>
						<span class="sm:hidden"
							>{about ? `${about} · ` : ''}{t('investments.mobileGain', {
								amount: signed(row.gain, 0)
							})}</span
						>
					</span>
				</span>
				<span class="text-right sm:hidden">
					<span class={cn('block font-medium tabular-nums', tone(row.twr))}>
						{row.twr === null ? '–' : signedPercent(row.twr)}
					</span>
					<Amount value={row.end} {currency} places={0} class="text-small text-ink-secondary" />
				</span>
				<span class="hidden text-right sm:block"><Amount value={row.end} places={0} /></span>
				<span class="hidden text-right text-ink-secondary sm:block">{signed(row.net_flows, 0)}</span
				>
				<span class={cn('hidden text-right tabular-nums sm:block', tone(row.gain))}
					>{signed(row.gain, 0)}</span
				>
				<span class={cn('hidden text-right tabular-nums sm:block', tone(row.twr))}>
					{row.twr === null ? '–' : signedPercent(row.twr)}
				</span>
				<span class="hidden text-right text-ink-secondary tabular-nums sm:block">
					{row.mwr === null ? '–' : signedPercent(row.mwr)}
				</span>
			</li>
		{/each}
	</ul>
</section>
