<!--
@component
The rows that wait for a category, by counterparty and direction: one choice gives it to all
of them, and, where rules are kept, to the counterparty's rows of every import to come.
-->
<script lang="ts">
	import ArrowDownLeft from '@lucide/svelte/icons/arrow-down-left';
	import ArrowUpRight from '@lucide/svelte/icons/arrow-up-right';
	import { dayOfYear } from '#lib/format/date.js';
	import type { Headed, Staged, StagedRow } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import CategorySelect from './CategorySelect.svelte';
	import { groupKey as key, type GroupChoice } from './choices.js';

	interface Props {
		groups: Staged['groups'];
		rows: Map<string, StagedRow>;
		leaves: Headed[];
		rules: boolean;
		/** The choice made for each group, by its key (`groupKey`). */
		chosen: Record<string, GroupChoice>;
	}

	let { groups, rows, leaves, rules, chosen = $bindable() }: Props = $props();

	const SHOWN = 3;
	let open = $state<Record<string, boolean>>({});
	const left = $derived(groups.filter((group) => !chosen[key(group)]?.category).length);
</script>

<Panel
	title="Need a category"
	note={left
		? `${left} of ${groups.length} still to choose. A choice is kept for every row of its counterparty.`
		: 'Every one has a category. Save them, or commit.'}
>
	<ul class="divide-y divide-line">
		{#each groups as group (key(group))}
			{@const members = group.ids.map((id) => rows.get(id)).filter((row) => row !== undefined)}
			{@const shown = open[key(group)] ? members : members.slice(0, SHOWN)}
			{@const choice = chosen[key(group)]}
			<li class="grid gap-x-6 gap-y-2 py-3 md:grid-cols-[minmax(0,1fr)_18rem]">
				<div class="min-w-0">
					<p class="flex items-center gap-1.5 text-body text-ink">
						{#if group.incoming}
							<ArrowDownLeft class="size-4 shrink-0 text-gain" aria-label="Received" />
						{:else}
							<ArrowUpRight class="size-4 shrink-0 text-ink-muted" aria-label="Paid" />
						{/if}
						<span class="truncate">{group.name || 'Rows without a description'}</span>
						<span class="shrink-0 text-small text-ink-muted">
							{members.length === 1 ? '1 row' : `${members.length} rows`}
						</span>
					</p>
					<ul class="mt-1 space-y-0.5 pl-5.5">
						{#each shown as row (row.id)}
							<li class="flex items-baseline gap-3 text-small text-ink-secondary">
								<span class="w-12 shrink-0 tabular-nums">{dayOfYear(row.date)}</span>
								<span class="min-w-0 flex-1 truncate">
									{row.description !== group.name ? row.description : row.account}
								</span>
								<Amount value={row.amount} currency={row.currency} class="text-ink" />
							</li>
						{/each}
					</ul>
					{#if members.length > SHOWN && !open[key(group)]}
						<button
							type="button"
							onclick={() => (open[key(group)] = true)}
							class="mt-1 pl-5.5 text-small text-brand hover:text-brand-hover"
						>
							Show all {members.length}
						</button>
					{/if}
				</div>
				{#if choice}<div class="flex flex-col gap-1.5 pl-5.5 md:pl-0">
						<CategorySelect
							bind:value={choice.category}
							{leaves}
							label="Category for {group.name || 'rows without a description'}"
						/>
						{#if rules && group.name}
							<label class="flex items-center gap-2 text-small text-ink-secondary">
								<input type="checkbox" bind:checked={choice.rule} class="size-4 accent-brand" />
								Remember for {group.name}
							</label>
						{/if}
					</div>{/if}
			</li>
		{/each}
	</ul>
</Panel>
