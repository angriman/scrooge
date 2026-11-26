<!--
@component
One month of the first page: how net worth moved over it and why, and what was spent in it
against the usual month. Its heading names the month and steps to the ones either side, and
says whether every account's statements cover it, so that a month still missing one is not
read as complete.
-->
<script lang="ts">
	import ChevronLeft from '@lucide/svelte/icons/chevron-left';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleDashed from '@lucide/svelte/icons/circle-dashed';
	import MonthBridge from '#lib/charts/MonthBridge.svelte';
	import { addDays, dayOfYear, monthName } from '#lib/format/date.js';
	import {
		approximate,
		difference,
		percent,
		percentOf,
		signedPercent,
		sum
	} from '#lib/format/number.js';
	import { t, type MessageKey } from '#lib/i18n/index.svelte.js';
	import type { MonthCategory, MonthRecap } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import { cn } from '#lib/ui/cn.js';
	import { here, withQuery } from '#lib/view.js';

	let { recap }: { recap: MonthRecap } = $props();

	const SHOWN = 5; // categories listed on their own; the rest are one line
	// A category within this many percent of its usual month spent as usual.
	const AS_USUAL = 5;

	const eve = $derived(addDays(recap.first, -1));
	const listed = $derived(recap.categories.slice(0, SHOWN));
	const rest = $derived(
		recap.categories.length > SHOWN
			? difference(recap.spent, sum(listed.map((category) => category.spent)))
			: null
	);
	const widest = $derived(Math.max(1, ...listed.map((category) => approximate(category.spent))));

	const step = (month: string | null, key: MessageKey) =>
		month
			? { href: withQuery(here(), { month }), label: t(key, { month: monthName(month) }) }
			: null;
	const earlier = $derived(step(recap.earlier, 'monthSection.earlierMonth'));
	const later = $derived(step(recap.later, 'monthSection.laterMonth'));

	const missingNames = $derived(recap.missing.map((account) => account.account));
	/** `2.5×` the usual month, for what was more than double it: a percent that large says less. */
	const times = (category: MonthCategory) => {
		const percent = category.usual === null ? null : percentOf(category.spent, category.usual, 0);
		return percent === null ? '' : `${(Number(percent) / 100).toFixed(1)}×`;
	};
</script>

