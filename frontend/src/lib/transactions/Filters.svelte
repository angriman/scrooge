<!--
@component
What the transactions are cut to: a search of their counterparty and description, and a chip
for each other field. Every change goes in the address, so a filter can be kept as a link.
-->
<script lang="ts">
	import Check from '@lucide/svelte/icons/check';
	import Search from '@lucide/svelte/icons/search';
	import type { Headed, KnownMerchants } from '#lib/types.js';
	import { cn } from '#lib/ui/cn.js';
	import FilterChip from './FilterChip.svelte';
	import MerchantPicker from './MerchantPicker.svelte';
	import { ACCOUNT_KINDS, accountsName, kindName, periodName, periods } from './filter.js';

	type Changes = Record<string, string | null>;

	interface Props {
		/** The fields as the address has them; an empty one is not set. */
		values: Record<string, string>;
		accounts: string[];
		categories: Headed[];
		kinds: string[];
		today: string;
		/** The merchants the Merchant chip offers, loaded as it opens. */
		merchants: () => Promise<KnownMerchants>;
		/** `typing`: the search changed as it is typed, and the address need not keep each step. */
		onchange: (changes: Changes, typing?: boolean) => void;
	}

	let { values, accounts, categories, kinds, today, merchants, onchange }: Props = $props();

	// The search as it is typed: the address follows once typing stops for a moment.
	let text = $state('');
	let waiting: ReturnType<typeof setTimeout> | undefined;
	$effect.pre(() => {
		if (document.activeElement?.id !== 'search') text = values.text ?? '';
	});
	$effect(() => () => clearTimeout(waiting));
	function typed() {
		clearTimeout(waiting);
		waiting = setTimeout(() => onchange({ text: text.trim() || null }, true), 250);
	}

	// What a chip is set to, until it is applied: a period of its own, the bounds of an amount.
	let from = $state('');
	let to = $state('');
	let least = $state('');
	let most = $state('');
	let periodOpen = $state(false);
	let amountOpen = $state(false);
	let unreadable = $state(false);
	// An amount as the API reads it: digits, a point for the cents, apostrophes or commas between
	// thousands.
	const readable = (text: string) => !text || /^\d[\d'’,]*(\.\d+)?$/.test(text);
	$effect(() => {
		if (periodOpen) [from, to] = [values.from ?? '', values.to ?? ''];
	});
	$effect(() => {
		if (amountOpen) {
			[least, most] = [values.min ?? '', values.max ?? ''];
			unreadable = false;
		}
	});

	const period = $derived(periodName(values.from ?? '', values.to ?? '', today));
	const amount = $derived.by(() => {
		const [low, high] = [values.min, values.max];
		if (low && high) return `${low} to ${high}`;
		if (low) return `At least ${low}`;
		return high ? `Up to ${high}` : null;
	});

	const option =
		'flex w-full items-center justify-between gap-3 rounded-control px-2 py-1.5 text-left text-small text-ink hover:bg-brand-soft';
	const field =
		'h-8 w-full rounded-control border border-line bg-sunken px-2 text-small text-ink placeholder:text-ink-muted focus:border-brand focus:outline-none';
	const apply =
		'h-8 rounded-control bg-brand px-3 text-small font-medium text-on-brand hover:bg-brand-hover';
</script>

{#snippet list(
	names: readonly { value: string; label: string }[],
	current: string | undefined,
	name: string,
	close: () => void
)}
	{#each names as item (item.value)}
		<button
			type="button"
			class={option}
			aria-pressed={current === item.value}
			onclick={() => {
				onchange({ [name]: item.value });
				close();
			}}
		>
			{item.label}
			{#if current === item.value}<Check class="size-3.5 text-brand" aria-hidden="true" />{/if}
		</button>
	{/each}
{/snippet}

<div class="space-y-3">
	<label class="relative block">
		<span class="sr-only">Search transactions</span>
		<Search
			class="pointer-events-none absolute top-1/2 left-4 size-5 -translate-y-1/2 text-ink-muted"
			aria-hidden="true"
		/>
		<input
			id="search"
			type="search"
			bind:value={text}
			oninput={typed}
			onkeydown={(event) => {
				if (event.key === 'Enter') {
					clearTimeout(waiting);
					onchange({ text: text.trim() || null });
				}
			}}
			placeholder="Search by who was paid or what for"
			autocomplete="off"
			spellcheck="false"
			class="h-13 w-full rounded-lead border border-line bg-panel pr-4 pl-12 text-title text-ink placeholder:text-ink-muted hover:border-line-strong focus:border-brand focus:outline-none"
		/>
	</label>

	<div class="flex flex-wrap items-center gap-2" role="group" aria-label="Filters">
		<FilterChip
			label="Period"
			value={period && (values.booking ? `${period}, by booking date` : period)}
			onclear={() => onchange({ from: null, to: null, booking: null })}
			bind:open={periodOpen}
		>
			{#snippet children(close)}
				{#each periods(today) as offered (offered.label)}
					{@const chosen = values.from === offered.from && values.to === offered.to}
					<button
						type="button"
						class={option}
						aria-pressed={chosen}
						onclick={() => {
							onchange({ from: offered.from, to: offered.to });
							close();
						}}
					>
						{offered.label}
						{#if chosen}<Check class="size-3.5 text-brand" aria-hidden="true" />{/if}
					</button>
				{/each}
				<form
					class="mt-2 space-y-2 border-t border-line px-2 pt-3 pb-1"
					onsubmit={(event) => {
						event.preventDefault();
						onchange({ from: from || null, to: to || null });
						close();
					}}
				>
					<div class="grid grid-cols-2 gap-2">
						<label class="text-caption text-ink-secondary">
							From
							<input
								type="date"
								bind:value={from}
								max={to || undefined}
								class={cn(field, 'mt-1')}
							/>
						</label>
						<label class="text-caption text-ink-secondary">
							To
							<input
								type="date"
								bind:value={to}
								min={from || undefined}
								class={cn(field, 'mt-1')}
							/>
						</label>
					</div>
					<label class="flex items-center gap-2 py-1 text-small text-ink">
						<input
							type="checkbox"
							checked={Boolean(values.booking)}
							onchange={(event) => onchange({ booking: event.currentTarget.checked ? '1' : null })}
							class="size-4 accent-brand"
						/>
						By booking date, as statements count it
					</label>
					<button type="submit" class={apply}>Show this period</button>
				</form>
			{/snippet}
		</FilterChip>

		{#if accounts.length > 1}
			<FilterChip
				label="Account"
				value={values.account || null}
				onclear={() => onchange({ account: null })}
			>
				{#snippet children(close)}
					{@render list(
						accounts.map((name) => ({ value: name, label: name })),
						values.account,
						'account',
						close
					)}
				{/snippet}
			</FilterChip>
		{/if}

		<FilterChip
			label="Kind of account"
			value={values.accounts ? accountsName(values.accounts) : null}
			onclear={() => onchange({ accounts: null })}
		>
			{#snippet children(close)}
				{@render list(ACCOUNT_KINDS, values.accounts, 'accounts', close)}
			{/snippet}
		</FilterChip>

		<FilterChip
			label="Category"
			value={values.category || null}
			onclear={() => onchange({ category: null })}
		>
			{#snippet children(close)}
				{#each categories as group (group.heading)}
					<p class="px-2 pt-2 pb-1 text-caption text-ink-muted">{group.heading}</p>
					{@render list(
						group.names.map((name) => ({ value: name, label: name })),
						values.category,
						'category',
						close
					)}
				{/each}
			{/snippet}
		</FilterChip>

		<FilterChip
			label="Merchant"
			value={values.merchant || null}
			onclear={() => onchange({ merchant: null })}
		>
			{#snippet children(close)}
				<MerchantPicker
					current={values.merchant ?? ''}
					load={merchants}
					onpick={(name) => {
						onchange({ merchant: name });
						close();
					}}
				/>
			{/snippet}
		</FilterChip>

		<FilterChip
			label="Kind"
			value={values.kind ? kindName(values.kind) : null}
			onclear={() => onchange({ kind: null })}
		>
			{#snippet children(close)}
				{@render list(
					kinds.map((kind) => ({ value: kind, label: kindName(kind) })),
					values.kind,
					'kind',
					close
				)}
			{/snippet}
		</FilterChip>

		<FilterChip
			label="Amount"
			value={amount}
			onclear={() => onchange({ min: null, max: null })}
			bind:open={amountOpen}
		>
			{#snippet children(close)}
				<form
					class="space-y-2 p-1"
					onsubmit={(event) => {
						event.preventDefault();
						const [low, high] = [
							least.trim().replaceAll('’', "'"),
							most.trim().replaceAll('’', "'")
						];
						unreadable = !readable(low) || !readable(high);
						if (unreadable) return;
						onchange({ min: low || null, max: high || null });
						close();
					}}
				>
					<p class="text-caption text-ink-secondary">In or out, whatever the sign.</p>
					<div class="grid grid-cols-2 gap-2">
						<label class="text-caption text-ink-secondary">
							At least
							<input
								inputmode="decimal"
								bind:value={least}
								placeholder="0"
								class={cn(field, 'mt-1 tabular-nums')}
							/>
						</label>
						<label class="text-caption text-ink-secondary">
							Up to
							<input
								inputmode="decimal"
								bind:value={most}
								placeholder="Any"
								class={cn(field, 'mt-1 tabular-nums')}
							/>
						</label>
					</div>
					{#if unreadable}
						<p class="text-caption text-loss" role="alert">Write an amount like 1250 or 1250.50.</p>
					{/if}
					<button type="submit" class={apply}>Show these amounts</button>
				</form>
			{/snippet}
		</FilterChip>
	</div>
</div>
