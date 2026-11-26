<!--
@component
What was left of what came in over the range: the amount saved, as a sentence with the savings
rate, how it compares with the same months a year before, and the rate on salary; beside it what
came in, what went out, the salary and how much of the income was earned by working.
-->
<script lang="ts">
	import { approximate, difference, negate, percent, signed } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { Saved } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import { cn } from '#lib/ui/cn.js';

	interface Props {
		totals: Saved;
		yearBefore: Saved | null;
		currency: string;
		/** What the range is called, for the sentence: `this year so far`, `2025`. */
		range: string;
		paidBack: string;
	}

	let { totals, yearBefore, currency, range, paidBack }: Props = $props();

	const short = $derived(totals.saved.startsWith('-'));
	// The change of the savings rate, in points: rates of different incomes are compared, not
	// the amounts. The sentence writes out the space before the bracket, as Svelte trims a plain
	// one at the start of a block; without a change there is neither the space nor the bracket.
	const points = $derived(
		totals.rate !== null && yearBefore?.rate != null
			? difference(totals.rate, yearBefore.rate)
			: null
	);
</script>

<section class="rounded-lead border border-line bg-panel px-5 py-5" aria-labelledby="saved">
	<div class="grid gap-6 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
		<div>
			<p id="saved" class="text-small text-ink-secondary">
				{t(short ? 'cashflow.deficitTitle' : 'cashflow.savedTitle', { range })}
			</p>
			<p class="mt-1 text-hero font-medium tracking-tight text-ink">
				<Amount value={short ? negate(totals.saved) : totals.saved} {currency} places={0} />
			</p>
			<p class="mt-2 max-w-prose text-body text-ink-secondary">
				{#if totals.rate !== null && !short}
					<span class="text-title font-medium text-brand">{percent(totals.rate)}</span>
					{t('cashflow.keptOfIncomeAfter')}{#if points !== null}{' '}(<span
							class={cn(points.startsWith('-') ? 'text-loss' : 'text-gain')}
							>{t('cashflow.pointsCompare', { points: signed(points, 1) })}</span
						>
						{t('cashflow.onYearBefore')}){/if}{#if totals.rate_salary !== null},
						<span class="text-title font-medium text-brand">{percent(totals.rate_salary)}</span>
						{t('cashflow.ofSalaryAfter')}{/if}.
				{:else if short}
					{t('cashflow.shortfallNote')}
				{:else if approximate(totals.income) > 0}
					{t('cashflow.littleCameIn')}
				{:else}
					{t('cashflow.nothingCameIn')}
				{/if}
			</p>
		</div>
		<dl class="grid grid-cols-2 gap-x-6 gap-y-1 text-small sm:grid-cols-4 md:text-right">
			<div>
				<dt class="text-ink-secondary">{t('cashflow.cameIn')}</dt>
				<dd class="text-title text-gain"><Amount value={totals.income} places={0} /></dd>
			</div>
			<div>
				<dt class="text-ink-secondary">{t('cashflow.wentOut')}</dt>
				<dd class="text-title text-ink"><Amount value={totals.spent} places={0} /></dd>
			</div>
			<div>
				<dt class="text-ink-secondary" title={t('cashflow.salaryTitle')}>{t('cashflow.salary')}</dt>
				<dd class="text-title text-ink"><Amount value={totals.work} places={0} /></dd>
			</div>
			<div>
				<dt class="text-ink-secondary">{t('cashflow.fromWork')}</dt>
				<dd class="text-title text-ink">
					{totals.from_work === null ? '–' : percent(totals.from_work, 0)}
				</dd>
			</div>
			{#if approximate(paidBack) > 0}
				<p class="col-span-full text-caption text-ink-muted">
					{t('cashflow.afterPaidBackBefore')}
					<Amount value={paidBack} {currency} places={0} />
					{t('cashflow.afterPaidBackAfter')}
				</p>
			{/if}
		</dl>
	</div>
</section>
