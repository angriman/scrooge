<!--
@component
Where the matching transactions went, and where they came from: a bar for each top-level
category in one currency, as long as its share of the largest. Amounts in several currencies are
never added: each currency has its own tab, opening on the header's currency when present.
Choosing a category opens its details in the side panel.
-->
<script lang="ts">
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import { approximate, money, negate, sum } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { CategoryTotal } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import { cn } from '#lib/ui/cn.js';
	import { TRANSFERS } from './filter.js';
	import { orderCurrencies } from './summary.js';

	interface Props {
		totals: CategoryTotal[];
		/** How many filtered transactions `totals` covers across all currencies. */
		count: number;
		/** The currency currently selected in the page header. */
		headerCurrency: string;
		/** The category currently open in the side panel, if any. */
		selected: string | null;
		onselect: (category: CategoryTotal, trigger: HTMLElement) => void;
	}

	let { totals, count, headerCurrency, selected, onselect }: Props = $props();

	const currencies = $derived(orderCurrencies(totals, headerCurrency));
	let picked = $state<string | null>(null);
	let lastHeaderCurrency = $state<string | null>(null);
	$effect.pre(() => {
		if (headerCurrency !== lastHeaderCurrency) {
			lastHeaderCurrency = headerCurrency;
			picked = null;
		}
	});
	const currency = $derived(picked && currencies.includes(picked) ? picked : (currencies[0] ?? ''));

	const inCurrency = $derived(totals.filter((total) => total.currency === currency));
	const mine = $derived(inCurrency.filter((total) => total.key !== TRANSFERS));
	const transferCount = $derived(
		inCurrency
			.filter((total) => total.key === TRANSFERS)
			.reduce((totalCount, line) => totalCount + line.count, 0)
	);

	const sides = $derived(
		[
			{
				id: 'out' as const,
				name: t('transactions.moneyOut'),
				lines: mine
					.filter((total) => total.total.startsWith('-'))
					.map((total) => ({ ...total, size: negate(total.total) }))
			},
			{
				id: 'in' as const,
				name: t('transactions.moneyIn'),
				lines: mine
					.filter((total) => !total.total.startsWith('-') && approximate(total.total) > 0)
					.map((total) => ({ ...total, size: total.total }))
			}
		]
			.filter((side) => side.lines.length)
			.map((side) => ({
				...side,
				total: sum(side.lines.map((line) => line.size))
			}))
	);

	const largest = $derived(
		Math.max(1, ...sides.flatMap((side) => side.lines.map((line) => approximate(line.size))))
	);

	const note = $derived.by(() => {
		if (count === 1) return t('transactions.byCategoryNoteOne');
		const formatted = money(String(count), 0);
		if (currencies.length === 1 && currency) {
			return t('transactions.byCategoryNoteSingleCurrency', { count: formatted, currency });
		}
		return t('transactions.byCategoryNoteMany', { count: formatted });
	});
</script>

<Panel title={t('transactions.byCategoryTitle')} {note}>
	{#snippet actions()}
		{#if currencies.length > 1}
			<Segmented
				label={t('nav.currency')}
				value={currency}
				options={currencies.map((code) => ({ value: code, label: code }))}
				onchange={(code) => (picked = code)}
			/>
		{/if}
	{/snippet}

	{#if sides.length}
		<div class={cn('grid gap-6', sides.length > 1 && 'md:grid-cols-2')}>
			{#each sides as side (side.id)}
				<div class="min-w-0">
					<div class="mb-2 flex items-baseline justify-between gap-3 border-b border-line pb-1.5">
						<h3 class="text-small font-medium text-ink-secondary">{side.name}</h3>
						<Amount value={side.total} {currency} places={0} class="text-small text-ink" />
					</div>
					<ul class="space-y-0.5">
						{#each side.lines as line (line.name)}
							{@const active = selected === line.name}
							<li>
								<button
									type="button"
									aria-haspopup="dialog"
									aria-expanded={active}
									onclick={(event) => {
										if (line.key !== TRANSFERS) onselect(line, event.currentTarget);
									}}
									title={t(
										line.count === 1
											? 'transactions.categoryDetailsOneTitle'
											: 'transactions.categoryDetailsManyTitle',
										{ count: line.count }
									)}
									class={cn(
										'block w-full rounded-control px-2 py-1.5 text-left transition-colors',
										active ? 'bg-brand-soft' : 'hover:bg-sunken dark:hover:bg-raised'
									)}
								>
									<span class="flex items-center justify-between gap-3 text-small">
										<span class="flex min-w-0 items-baseline gap-2">
											<span class="truncate text-ink">{line.name}</span>
											<span class="shrink-0 text-caption text-ink-muted tabular-nums"
												>{line.count}×</span
											>
										</span>
										<span class="flex shrink-0 items-center gap-1.5">
											<Amount value={line.size} {currency} places={0} class="text-ink" />
											<ChevronRight
												class={cn('size-3.5', active ? 'text-brand' : 'text-ink-muted')}
												aria-hidden="true"
											/>
										</span>
									</span>
									<span class="mt-1 block h-1.5 rounded-full bg-sunken" aria-hidden="true">
										<span
											class={[
												'block h-full rounded-full',
												side.id === 'in' ? 'bg-gain' : 'bg-data-1'
											]}
											style:width="{Math.max(1.5, (approximate(line.size) / largest) * 100)}%"
										></span>
									</span>
								</button>
							</li>
						{/each}
					</ul>
				</div>
			{/each}
		</div>
	{:else if currency}
		{@const shownTransfers =
			transferCount || inCurrency.reduce((totalCount, line) => totalCount + line.count, 0)}
		<p class="py-4 text-small text-ink-secondary">
			{shownTransfers === 1
				? t('transactions.byCategoryTransfersOnlyOne', { currency })
				: t('transactions.byCategoryTransfersOnlyMany', {
						count: money(String(shownTransfers), 0),
						currency
					})}
		</p>
	{/if}
</Panel>
