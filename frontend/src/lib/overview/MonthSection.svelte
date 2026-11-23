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

	const step = (month: string | null, label: string) =>
		month ? { href: withQuery(here(), { month }), label: `${label}: ${monthName(month)}` } : null;
	const earlier = $derived(step(recap.earlier, 'Earlier month'));
	const later = $derived(step(recap.later, 'Later month'));

	const missingNames = $derived(recap.missing.map((account) => account.account));
	/** `2.5×` the usual month, for what was more than double it: a percent that large says less. */
	const times = (category: MonthCategory) => {
		const percent = category.usual === null ? null : percentOf(category.spent, category.usual, 0);
		return percent === null ? '' : `${(Number(percent) / 100).toFixed(1)}×`;
	};
</script>

<section class="mt-10" aria-labelledby="month-title">
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
						class="grid size-8 place-items-center rounded-control text-ink-secondary hover:bg-raised hover:text-ink"
					>
						{#if index === 0}<ChevronLeft class="size-4" />{:else}<ChevronRight class="size-4" />{/if}
					</a>
				{:else}
					<span class="grid size-8 place-items-center text-ink-muted/40" aria-hidden="true">
						{#if index === 0}<ChevronLeft class="size-4" />{:else}<ChevronRight class="size-4" />{/if}
					</span>
				{/if}
			{/each}
		</div>
		<p class="flex items-center gap-1.5 text-small text-ink-secondary">
			{#if !recap.over}
				<CircleDashed class="size-4 text-ink-muted" aria-hidden="true" />
				So far, up to {dayOfYear(recap.last)}
			{:else if recap.missing.length}
				<CircleAlert class="size-4 text-caution" aria-hidden="true" />
				{missingNames.length === 1
					? `The ${missingNames[0]} statement is not in yet`
					: `${missingNames.length} statements are not in yet`}
			{:else}
				<CircleCheck class="size-4 text-gain" aria-hidden="true" />
				All statements are in
			{/if}
		</p>
	</header>

	<div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
		<Panel
			title="How net worth moved"
			note="What was saved, and what markets and everything else added"
		>
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
					The history starts within this month, so how net worth moved over it is not known.
				</p>
			{/if}
			<dl class="mt-3 flex flex-wrap gap-x-8 gap-y-2 border-t border-line pt-3">
				<div>
					<dt class="text-caption text-ink-muted">Saved</dt>
					<dd class="text-title"><Amount value={recap.saved} currency={recap.currency} places={0} /></dd>
				</div>
				<div>
					<dt class="text-caption text-ink-muted">Savings rate</dt>
					<dd class="text-title">{recap.rate === null ? '–' : percent(recap.rate, 0)}</dd>
				</div>
				<div>
					<dt class="text-caption text-ink-muted">12-month rate</dt>
					<dd class="text-title">
						{recap.rate_year === null ? '–' : percent(recap.rate_year, 0)}
					</dd>
				</div>
			</dl>
		</Panel>

		<Panel title="Spending" note="Against a usual month">
			{#snippet actions()}
				<span class="text-title"><Amount value={recap.spent} currency={recap.currency} places={0} /></span>
			{/snippet}
			{#if listed.length}
				<ul class="grid grid-cols-[minmax(0,auto)_1fr_auto_4.5rem] items-center gap-x-3 gap-y-2.5">
					{#each listed as category (category.name)}
						{@const against = category.against_usual === null ? null : approximate(category.against_usual)}
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
								{#if category.against_usual === null}new{:else if against !== null && Math.abs(against) < AS_USUAL}as
									usual{:else if against !== null && against >= 100}{times(category)}{:else}{signedPercent(
										category.against_usual,
										0
									)}{/if}
							</span>
						</li>
					{/each}
					{#if rest !== null}
						<li class="contents text-ink-secondary">
							<span class="text-body">Other categories</span>
							<span></span>
							<span class="text-right"><Amount value={rest} places={0} /></span>
							<span></span>
						</li>
					{/if}
				</ul>
			{:else}
				<p class="py-6 text-body text-ink-secondary">Nothing was spent this month.</p>
			{/if}
		</Panel>
	</div>
</section>
