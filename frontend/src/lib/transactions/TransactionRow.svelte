<!--
@component
One transaction: who was paid or paid, what for, from which account, its category and how
much. A category that is wrong can be changed where it stands, and remembered as a rule so
that the next import gives it again.
-->
<script lang="ts">
	import Repeat from '@lucide/svelte/icons/repeat';
	import { kindName } from './filter.js';
	import type { Headed, Transaction } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import { cn } from '#lib/ui/cn.js';

	interface Props {
		row: Transaction;
		/** The categories a row can be given, under their headings. */
		leaves: Headed[];
		/** Whether to say whose the account is: the household is shown. */
		people: boolean;
		/** Whether a category chosen can be remembered as a rule. */
		rules: boolean;
		/** Whether anything can be changed here: not over a copy. */
		changeable: boolean;
		onchange: (row: Transaction, category: string, remember: boolean) => Promise<void>;
	}

	let { row, leaves, people, rules, changeable, onchange }: Props = $props();

	let editing = $state(false);
	let category = $state('');
	let remember = $state(false);
	let saving = $state(false);
	let problem = $state<string | null>(null);

	const who = $derived(row.counterparty || row.description);
	const what = $derived(
		row.counterparty && row.description !== row.counterparty ? row.description : ''
	);
	const tone = $derived(
		row.kind === 'internal' || row.kind === 'transfer'
			? 'text-ink-secondary'
			: row.amount.startsWith('-')
				? 'text-ink'
				: 'text-gain'
	);

	function edit() {
		category = row.category ?? '';
		remember = false;
		problem = null;
		editing = true;
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		if (category === row.category) {
			editing = false;
			return;
		}
		saving = true;
		problem = null;
		try {
			await onchange(row, category, remember);
			editing = false;
		} catch (error) {
			problem = error instanceof Error ? error.message : String(error);
		} finally {
			saving = false;
		}
	}
</script>

<li class="border-t border-line first:border-t-0">
	<div
		class="grid grid-cols-[1fr_auto] items-start gap-x-4 gap-y-1 px-4 py-2.5 md:grid-cols-[minmax(0,1fr)_12rem_auto]"
	>
		<div class="min-w-0">
			<p class="flex items-center gap-1.5 text-body text-ink">
				<span class="truncate">{who}</span>
				{#if row.recurring}
					<span
						class="inline-flex shrink-0 items-center gap-1 rounded-full bg-sunken px-1.5 py-px text-caption text-ink-secondary"
						title="Comes back every {row.recurring === 'monthly' ? 'month' : 'year'}"
					>
						<Repeat class="size-3" aria-hidden="true" />
						{row.recurring === 'monthly' ? 'Monthly' : 'Yearly'}
					</span>
				{/if}
			</p>
			{#if what}<p class="truncate text-small text-ink-secondary">{what}</p>{/if}
		</div>

		<div class="order-last col-span-2 md:order-none md:col-span-1">
			{#if row.changeable && changeable && !editing}
				<button
					type="button"
					onclick={edit}
					title="Change the category"
					aria-label="{row.category}: change the category"
					class="max-w-full truncate rounded-full border border-dashed border-line-strong px-2 py-0.5 text-small text-ink-secondary hover:border-brand hover:text-ink"
				>
					{row.category}
				</button>
			{:else if !editing}
				<span class="block truncate px-2 py-0.5 text-small text-ink-muted">
					{row.category ?? kindName(row.kind)}
				</span>
			{/if}
		</div>

		<div class="text-right">
			<p class={cn('text-body', tone)}>
				<Amount value={row.amount} currency={row.currency} />
			</p>
			<p class="text-caption whitespace-nowrap text-ink-muted">
				{row.account}{#if people}, {row.owner}{/if}
			</p>
		</div>
	</div>

	{#if editing}
		<form
			onsubmit={save}
			class="mx-4 mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-control bg-sunken px-3 py-2.5"
		>
			<label class="flex min-w-0 items-center gap-2 text-small text-ink-secondary">
				Category
				<!-- svelte-ignore a11y_autofocus -->
				<select
					bind:value={category}
					autofocus
					class="h-8 max-w-56 rounded-control border border-line bg-raised px-2 text-small text-ink focus:border-brand focus:outline-none"
				>
					{#each leaves as group (group.heading)}
						<optgroup label={group.heading}>
							{#each group.names as name (name)}
								<option value={name}>{name}</option>
							{/each}
						</optgroup>
					{/each}
				</select>
			</label>
			{#if rules && row.counterparty}
				<label class="flex items-center gap-2 text-small text-ink">
					<input type="checkbox" bind:checked={remember} class="size-4 accent-brand" />
					Remember for {row.counterparty}
				</label>
			{/if}
			<span class="ml-auto flex gap-2">
				<button
					type="button"
					onclick={() => (editing = false)}
					class="h-8 rounded-control px-3 text-small text-ink-secondary hover:text-ink"
				>
					Cancel
				</button>
				<button
					type="submit"
					disabled={saving}
					class="h-8 rounded-control bg-brand px-3 text-small font-medium text-on-brand hover:bg-brand-hover disabled:opacity-60"
				>
					{saving ? 'Changing…' : 'Change category'}
				</button>
			</span>
			{#if problem}
				<p class="w-full text-small text-loss" role="alert">{problem}</p>
			{/if}
		</form>
	{/if}
</li>
