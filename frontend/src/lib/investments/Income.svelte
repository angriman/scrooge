<!--
@component
What investments paid over the range: dividends and interest in all, the tax withheld from them
and what was left, against the same months a year before and, for another range, beside what
the last twelve months paid; each month as a column, dividends below interest, before tax; and
each holding, what paid the most first, opening its transactions.
-->
<script lang="ts">
	import { resolve } from '$app/paths';
	import { day } from '#lib/format/date.js';
	import { approximate, short, signedPercent } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { PayoutsReport } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import { cn } from '#lib/ui/cn.js';
	import {
		change,
		DIVIDENDS_COLOR,
		highest,
		INTEREST_COLOR,
		isTrailing,
		monthLabel,
		paid,
		stack,
		transactionsOf,
		withheld
	} from './income.js';

	interface Props {
		income: PayoutsReport;
		logos: Record<string, string>;
		/** What the range is called, for the sentence: `over the last 12 months`, `in 2025`. */
		range: string;
		/** Whose figures, for the transactions a holding opens. */
		owner: string | null;
	}

	let { income, logos, range, owner }: Props = $props();

	const currency = $derived(income.currency);
	const total = $derived(paid(income.totals));
	const moved = $derived(change(income.totals, income.year_before));
	const trailing = $derived(isTrailing(income) ? null : income.trailing);

	let width = $state(640);
	const HEIGHT = 200;
	const TOP = 12;
	const BOTTOM = 22; // room for the names of the months
	const LEFT = 44; // room for the amounts
	const plot = HEIGHT - TOP - BOTTOM;
	const top = $derived(highest(income.months));
	const step = $derived((width - LEFT) / Math.max(1, income.months.length));
	const bar = $derived(Math.max(3, Math.min(32, step * 0.6)));
	const columns = $derived(
		income.months.map((month, index) => {
			const { dividends, interest } = stack(month, plot / top);
			const x = LEFT + step * index + (step - bar) / 2;
			const base = TOP + plot;
			return { month, x, dividends, interest, base };
		})
	);
	// Names every few columns when they would crowd, counted back from the last.
	const every = $derived(Math.ceil(36 / Math.max(1, step)));
	let shown = $state<number | null>(null);
	const detail = $derived(income.months[shown ?? income.months.length - 1]);

	const withLogos = $derived(income.holdings.some((h) => h.symbol && logos[h.symbol]));
	const href = (holding: PayoutsReport['holdings'][number]) =>
		`${resolve('/transactions')}?${transactionsOf(holding, income, owner)}`;
</script>