<section aria-labelledby="month-title">
	<header
		class="mb-4 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-line pb-3"
	>
		<div class="flex items-center gap-1">
			{#each [earlier, null, later] as link, index (index)}
				{#if index === 1}
					<h2 id="month-title" class="px-1 text-heading font-medium text-ink">
						{monthName(recap.month)}
					</h2>
				{:else if link}
					<a
						href={link.href}
						aria-label={link.label}
						data-sveltekit-reset="false"
						class="grid size-11 place-items-center rounded-control text-ink-secondary hover:bg-sunken hover:text-ink sm:size-8 dark:hover:bg-raised"
					>
						{#if index === 0}<ChevronLeft class="size-4" />{:else}<ChevronRight
								class="size-4"
							/>{/if}
					</a>
				{:else}
					<span
						class="grid size-11 place-items-center text-ink-muted/40 sm:size-8"
						aria-hidden="true"
					>
						{#if index === 0}<ChevronLeft class="size-4" />{:else}<ChevronRight
								class="size-4"
							/>{/if}
					</span>
				{/if}
			{/each}
		</div>
		<p class="flex items-center gap-1.5 text-small text-ink-secondary">
			{#if !recap.over}
				<CircleDashed class="size-4 text-ink-muted" aria-hidden="true" />
				{t('monthSection.soFar', { date: dayOfYear(recap.last) })}
			{:else if recap.missing.length}
				<CircleAlert class="size-4 text-caution" aria-hidden="true" />
				{missingNames.length === 1
					? t('monthSection.oneMissing', { account: missingNames[0] })
					: t('monthSection.manyMissing', { count: missingNames.length })}
			{:else}
				<CircleCheck class="size-4 text-gain" aria-hidden="true" />
				{t('monthSection.allIn')}
			{/if}
		</p>
	</header>

	<div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
		<Panel title={t('monthSection.movedTitle')} note={t('monthSection.movedNote')}>
			{#if recap.before !== null && recap.other !== null}
				<MonthBridge
					before={recap.before}
					income={recap.income}
					spent={recap.spent}
					arrived={recap.arrived}
					other={recap.other}
					after={recap.after}
					from={dayOfYear(eve)}
					to={dayOfYear(recap.last)}
				/>
			{:else}
				<p class="py-6 text-body text-ink-secondary">
					{t('monthSection.historyStarts')}
				</p>
			{/if}
			<dl class="mt-3 flex flex-wrap gap-x-8 gap-y-2 border-t border-line pt-3">
				<div>
					<dt class="text-caption text-ink-muted">{t('monthSection.saved')}</dt>
					<dd class="text-title">
						<Amount value={recap.saved} currency={recap.currency} places={0} />
					</dd>
				</div>
				<div>
					<dt class="text-caption text-ink-muted">{t('monthSection.savingsRate')}</dt>
					<dd class="text-title">{recap.rate === null ? '–' : percent(recap.rate, 0)}</dd>
					{#if recap.rate_salary !== null}
						<dd class="text-caption text-ink-secondary">
							{t('monthSection.ofSalary', { rate: percent(recap.rate_salary, 0) })}
						</dd>
					{/if}
				</div>
				<div>
					<dt class="text-caption text-ink-muted">{t('monthSection.twelveMonthRate')}</dt>
					<dd class="text-title">
						{recap.rate_year === null ? '–' : percent(recap.rate_year, 0)}
					</dd>
					{#if recap.rate_salary_year !== null}
						<dd class="text-caption text-ink-secondary">
							{t('monthSection.ofSalaryYear', { rate: percent(recap.rate_salary_year, 0) })}
						</dd>
					{/if}
				</div>
			</dl>
		</Panel>

		<Panel title={t('monthSection.spendingTitle')} note={t('monthSection.spendingNote')}>
			{#snippet actions()}
				<span class="text-title"
					><Amount value={recap.spent} currency={recap.currency} places={0} /></span
				>
			{/snippet}
			{#if listed.length}
				<ul class="grid grid-cols-[minmax(0,auto)_1fr_auto_4.5rem] items-center gap-x-3 gap-y-2.5">
					{#each listed as category (category.name)}
						{@const against =
							category.against_usual === null ? null : approximate(category.against_usual)}
						<li class="contents">
							<span class="truncate text-body text-ink">{category.name}</span>
							<span class="block h-1.5 rounded-mark bg-sunken" aria-hidden="true">
								<span
									class="block h-full rounded-mark bg-ink-muted"
									style:width="{(Math.max(0, approximate(category.spent)) / widest) * 100}%"
								></span>
							</span>
							<span class="text-right"><Amount value={category.spent} places={0} /></span>
							<span
								class={cn(
									'text-right text-small',
									against === null || Math.abs(against) < AS_USUAL
										? 'text-ink-muted'
										: against > 0
											? 'text-loss'
											: 'text-gain'
								)}
							>
								{#if category.against_usual === null}{t(
										'monthSection.new'
									)}{:else if against !== null && Math.abs(against) < AS_USUAL}{t(
										'monthSection.asUsual'
									)}{:else if against !== null && against >= 100}{times(
										category
									)}{:else}{signedPercent(category.against_usual, 0)}{/if}
							</span>
						</li>
					{/each}
					{#if rest !== null}
						<li class="contents text-ink-secondary">
							<span class="text-body">{t('monthSection.otherCategories')}</span>
							<span></span>
							<span class="text-right"><Amount value={rest} places={0} /></span>
							<span></span>
						</li>
					{/if}
				</ul>
			{:else}
				<p class="py-6 text-body text-ink-secondary">{t('monthSection.nothingSpent')}</p>
			{/if}
		</Panel>
	</div>
</section>
