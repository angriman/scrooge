<!--
@component
Every staged row with the category it will be counted under: what a rule gave can be
changed here, and a row waiting can be given one of its own, apart from its counterparty's.
-->
<script lang="ts">
	import { dayOfYear } from '#lib/format/date.js';
	import type { Headed, StagedRow } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import { cn } from '#lib/ui/cn.js';
	import CategorySelect from './CategorySelect.svelte';

	interface Props {
		rows: StagedRow[];
		leaves: Headed[];
		/** The category chosen for a row on its own, by its id. */
		own: Record<string, string>;
	}

	let { rows, leaves, own = $bindable() }: Props = $props();
</script>

<ul class="divide-y divide-line">
	{#each rows as row (row.id)}
		<li
			class="grid grid-cols-[3rem_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 py-2 md:grid-cols-[3rem_minmax(0,1fr)_auto_14rem]"
		>
			<span class="text-small text-ink-muted tabular-nums">{dayOfYear(row.date)}</span>
			<span class="min-w-0">
				<span class="block truncate text-body text-ink"
					>{row.counterparty || row.description}</span
				>
				<span class="block truncate text-small text-ink-muted">
					{#if row.counterparty && row.description !== row.counterparty}{row.description},
					{/if}{row.account}
				</span>
			</span>
			<Amount
				value={row.amount}
				currency={row.currency}
				class={cn(
					'text-right text-body',
					row.amount.startsWith('-') ? 'text-ink' : 'text-gain'
				)}
			/>
			<span class="col-span-3 md:col-span-1">
				{#if row.choosable}
					<CategorySelect
						value={own[row.id] ?? ''}
						onchange={(value) => (own[row.id] = value)}
						{leaves}
						placeholder={row.category ?? 'Waiting for a category'}
						label="Category of {row.counterparty || row.description} on {dayOfYear(
							row.date
						)}"
						class="w-full"
					/>
				{:else}
					<span class="block truncate px-2 text-small text-ink-secondary"
						>{row.category}</span
					>
				{/if}
			</span>
		</li>
	{/each}
</ul>
