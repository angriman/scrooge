<!--
@component
The pensions as a group of their own: one line per pension account with what it is worth, how
it moved over the period chosen, what was paid into it and what it returned where that can be
known, and what the group adds up to. A 3a portfolio is one figure here, its cash included.
-->
<script lang="ts">
	import { day } from '#lib/format/date.js';
	import { signed, signedPercent } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { Amount as Figure, PensionsReport } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import { cn } from '#lib/ui/cn.js';
	import { direction } from './performance.js';
	import { changeOf, described, periodOptions, totals, type Period } from './pensions.js';

	interface Props {
		report: PensionsReport;
		period: Period;
		/** Whether both people's pensions are listed: each is then named with its owner. */
		people: boolean;
		onperiod: (period: Period) => void;
	}

	let { report, period, people, onperiod }: Props = $props();

	const currency = $derived(report.currency);
	const all = $derived(totals(report.pensions, period));
	const label = $derived(t(period === '1y' ? 'pensions.over12m' : 'pensions.thisYear'));
	const periods = $derived(periodOptions());
	const withFund = $derived(report.pensions.some((pension) => pension.pillar === 'Pillar 2'));

	const tone = (value: Figure | null) =>
		({ up: 'text-gain', down: 'text-loss', flat: 'text-ink-secondary' })[direction(value, 0)];
	const COLUMNS = 'sm:grid-cols-[minmax(0,1fr)_8rem_7rem_7rem_6rem]';
</script>

<Panel
	class="mt-10"
	title={t('pensions.title')}
	note={t('pensions.note', { date: day(report.day!) })}
>
	{#snippet actions()}
		<Segmented
			label={t('pensions.changeOverAria')}
			value={period}
			options={periods}
			onchange={onperiod}
		/>
	{/snippet}
	<div
		class={cn('hidden gap-x-4 px-1 pb-2 text-caption text-ink-muted sm:grid', COLUMNS)}
		aria-hidden="true"
	>
		<span>{t('investments.colAccount')}</span>
		<span class="text-right">{t('investments.now')}</span>
		<span class="text-right">{t('monthEnds.colChange')}</span>
		<span class="text-right">{t('pensions.colPaidIn')}</span>
		<span class="text-right">{t('pensions.colReturn')}</span>
	</div>
	<ul>
		{#each report.pensions as pension (`${pension.owner}|${pension.account}`)}
			{@const change = changeOf(pension, period)}
			{@const measured = pension.changes[period]}
			<li
				class={cn(
					'grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 border-t border-line px-1 py-3 first:border-t-0',
					COLUMNS
				)}
			>
				<span class="min-w-0">
					<span class="block truncate font-medium text-ink">{pension.account}</span>
					<span class="block truncate text-small text-ink-muted">{described(pension, people)}</span>
					<span class="block text-small text-ink-muted sm:hidden">
						{change === null
							? t('pensions.changeNotKnown')
							: `${signed(change, 0)} ${label}`}{measured.paid_in !== null
							? ` · ${t('pensions.paidInInline', { amount: signed(measured.paid_in, 0) })}`
							: ''}{measured.twr !== null
							? ` · ${t('pensions.returnInline', { pct: signedPercent(measured.twr) })}`
							: ''}
					</span>
				</span>
				<span class="text-right"
					><Amount value={pension.value} {currency} places={0} class="sm:hidden" /><Amount
						value={pension.value}
						places={0}
						class="hidden sm:inline"
					/></span
				>
				<span class={cn('hidden text-right tabular-nums sm:block', tone(change))}>
					{change === null ? '–' : signed(change, 0)}
				</span>
				<span class="hidden text-right text-ink-secondary tabular-nums sm:block">
					{measured.paid_in === null ? '–' : signed(measured.paid_in, 0)}
				</span>
				<span class={cn('hidden text-right tabular-nums sm:block', tone(measured.twr))}>
					{measured.twr === null ? '–' : signedPercent(measured.twr)}
				</span>
			</li>
		{/each}
		{#if report.pensions.length > 1}
			<li
				class={cn(
					'grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 border-t border-line-strong px-1 py-3',
					COLUMNS
				)}
			>
				<span class="min-w-0">
					<span class="block font-medium text-ink">{t('pensions.allPensions')}</span>
					<span class="block text-small text-ink-muted sm:hidden">
						{all.change === null
							? t('pensions.changeNotKnown')
							: `${signed(all.change, 0)} ${label}`}{all.paidIn !== null
							? ` · ${t('pensions.paidInInline', { amount: signed(all.paidIn, 0) })}`
							: ''}
					</span>
				</span>
				<span class="text-right font-medium">
					<Amount value={all.value} {currency} places={0} />
				</span>
				<span class={cn('hidden text-right tabular-nums sm:block', tone(all.change))}>
					{all.change === null ? '–' : signed(all.change, 0)}
				</span>
				<span class="hidden text-right text-ink-secondary tabular-nums sm:block">
					{all.paidIn === null ? '–' : signed(all.paidIn, 0)}{all.paidInPartly ? ' *' : ''}
				</span>
				<span class="hidden sm:block"></span>
			</li>
		{/if}
	</ul>
	<div class="mt-3 grid gap-1 text-caption text-ink-muted">
		<p>
			{t('pensions.footnote3a')}
		</p>
		{#if withFund}
			<p>
				{t('pensions.footnoteFund', {
					partlyNote: all.paidInPartly ? t('pensions.partlyNote') : ''
				})}
			</p>
		{/if}
	</div>
</Panel>
