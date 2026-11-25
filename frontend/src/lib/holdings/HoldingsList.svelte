<!--
@component
Every instrument held, every account together: its value and share of securities, and what it
gained since it was bought. Sorted by value, by gain, by return or by the return over the past
year. A row opens to its positions, account by account, with the quantity, the price it is
valued at (and when that price is from, when it is not the last day), what it cost and what it
gained, in the instrument's currency; then what it returned over each range (`HoldingReturns`).
-->
<script lang="ts">
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import { assetColor, assetName } from '#lib/design/data.js';
	import { day } from '#lib/format/date.js';
	import { exact, money, percent } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { HeldInstrument, Position } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Change from '#lib/ui/Change.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import Share from '#lib/ui/Share.svelte';
	import Swatch from '#lib/ui/Swatch.svelte';
	import { cn } from '#lib/ui/cn.js';
	import HoldingReturns from './HoldingReturns.svelte';
	import LogoControl from './LogoControl.svelte';
	import { sortHoldings, sortOptions, yearOf, type Sort } from './returns.js';

	interface Props {
		holdings: HeldInstrument[];
		logos: Record<string, string>;
		currency: string;
		/** The last day: a price from another day says so. */
		lastDay: string;
		/** Whether several people's positions are listed: each says whose it is. */
		people: boolean;
		/** The holding opened, by its key; bindable, so the map can open one. */
		open?: string | null;
		/** The token a change of a logo needs; null where logos cannot be changed. */
		token?: string | null;
		/** Called once a logo is changed. */
		onlogo?: () => void | Promise<void>;
	}

	let {
		holdings,
		logos,
		currency,
		lastDay,
		people,
		open = $bindable(null),
		token = null,
		onlogo = () => {}
	}: Props = $props();

	let sort = $state<Sort>('value');

	const keyOf = (holding: HeldInstrument) =>
		`${holding.symbol}|${holding.currency}|${holding.asset}`;
	const sorted = $derived(sortHoldings(holdings, sort));
	const withLogos = $derived(holdings.some((holding) => logos[holding.symbol]));

	const priced = (position: Position) => {
		if (position.price === null) return '–';
		return exact(position.price);
	};
	const priceNote = (position: Position) =>
		position.price_is_trade
			? t('holdings.lastTradePrice')
			: position.price_day && position.price_day !== lastDay
				? t('holdings.closeOf', { date: day(position.price_day) })
				: '';
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

	<ul class="rounded-panel bg-panel">
		{#each sorted as holding (keyOf(holding))}
			{@const key = keyOf(holding)}
			{@const expanded = open === key}
			{@const color = assetColor(holding.asset)}
			<li id="holding-{key}" class="scroll-mt-20 border-t border-line first:border-t-0">
				<button
					type="button"
					aria-expanded={expanded}
					onclick={() => (open = expanded ? null : key)}
					class="grid w-full grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-x-4 px-4 py-3 text-left hover:bg-raised sm:grid-cols-[minmax(0,1fr)_7rem_9rem_8rem_auto]"
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
							<span class="block truncate font-medium text-ink"
								>{holding.name || holding.symbol}</span
							>
							<span class="flex items-center gap-1.5 text-small text-ink-muted">
								<Swatch {color} class="size-2 rounded-full" />
								<span class="text-ink-secondary">{assetName(holding.asset)}</span>
								{#if holding.name}<span class="truncate">{holding.symbol}</span>{/if}
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
					<ChevronDown
						class={cn('size-4 text-ink-muted transition-transform', expanded && 'rotate-180')}
						aria-hidden="true"
					/>
				</button>

				{#if expanded}
					<div class="overflow-x-auto px-4 pb-4">
						{#if holding.gain !== null}
							<p class="mb-2 text-small sm:hidden">
								<Change
									amount={holding.gain}
									percent={holding.return_percent}
									label={t('holdings.sinceBoughtLower')}
								/>
							</p>
						{/if}
						<table class="w-full text-small">
							<thead class="text-caption text-ink-muted">
								<tr>
									<th class="pb-2 text-left font-normal">{t('investments.colAccount')}</th>
									<th class="pb-2 text-right font-normal">{t('holdings.colQuantity')}</th>
									<th class="pb-2 text-right font-normal">{t('holdings.colPrice')}</th>
									<th class="pb-2 text-right font-normal">{t('holdings.colValue')}</th>
									<th class="pb-2 text-right font-normal">{t('holdings.colCost')}</th>
									<th class="pb-2 text-right font-normal">{t('investments.sinceBought')}</th>
								</tr>
							</thead>
							<tbody>
								{#each holding.positions as position (position.owner + position.account)}
									<tr class="border-t border-line">
										<td class="py-2 whitespace-nowrap">
											{position.account}{#if people}<span class="text-ink-muted"
													>, {position.owner}</span
												>{/if}
										</td>
										<td class="py-2 text-right tabular-nums">{exact(position.quantity)}</td>
										<td class="py-2 text-right whitespace-nowrap tabular-nums">
											{priced(position)}
											{#if priceNote(position)}<span class="block text-caption text-ink-muted"
													>{priceNote(position)}</span
												>{/if}
										</td>
										<td class="py-2 text-right whitespace-nowrap tabular-nums">
											{money(position.value_native)}
											<span class="text-caption text-ink-muted">{holding.currency}</span>
										</td>
										<td class="py-2 text-right whitespace-nowrap tabular-nums">
											{position.cost_native === null
												? t('common.notKnown')
												: money(position.cost_native)}
										</td>
										<td class="py-2 text-right whitespace-nowrap">
											<Change amount={position.gain_native} percent={position.gain_percent} />
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
						<HoldingReturns {holding} {currency} {people} />
						{#if token && holding.asset !== 'Bills'}
							<LogoControl
								symbol={holding.symbol}
								has={Boolean(logos[holding.symbol])}
								{token}
								onchange={onlogo}
							/>
						{/if}
					</div>
				{/if}
			</li>
		{/each}
	</ul>
</section>