<section class="rounded-lead border border-line bg-panel px-5 py-5" aria-labelledby="paid">
	<div class="grid gap-6 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
		<div>
			<h1 id="paid" class="text-small text-ink-secondary">
				{t('investments.paidLead', { range })}
			</h1>
			<p class="mt-1 text-hero font-medium tracking-tight text-ink">
				<Amount value={total} {currency} places={0} />
			</p>
			<p class="mt-2 max-w-prose text-body text-ink-secondary">
				{#if moved !== null}
					<span class={cn('font-medium', moved.startsWith('-') ? 'text-loss' : 'text-gain')}>
						{signedPercent(moved)}
					</span>
					{t('investments.onYearBeforePct')}
				{:else}
					{t('investments.beforeTaxNote')}
				{/if}
			</p>
			{#if trailing}
				<p class="mt-1 max-w-prose text-small text-ink-secondary">
					{t('investments.last12MonthsPaidBefore')}
					<Amount value={paid(trailing)} {currency} places={0} class="text-ink" />,
					<Amount value={trailing.net} {currency} places={0} class="text-ink" />
					{t('investments.last12MonthsAfterTax')}
				</p>
			{/if}
		</div>
		<dl class="grid grid-cols-2 gap-x-6 gap-y-2 text-small sm:grid-cols-4 md:text-right">
			<div>
				<dt class="text-ink-secondary">{t('investments.dividends')}</dt>
				<dd class="text-title text-ink"><Amount value={income.totals.dividends} places={0} /></dd>
			</div>
			<div>
				<dt class="text-ink-secondary">{t('investments.interest')}</dt>
				<dd class="text-title text-ink"><Amount value={income.totals.interest} places={0} /></dd>
			</div>
			<div>
				<dt class="text-ink-secondary">{t('investments.taxWithheld')}</dt>
				<dd class="text-title text-ink">
					<Amount value={withheld(income.totals.tax)} places={0} />
				</dd>
			</div>
			<div>
				<dt class="text-ink-secondary">{t('investments.net')}</dt>
				<dd class="text-title text-ink"><Amount value={income.totals.net} places={0} /></dd>
			</div>
		</dl>
	</div>
</section>

<Panel class="mt-6" title={t('investments.monthByMonth')}>
	<div bind:clientWidth={width}>
		<svg
			viewBox="0 0 {width} {HEIGHT}"
			class="block h-[200px] w-full"
			role="img"
			aria-label={t('investments.incomeChartAria')}
		>
			<text x={LEFT - 6} y={TOP + 4} text-anchor="end" class="fill-ink-muted text-caption"
				>{short(String(top))}</text
			>
			<text x={LEFT - 6} y={TOP + plot} text-anchor="end" class="fill-ink-muted text-caption"
				>0</text
			>
			{#each columns as column, index (column.month.month)}
				<g
					role="presentation"
					onpointerenter={() => (shown = index)}
					onpointerleave={() => (shown = null)}
					opacity={shown === null || shown === index ? 1 : 0.45}
				>
					<rect x={LEFT + step * index} y={TOP} width={step} height={plot} fill="transparent" />
					{#if column.dividends > 0}
						<rect
							x={column.x}
							y={column.base - column.dividends}
							width={bar}
							height={column.dividends}
							fill={DIVIDENDS_COLOR}
						/>
					{/if}
					{#if column.interest > 0}
						<rect
							x={column.x}
							y={column.base - column.dividends - column.interest}
							width={bar}
							height={column.interest}
							fill={INTEREST_COLOR}
						/>
					{/if}
					{#if (columns.length - 1 - index) % every === 0}
						<text
							x={column.x + bar / 2}
							y={HEIGHT - 6}
							text-anchor="middle"
							class="fill-ink-muted text-caption"
						>
							{monthLabel(column.month.month)}
						</text>
					{/if}
				</g>
			{/each}
			<line x1={LEFT} x2={width} y1={TOP + plot} y2={TOP + plot} class="stroke-line-strong" />
		</svg>

		{#if detail}
			<p
				class="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-small text-ink-secondary"
				aria-live="polite"
			>
				<span class="font-medium text-ink"
					>{monthLabel(detail.month)} {detail.month.slice(0, 4)}</span
				>
				<span
					>{t('investments.dividends')}
					<Amount value={detail.dividends} {currency} places={0} class="text-ink" /></span
				>
				<span
					>{t('investments.interest')}
					<Amount value={detail.interest} {currency} places={0} class="text-ink" /></span
				>
				<span
					>{t('investments.taxWithheld')}
					<Amount value={withheld(detail.tax)} {currency} places={0} class="text-ink" /></span
				>
			</p>
		{/if}

		<ul class="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-caption text-ink-secondary">
			<li class="flex items-center gap-1.5">
				<span class="size-2 rounded-[2px]" style:background={DIVIDENDS_COLOR}></span>{t(
					'investments.dividends'
				)}
			</li>
			<li class="flex items-center gap-1.5">
				<span class="size-2 rounded-[2px]" style:background={INTEREST_COLOR}></span>{t(
					'investments.interest'
				)}
			</li>
			<li class="text-ink-muted">{t('investments.incomeLegendNote')}</li>
		</ul>
	</div>
</Panel>

<section class="mt-10" aria-labelledby="paying-title">
	<header class="mb-4 border-b border-line pb-3">
		<h2 id="paying-title" class="text-heading font-medium text-ink">
			{income.holdings.length === 1
				? t('investments.oneHoldingPaid')
				: t('investments.manyHoldingsPaid', { count: income.holdings.length })}
		</h2>
		<p class="text-small text-ink-secondary">
			{t('investments.holdingsPaidSubtitle')}
		</p>
	</header>

	{#if !income.holdings.length}
		<p class="text-body text-ink-secondary">{t('investments.incomeEmptyMonths')}</p>
	{:else}
		<div
			class="hidden grid-cols-[minmax(0,1fr)_7rem_7rem_7rem_7rem] gap-x-4 px-4 pb-2 text-caption text-ink-muted sm:grid"
			aria-hidden="true"
		>
			<span>{t('investments.colHolding')}</span>
			<span class="text-right">{t('investments.dividends')}</span>
			<span class="text-right">{t('investments.interest')}</span>
			<span class="text-right">{t('investments.taxWithheld')}</span>
			<span class="text-right">{t('investments.colLastPaid')}</span>
		</div>
		<ul class="rounded-panel bg-panel">
			{#each income.holdings as holding (`${holding.key}/${holding.account ?? ''}/${holding.owner ?? ''}`)}
				{@const logo = holding.symbol ? logos[holding.symbol] : undefined}
				<li class="border-t border-line first:border-t-0">
					<a
						href={href(holding)}
						class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 px-4 py-3 hover:bg-raised sm:grid-cols-[minmax(0,1fr)_7rem_7rem_7rem_7rem]"
					>
						<span class="flex min-w-0 items-center gap-3">
							{#if withLogos}
								{#if logo}
									<img
										src={logo}
										alt=""
										class="size-7 shrink-0 rounded-control bg-ink object-contain p-0.5"
									/>
								{:else}
									<span class="size-7 shrink-0" aria-hidden="true"></span>
								{/if}
							{/if}
							<span class="min-w-0">
								<span class="block truncate font-medium text-ink">{holding.name}</span>
								<span class="flex gap-1.5 text-small text-ink-muted">
									{#if holding.account}
										<span class="truncate">{t('investments.unnamedPayments')}</span>
									{:else if holding.name !== holding.key}
										<span class="truncate">{holding.key}</span>
									{/if}
									<span class="shrink-0 sm:hidden"
										>{t('investments.lastDateShort', { date: day(holding.last) })}</span
									>
								</span>
							</span>
						</span>
						<span class="text-right sm:hidden">
							<Amount value={paid(holding)} {currency} />
						</span>
						{#each [holding.dividends, holding.interest, withheld(holding.tax)] as value, column (column)}
							<span class={cn('hidden text-right sm:block', column === 2 && 'text-ink-secondary')}>
								{#if approximate(value) === 0}<span class="text-ink-muted">–</span>{:else}<Amount
										{value}
									/>{/if}
							</span>
						{/each}
						<span class="hidden text-right text-small text-ink-secondary sm:block"
							>{day(holding.last)}</span
						>
					</a>
				</li>
			{/each}
		</ul>
	{/if}
</section>
