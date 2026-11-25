<!--
@component
What the Merchant chip offers: the merchants money was spent at, loaded as the chip opens, the
largest over the last twelve months first, narrowed as a name is typed. Arrows move through
them, Enter picks one. What is typed can be picked as it is, for a name not listed.
-->
<script lang="ts">
	import Check from '@lucide/svelte/icons/check';
	import Search from '@lucide/svelte/icons/search';
	import { Command } from 'bits-ui';
	import { untrack } from 'svelte';
	import { month } from '#lib/format/date.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { KnownMerchants } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import { narrow, offered, typedName } from './merchants.js';

	interface Props {
		/** The merchant the transactions are cut to; empty when none. */
		current: string;
		/** The merchants of the owner and currency shown, loaded once for each. */
		load: () => Promise<KnownMerchants>;
		onpick: (name: string) => void;
	}

	let { current, load, onpick }: Props = $props();

	let typed = $state('');
	let known = $state<KnownMerchants | null>(null);
	let unloaded = $state(false);
	// Loaded as the chip opens, once: what the address changes to while it is open is not.
	$effect(() => {
		let gone = false;
		untrack(load).then(
			(sent) => {
				if (!gone) known = sent;
			},
			() => {
				if (!gone) unloaded = true;
			}
		);
		return () => (gone = true);
	});

	const merchants = $derived(offered(known));
	const shown = $derived(narrow(merchants, typed));
	const own = $derived(typedName(merchants, typed));
	const OWN = '\u0000typed'; // the value of the typed name's item: no merchant is named so

	// The first item is the one Enter picks while typing.
	let highlighted = $state('');
	$effect(() => {
		highlighted = shown[0]?.name ?? (own === null ? '' : OWN);
	});

	const item =
		'flex w-full cursor-default items-baseline gap-3 rounded-control px-2 py-1.5 text-left text-small text-ink outline-none data-selected:bg-brand-soft';
</script>

<Command.Root
	shouldFilter={false}
	loop
	bind:value={highlighted}
	label={t('transactions.merchantsListAria')}
	class="flex flex-col"
>
	<label class="relative block">
		<span class="sr-only">{t('transactions.merchantsSearchSr')}</span>
		<Search
			class="pointer-events-none absolute top-1/2 left-2 size-4 -translate-y-1/2 text-ink-muted"
			aria-hidden="true"
		/>
		<Command.Input
			bind:value={typed}
			placeholder={t('transactions.merchantsSearchPlaceholder')}
			autocomplete="off"
			spellcheck="false"
			class="h-8 w-full rounded-control border border-line bg-sunken pr-2 pl-8 text-small text-ink placeholder:text-ink-muted focus:border-brand focus:outline-none"
		/>
	</label>
	{#if known?.since}
		<p class="mt-2 flex justify-between px-2 text-caption text-ink-muted">
			<span>{t('transactions.merchantsSpentSince', { month: month(known.since) })}</span>
			<span>{known.currency}</span>
		</p>
	{/if}
	<Command.List class="mt-1 max-h-72 overflow-y-auto">
		<Command.Viewport>
			{#each shown as merchant (merchant.name)}
				<Command.Item value={merchant.name} onSelect={() => onpick(merchant.name)} class={item}>
					<span class="min-w-0 flex-1 truncate">{merchant.name}</span>
					{#if merchant.count && merchant.spent}
						<span class="text-caption text-ink-muted">{merchant.count}×</span>
						<Amount value={merchant.spent} places={0} />
					{:else if merchant.last}
						<span class="text-caption text-ink-muted"
							>{t('transactions.merchantLastMonth', { month: month(merchant.last) })}</span
						>
					{/if}
					{#if merchant.name.toLowerCase() === current.toLowerCase()}
						<Check class="size-3.5 shrink-0 self-center text-brand" aria-hidden="true" />
						<span class="sr-only">{t('transactions.chosenSr')}</span>
					{/if}
				</Command.Item>
			{/each}
			{#if own !== null}
				<Command.Item value={OWN} onSelect={() => onpick(own ?? '')} class={item}>
					<span class="min-w-0 flex-1 truncate"
						>{t('transactions.merchantShowTyped', { name: own })}</span
					>
					<span class="text-caption text-ink-muted">{t('transactions.merchantAsTyped')}</span>
				</Command.Item>
			{/if}
		</Command.Viewport>
	</Command.List>
	{#if unloaded}
		<p class="px-2 py-1.5 text-caption text-ink-secondary" role="status">
			{t('transactions.merchantsLoadError')}
		</p>
	{:else if known === null}
		<p class="px-2 py-1.5 text-caption text-ink-muted" role="status">
			{t('transactions.merchantsLoading')}
		</p>
	{:else}
		{#each known.errors as error (error)}
			<p class="px-2 py-1.5 text-caption text-ink-secondary" role="status">{error}</p>
		{/each}
		{#if !shown.length && own === null && !known.errors.length}
			<p class="px-2 py-1.5 text-caption text-ink-muted" role="status">
				{t('transactions.merchantsEmpty')}
			</p>
		{/if}
	{/if}
	<p class="mt-1 border-t border-line px-2 pt-2 text-caption text-ink-muted">
		{t('transactions.merchantsFootnote')}
	</p>
</Command.Root>
