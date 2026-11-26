<!--
@component
Every instrument held, every account together: its value and share of securities, and what it
gained since it was bought. Sorted by value, by gain, by return or by the return over the past
year. Choosing a row opens its details in the side panel.
-->
<script lang="ts">
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import { assetColor, assetName } from '#lib/design/data.js';
	import { percent } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { HeldInstrument } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Change from '#lib/ui/Change.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import Share from '#lib/ui/Share.svelte';
	import Swatch from '#lib/ui/Swatch.svelte';
	import { cn } from '#lib/ui/cn.js';
	import { holdingName } from './names.js';
	import { holdingKey, sortHoldings, sortOptions, yearOf, type Sort } from './returns.js';

	interface Props {
		holdings: HeldInstrument[];
		logos: Record<string, string>;
		currency: string;
		/** Key of the holding currently shown in the side panel, if any. */
		selected?: string | null;
		/** Called when a holding row is chosen. */
		onchoose: (holding: HeldInstrument) => void;
	}

	let { holdings, logos, currency, selected = null, onchoose }: Props = $props();

	let sort = $state<Sort>('value');

	const sorted = $derived(sortHoldings(holdings, sort));
	const withLogos = $derived(holdings.some((holding) => logos[holding.symbol]));
	const sorts = $derived(sortOptions());
</script>

<section class="mt-10" aria-labelledby="holdings-title">
	<header class="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
		<h2 id="holdings-title" class="text-heading font-medium text-ink">
			{holdings.length === 1
				? t('holdings.countOne')
				: t('holdings.countMany', { count: holdings.length })}
		</h2>
		<Segmented
			label={t('holdings.sortByAria')}
			value={sort}
			options={sorts}
			onchange={(chosen) => (sort = chosen)}
		/>
	</header>

	<ul class="overflow-hidden rounded-panel bg-panel">
		{#each sorted as holding (holdingKey(holding))}
			{@const key = holdingKey(holding)}
			{@const active = selected === key}
			{@const color = assetColor(holding.asset)}
			{@const title = holdingName(holding)}
			<li id="holding-{key}" class="scroll-mt-20 border-t border-line first:border-t-0">
				<button
					type="button"
					aria-haspopup="dialog"
					aria-expanded={active}
					onclick={() => onchoose(holding)}
					class={cn(
						'grid w-full grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-x-4 px-4 py-3 text-left sm:grid-cols-[minmax(0,1fr)_7rem_9rem_8rem_auto]',
						active ? 'bg-brand-soft' : 'hover:bg-raised'
					)}
				>
					<span class="flex min-w-0 items-center gap-3">
						{#if withLogos}
							{#if logos[holding.symbol]}
								<img
									src={logos[holding.symbol]}
									alt=""
									class="size-7 shrink-0 rounded-control bg-ink object-contain p-0.5"
								/>
							{:else}
								<span class="size-7 shrink-0" aria-hidden="true"></span>
							{/if}
						{/if}
						<span class="min-w-0">
							<span class="block truncate font-medium text-ink">{title}</span>
							<span class="flex items-center gap-1.5 text-small text-ink-muted">
								<Swatch {color} class="size-2 rounded-full" />
								<span class="text-ink-secondary">{assetName(holding.asset)}</span>
								{#if title !== holding.symbol}<span class="truncate">{holding.symbol}</span>{/if}
								{#if holding.positions.length > 1}<span class="shrink-0"
										>{t('holdings.inAccounts', { count: holding.positions.length })}</span
									>{/if}
							</span>
						</span>
					</span>
					<span class="hidden items-center gap-2 sm:flex">
						<Share share={holding.share} {color} class="w-14" />
						<span class="text-small text-ink-secondary tabular-nums">{percent(holding.share)}</span>
					</span>
					<span class="text-right">
						<Amount value={holding.value} {currency} places={0} />
					</span>
					<span class="hidden text-right sm:block">
						{#if sort === 'year'}
							{@const year = yearOf(holding.returns)}
							{#if year === null}
								<span class="text-small text-ink-muted">{t('holdings.notHeldAllYear')}</span>
							{:else}
								<Change
									amount={year.amount}
									percent={year.percent}
									label={t('range.1y')}
									class="text-small"
								/>
							{/if}
						{:else if holding.gain === null}
							<span class="text-small text-ink-muted">{t('holdings.costNotKnown')}</span>
						{:else}
							<Change amount={holding.gain} percent={holding.return_percent} class="text-small" />
						{/if}
					</span>
					<ChevronRight
						class={cn('size-4', active ? 'text-brand' : 'text-ink-muted')}
						aria-hidden="true"
					/>
				</button>
			</li>
		{/each}
	</ul>
</section>
