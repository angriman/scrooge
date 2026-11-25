<!--
@component
Where the money that matches went, and where it came from: a bar for each top-level category
in one currency, as long as its share of the largest. A bar chosen cuts the list to it.
Amounts in several currencies are never added: each currency is shown on its own.
-->
<script lang="ts">
	import { approximate, negate } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import { TRANSFERS } from './filter.js';
	import type { CategoryTotal } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';

	interface Props {
		totals: CategoryTotal[];
		/** The category the list is cut to, if any: its bar is the one marked. */
		chosen: string | null;
		onchoose: (category: CategoryTotal) => void;
	}

	let { totals, chosen, onchoose }: Props = $props();

	// Categories shown on each side until every one is asked for; fewer on a narrow screen,
	// where the list comes after them.
	const SHOWN = 8;
	const NARROW = 4;

	// The currency with the most rows first: the one most of them are in.
	const currencies = $derived.by(() => {
		const rows = new Map<string, number>();
		for (const total of totals)
			rows.set(total.currency, (rows.get(total.currency) ?? 0) + total.count);
		return [...rows].sort((a, b) => b[1] - a[1]).map(([currency]) => currency);
	});
	let picked = $state<string | null>(null);
	const currency = $derived(picked && currencies.includes(picked) ? picked : currencies[0]);

	const mine = $derived(
		totals.filter((total) => total.currency === currency && total.key !== TRANSFERS)
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
		].filter((side) => side.lines.length)
	);
	const largest = $derived(
		Math.max(1, ...sides.flatMap((side) => side.lines.map((line) => approximate(line.size))))
	);
	let all = $state(false);
</script>

{#if sides.length}
	<Panel title={t('transactions.byCategoryTitle')}>
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
		<div class="space-y-5">
			{#each sides as side (side.id)}
				<div>
					<p class="mb-2 text-small text-ink-secondary">{side.name}</p>
					<ul class="space-y-0.5">
						{#each all ? side.lines : side.lines.slice(0, SHOWN) as line, index (line.name)}
							<li class={!all && index >= NARROW ? 'max-lg:hidden' : undefined}>
								<button
									type="button"
									onclick={() => onchoose(line)}
									title={t(
										line.count === 1
											? 'transactions.oneTransactionTitle'
											: 'transactions.manyTransactionsTitle',
										{ count: line.count }
									)}
									aria-pressed={chosen === line.name}
									class="block w-full rounded-control px-1.5 py-1.5 text-left hover:bg-sunken"
								>
									<span class="flex items-baseline justify-between gap-3 text-small">
										<span class="truncate text-ink">{line.name}</span>
										<Amount value={line.size} {currency} places={0} class="text-ink" />
									</span>
									<span class="mt-1 block h-1.5 rounded-full bg-sunken" aria-hidden="true">
										<span
											class={[
												'block h-full rounded-full',
												side.id === 'in' ? 'bg-gain' : 'bg-data-1',
												chosen && chosen !== line.name && 'opacity-40'
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
			{#if !all && sides.some((side) => side.lines.length > NARROW)}
				{@const wide = sides.some((side) => side.lines.length > SHOWN)}
				<button
					type="button"
					onclick={() => (all = true)}
					class={['text-small text-brand hover:text-brand-hover', !wide && 'lg:hidden']}
				>
					{t('transactions.showEveryCategory')}
				</button>
			{/if}
		</div>
	</Panel>
{/if}
